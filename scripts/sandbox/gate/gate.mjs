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
import { KEY, bytesSha, cleanup, create, log, must, outcome, redact, run, sha } from "../env-against-comms.mjs";
import { CASES, SCENARIOS } from "./cases.mjs";
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
const started = Date.now();
const deadline = () => { if (Date.now() - started > ATTEMPT_MS) throw new Error("the attempt deadline passed"); };
const SOURCES = ["gate.mjs", "pair.mjs", "cases.mjs", "judges.mjs", "self-check.mjs", "stand-in.mjs", "claude-aify.sh",
  "probe.py", "env.Dockerfile"].map((name) => path.join(HERE, name))
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
  return path.join(workDir, "env");
}

async function attempt({ commsRef, envRef, label, only, outDir }) {
  const owned = [];
  const results = [];
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
      create(owned, "image", images.comms, ["build", "-q", "-t", images.comms, "-"], { input: fs.readFileSync(tar), timeout: BUILD_MS });
      deadline();
      create(owned, "image", images.env, ["build", "-q", "-t", images.env, envContext(workDir, envSha)], { timeout: BUILD_MS });
    }
    const probePath = path.join(HERE, "probe.py");
    const scenarios = [...new Set(CASES.map((c) => c.scenario))].filter((s) => !only || only.includes(s));
    for (const [index, name] of scenarios.entries()) {
      deadline();
      const caseOwned = [];
      const pair = new Pair({ owned: caseOwned, runId, caseId: `s${index}`, images, probePath, key: KEY });
      const dir = path.join(outDir, name);
      let marks = null, error = null;
      try {
        log(`scenario ${name}: starting`);
        await pair.start();
        marks = await SCENARIOS[name](pair, () => pair.evidence());
      } catch (failure) { error = redact(failure?.message || failure); }
      let evidence = null;
      try { evidence = pair.saveEvidence(dir, { marks, error }); } catch (failure) { error ??= redact(failure?.message); }
      const clean = cleanup(caseOwned, null);
      owned.push(...caseOwned);
      for (const c of CASES.filter((x) => x.scenario === name)) {
        const judged = error || !evidence ? { pass: false, failed: [`scenario error: ${error ?? "no evidence"}`] }
          : JUDGES[c.id](evidence);
        results.push({ id: c.id, scenario: name, expected: c.expected, red: c.red, ...judged });
        log(`case ${c.id}: ${judged.pass ? "PASS" : `FAIL [${judged.failed.join("; ")}]`}`);
      }
      if (!clean.ok) log(`scenario ${name}: cleanup left [${clean.remaining.join(", ")}] unknown [${clean.unknown.join(", ")}]`);
    }
    primary.ok = results.length > 0 && results.every((r) => r.pass);
    primary.verdict = `${results.filter((r) => r.pass).length}/${results.length} cases passed`;
    fs.writeFileSync(path.join(outDir, "attempt.json"), `${JSON.stringify({ label, commsRef, commsSha, envRef, envSha, imagesReused: Boolean(reused), results }, null, 1)}\n`);
  } catch (error) {
    primary.verdict = `ERROR: ${redact(error?.message || error)}`;
  }
  const clean = cleanup(owned, workDir);
  const result = outcome(primary, clean);
  for (const line of result.lines) log(line);
  log(`terminal: exit ${result.exit}`);
  return result.exit;
}

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
  fs.mkdirSync(outDir, { recursive: true });
  exit = await attempt({ commsRef, envRef, label, only: arg("scenarios") ? arg("scenarios").split(",") : null, outDir });
}
process.exit(exit);
