// The 0.9.8 sandbox gate (plan s4-hard-switch page 2): page 2's frozen cases, run against one comms commit and one
// aify-env commit, each scenario in a fresh pair of containers on an internal network of its own. Built on the
// env-against-comms runner (../env-against-comms.mjs): its ownership, bounded commands, key redaction and cleanup
// readback hold here unchanged.
//
//   node scripts/sandbox/gate/gate.mjs --comms=<ref> --env=<ref> --attempt=<label> [--scenarios=a,b] [--out=<dir>]
//   node scripts/sandbox/gate/gate.mjs --self-check        every judge against its passing model and countermodels
//
// One attempt. The pass rule (three attempts per pinned pair, every attempt kept) is SW3b's; this records one attempt's
// verdicts and evidence under --out, and exits 0 only when every case judged passes and cleanup read back complete.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { KEY, bytesSha, cleanup, create, docker, log, must, outcome, redact, run, setPrimaryUntil, sha } from "../env-against-comms.mjs";
import { AttemptDirTaken, acquireAttemptDir } from "./attempt-dir.mjs";
import { judgeCase } from "./case-judging.mjs";
import { CASES, OBSERVATIONS, SCENARIOS, START } from "./cases.mjs";
import { JUDGES } from "./judges.mjs";
import { Pair } from "./pair.mjs";
import { selfCheck } from "./self-check.mjs";

const SELF = fileURLToPath(import.meta.url);
const HERE = path.dirname(SELF);
const SANDBOX = path.join(HERE, "..");
const REPO = path.join(SANDBOX, "..", "..");
const ENV_REPO = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const BUILD_MS = 20 * 60_000;
// The runner's 30-minute deadline covers one short run; an attempt is about 35 minutes of scenarios plus two builds.
const ATTEMPT_MS = 120 * 60_000;
const SOURCES = ["gate.mjs", "pair.mjs", "cases.mjs", "case-judging.mjs", "attempt-dir.mjs", "judges.mjs",
  "self-check.mjs", "stand-in.mjs", "claude-aify.sh", "probe.py", "relay.mjs", "env.Dockerfile"].map((name) => path.join(HERE, name))
  .concat([path.join(SANDBOX, "env-against-comms.mjs"), path.join(SANDBOX, "sandbox-entry.sh")]);

/** The env build context: the archive, the gate's Dockerfile, the runner's entry script and the stand-in. */
function envContext(workDir, envSha) {
  must(run("git", ["-C", ENV_REPO, "archive", "--format=tar", "-o", path.join(workDir, "env.tar"), envSha]), "git archive env");
  fs.mkdirSync(path.join(workDir, "env"));
  must(run("tar", ["-xf", "env.tar", "-C", "env"], { cwd: workDir }), "tar");
  const copy = (from, to) => fs.writeFileSync(path.join(workDir, "env", to), fs.readFileSync(from, "utf8").replace(/\r\n/g, "\n"));
  copy(path.join(HERE, "env.Dockerfile"), "Dockerfile");
  copy(path.join(SANDBOX, "sandbox-entry.sh"), "sandbox-entry.sh");
  copy(path.join(HERE, "stand-in.mjs"), "gate-stand-in.mjs");
  copy(path.join(HERE, "claude-aify.sh"), "gate-claude-aify.sh");
  copy(path.join(HERE, "relay.mjs"), "gate-relay.mjs");
  return path.join(workDir, "env");
}

/**
 * One attempt, its exit and its lines. The attempt budget is the runner's primary window: no command starts after it,
 * one running at it is cut there, and a late answer reads as timed out. Cleanup is outside it, and the verdict is
 * refused when the budget passed before it was reached. `dock` and `budgetMs` are for the gate's own tests.
 */
export async function attempt({ commsRef, envRef, label, only, outDir, dock = docker, budgetMs = ATTEMPT_MS }) {
  const until = Date.now() + budgetMs;
  const deadline = () => { if (Date.now() > until) throw new Error("the attempt deadline passed"); };
  setPrimaryUntil(until);
  const owned = [];
  const results = [];
  const observations = {};
  let workDir = null;
  let reused = null;
  const primary = { ok: false, verdict: "ERROR" };
  try {
    for (const file of SOURCES) log(`${path.relative(REPO, file)} sha256 ${bytesSha(file)}`);
    const commsSha = sha(REPO, commsRef), envSha = sha(ENV_REPO, envRef);
    log(`attempt ${label}: comms ${commsRef} = ${commsSha}; env ${envRef} = ${envSha}`);
    const runId = `aify-gate-${randomUUID().slice(0, 13)}`;
    workDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-gate-"));
    let images = { comms: `${runId}-comms`, env: `${runId}-env` };
    // DEVELOPMENT ONLY: reuse two images built earlier. They are not owned, and nothing proves they were built from the
    // refs named above, so the attempt record says so and such an attempt is never qualification evidence.
    reused = process.env.SANDBOX_GATE_IMAGES?.split(",") ?? null;
    if (reused && reused.length !== 2) throw new Error("SANDBOX_GATE_IMAGES names two images: comms,env");
    if (reused) {
      images = { comms: reused[0], env: reused[1] };
      log(`REUSING images ${reused.join(", ")}: not built from the refs above, so not qualification evidence`);
    } else {
      const tar = path.join(workDir, "comms.tar");
      must(run("git", ["-C", REPO, "archive", "--format=tar", "-o", tar, commsSha]), "git archive comms");
      create(owned, "image", images.comms, ["build", "-q", "-t", images.comms, "-"], { input: fs.readFileSync(tar), timeout: BUILD_MS }, dock);
      deadline();
      create(owned, "image", images.env, ["build", "-q", "-t", images.env, envContext(workDir, envSha)], { timeout: BUILD_MS }, dock);
    }
    const probePath = path.join(HERE, "probe.py");
    const scenarios = [...new Set([...CASES, ...OBSERVATIONS].map((c) => c.scenario))].filter((s) => !only || only.includes(s));
    for (const [index, name] of scenarios.entries()) {
      deadline();
      const caseOwned = [];
      const pair = new Pair({ owned: caseOwned, runId, caseId: `s${index}`, images, probePath, key: KEY, dock });
      const dir = path.join(outDir, name);
      let marks = null, error = null;
      try {
        log(`scenario ${name}: starting`);
        await pair.start(START[name] ?? {});
        marks = await SCENARIOS[name](pair, () => pair.evidence());
      } catch (failure) { error = redact(failure?.message || failure); }
      let evidence = null;
      try { evidence = pair.saveEvidence(dir, { marks, error }); } catch (failure) { error ??= redact(failure?.message); }
      const clean = cleanup(caseOwned, null, dock);
      owned.push(...caseOwned);
      for (const o of OBSERVATIONS.filter((x) => x.scenario === name)) observations[o.id] = { marks, error };
      for (const c of CASES.filter((x) => x.scenario === name)) {
        const judged = judgeCase({ id: c.id, error, evidence, judges: JUDGES });
        results.push({ id: c.id, scenario: name, expected: c.expected, red: c.red, ...judged });
        log(`case ${c.id}: ${judged.verdict ?? "FAIL"}${judged.pass ? "" : ` [${[...judged.failed, ...(judged.unknown ?? [])].join("; ")}]`}`);
      }
      if (!clean.ok) log(`scenario ${name}: cleanup left [${clean.remaining.join(", ")}] unknown [${clean.unknown.join(", ")}]`);
    }
    deadline(); // a verdict reached after the budget is not one
    primary.ok = results.length > 0 && results.every((r) => r.pass);
    primary.verdict = `${results.filter((r) => r.pass).length}/${results.length} cases passed`;
    fs.writeFileSync(path.join(outDir, "attempt.json"), `${JSON.stringify({ label, commsRef, commsSha, envRef, envSha, imagesReused: Boolean(reused), results, observations }, null, 1)}\n`);
  } catch (error) {
    primary.verdict = `ERROR: ${redact(error?.message || error)}`;
  } finally {
    setPrimaryUntil(null);
  }
  const clean = cleanup(owned, workDir, dock);
  const result = outcome(primary, clean);
  for (const line of result.lines) log(line);
  log(`terminal: exit ${result.exit}`);
  return result;
}

// The CLI, only when this file is the entry point: the gate's tests import attempt().
if (path.resolve(process.argv[1] ?? "") === SELF) {
  const arg = (name) => (process.argv.find((a) => a.startsWith(`--${name}=`)) ?? "").slice(name.length + 3);
  let exit;
  if (process.argv.includes("--self-check")) exit = selfCheck();
  else {
    const commsRef = arg("comms"), envRef = arg("env"), label = arg("attempt");
    if (!commsRef || !envRef || !label) {
      console.error("usage: gate.mjs --comms=<ref> --env=<ref> --attempt=<label> [--scenarios=a,b] [--out=<dir>] | --self-check");
      process.exit(2);
    }
    const outDir = path.resolve(arg("out") || path.join(os.tmpdir(), `aify-gate-${label}`));
    // An attempt owns its output, acquired exclusively: an existing folder is another attempt's and is never reused.
    try {
      acquireAttemptDir(outDir);
    } catch (error) {
      if (!(error instanceof AttemptDirTaken)) throw error;
      console.error(`refusing: ${error.message}; name a new --attempt or --out`);
      process.exit(2);
    }
    exit = (await attempt({ commsRef, envRef, label, only: arg("scenarios") ? arg("scenarios").split(",") : null, outDir })).exit;
  }
  process.exit(exit);
}
