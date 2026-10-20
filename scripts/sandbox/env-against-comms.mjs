// A throwaway sandbox: aify-comms built from one commit and aify-env built from another. Both RUN on a Docker network
// created with `--internal` and publish no port, so at runtime neither can reach the live service, the live dashboard
// or the host's own aify-env. That is runtime isolation only: the builds run before the network exists and may fetch
// dependencies, so build only refs you trust. The env publishes to comms, is stopped with SIGTERM, and comms' own
// cursor and rows are read before and after.
//
//   node scripts/sandbox/env-against-comms.mjs --comms=<ref> --env=<ref> --expect=retired|kept [--no-agent]
//   node scripts/sandbox/env-against-comms.mjs --self-check      the ownership, cleanup, secret and predicate witnesses
//
// OWNERSHIP. A run owns only what it created: a random run id, an OS-reserved work dir, and a name it checked was
// absent before creating it. Cleanup removes those ids and nothing else, then reads each one back. A run succeeds
// only when its primary check holds AND that readback says every owned resource is gone. A failed readback is a
// cleanup failure that names the ids left, for removal by hand; it never widens what is deleted.
//
// The key is generated per run, passed to the containers through the environment, and redacted from every line this
// prints, error paths included. Every child command has a timeout and the primary steps share one run deadline. A
// SIGKILL of this process cannot be handled: the ids it owned are printed as they are created, for manual recovery.
//
// Adapted from aify-dashboard docs/evidence/sandbox-2026-10-20/run.mjs.

import { spawnSync } from "node:child_process";
import { createHash, randomBytes, randomUUID } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SELF = fileURLToPath(import.meta.url);
const HERE = path.dirname(SELF);
const REPO = path.join(HERE, "..", "..");
const ENV_REPO = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const HELPERS = ["env.Dockerfile", "sandbox-entry.sh"];
const COMMAND_MS = 120_000, BUILD_MS = 15 * 60_000, RUN_DEADLINE_MS = 30 * 60_000;
//: What the live stack is called. A sandbox name equal to one of these is refused before anything is made.
const LIVE = ["aify-comms-service", "aify-comms-dashboard-next", "aify-comms-network", "aify-comms-data", "aify-comms"];

const KEY = process.env.SANDBOX_SELF_CHECK_KEY || randomBytes(32).toString("hex");
const redact = (text) => String(text).split(KEY).join("<redacted key>");
const started = Date.now();
const log = (line) => console.log(redact(`+${((Date.now() - started) / 1000).toFixed(1)}s ${line}`));
const deadline = () => { if (Date.now() - started > RUN_DEADLINE_MS) throw new Error("the run deadline passed"); };

/** One child command, bounded. Never throws; its output is redacted before anything can carry it. */
function run(command, args, { input, env, cwd, timeout = COMMAND_MS } = {}) {
  const done = spawnSync(command, args, { input, cwd, env: env ?? process.env, timeout, maxBuffer: 1 << 28,
    encoding: input === undefined || typeof input === "string" ? "utf8" : undefined });
  const raw = `${done.stdout ?? ""}${done.stderr ?? ""}${done.error ? ` ${done.error.message}` : ""}`;
  return { status: done.status, timedOut: done.error?.code === "ETIMEDOUT", leaked: raw.includes(KEY), out: redact(raw) };
}
/** A failed or key-printing result becomes an error whose text is already redacted. */
function must(result, what) {
  if (result.status !== 0) throw new Error(`${what} ${result.timedOut ? "timed out" : `exited ${result.status}`}: ${result.out.slice(-2000)}`);
  if (result.leaked) throw new Error(`${what} printed the key; its output is withheld`);
  return result;
}
const docker = (args, opts) => run("docker", args, opts);
const sha = (repo, ref) => must(run("git", ["-C", repo, "rev-parse", `${ref}^{commit}`]), "git rev-parse").out.trim();
const bytesSha = (file) => createHash("sha256").update(fs.readFileSync(file)).digest("hex");

/** "present", "absent", or "unknown" when docker could not say (a timeout, a daemon error). */
function exists(kind, ref, dock = docker) {
  const r = dock([kind, "inspect", "--format", "{{.Id}}", ref]);
  if (r.status === 0) return "present";
  return r.status !== null && /no such|not found/i.test(r.out) ? "absent" : "unknown";
}

/** Create one resource this run will own. A name already present, or one docker cannot check, is refused. */
function create(owned, kind, name, args, opts, dock = docker) {
  if (LIVE.includes(name)) throw new Error(`refusing: ${name} is a live name`);
  const before = exists(kind, name, dock);
  if (before !== "absent") throw new Error(`refusing to create ${kind} ${name}: it is ${before}, so it is not this run's`);
  const r = dock(args, opts);
  // Checked absent a moment ago under a random name, so a resource by that name now is this run's, even if the
  // command failed after making it (a container that could not start, say).
  if (r.status !== 0 && exists(kind, name, dock) === "present") owned.push({ kind, ref: name });
  must(r, `create ${kind} ${name}`);
  owned.push({ kind, ref: kind === "image" ? name : r.out.trim().split(/\s+/).pop() });
  log(`owned ${kind} ${owned.at(-1).ref}`);
}

/** Remove each owned resource, then read each one back. ok only when every readback answered absent. */
function cleanup(owned, workDir, dock = docker) {
  const order = { container: 0, network: 1, image: 2 };
  for (const { kind, ref } of [...owned].sort((a, b) => order[a.kind] - order[b.kind])) {
    dock(kind === "container" ? ["rm", "-f", "-v", ref] : kind === "network" ? ["network", "rm", ref] : ["rmi", "-f", ref]);
  }
  const remaining = [], unknown = [];
  for (const { kind, ref } of owned) {
    const state = exists(kind, ref, dock);
    if (state === "present") remaining.push(`${kind} ${ref}`);
    if (state === "unknown") unknown.push(`${kind} ${ref}`);
  }
  if (workDir) {
    try { fs.rmSync(workDir, { recursive: true, force: true }); } catch { /* read back below */ }
    if (fs.existsSync(workDir)) remaining.push(`directory ${workDir}`);
  }
  return { ok: remaining.length === 0 && unknown.length === 0, remaining, unknown };
}

/** Success needs both the primary check and a complete cleanup; either failing is reported on its own line. */
function outcome(primary, clean) {
  return {
    exit: primary.ok && clean.ok ? 0 : 1,
    lines: [`primary: ${primary.verdict}`, clean.ok ? "cleanup: complete, every owned resource read back absent"
      : `cleanup: FAILED; still present [${clean.remaining.join(", ")}]; could not be read back [${clean.unknown.join(", ")}]; remove these by hand`],
  };
}

const keyOf = (p) => `${p.machine}/${p.instance}`;
const isResidents = (p) => p.instance.startsWith("residents-");
/** The managed key retired: same generation, reason shutdown, a later publication, no rows. Residents untouched. */
function retiredHolds(before, after) {
  const find = (p) => after.publishers.find((q) => keyOf(q) === keyOf(p));
  const managed = before.publishers.filter((p) => !isResidents(p));
  return managed.length > 0 && after.publishers.length === before.publishers.length
    && managed.every((b) => { const a = find(b); return a && a.kind === "retired" && a.reason === "shutdown"
      && a.generation === b.generation && a.publication > b.publication && a.rows === 0; })
    && before.publishers.filter(isResidents).every((b) => JSON.stringify(find(b)) === JSON.stringify(b));
}
const keptHolds = (before, after) => JSON.stringify(after.publishers) === JSON.stringify(before.publishers);

// Read inside the comms container, read-only: each publisher's cursor and how many rows it holds. Never the key.
const OBSERVE = `
import json, sqlite3
db = sqlite3.connect('file:/data/aify.db?mode=ro', uri=True)
cur = [dict(machine=m, instance=i, generation=g, publication=p, kind=k, reason=r, dataGeneration=dg, applied=da is not None)
       for m, i, g, p, k, r, dg, da in db.execute(
           'select machine_id, instance, generation, publication, kind, reason, data_generation, data_applied_at '
           'from agent_state_shadow_publishers order by instance')]
for c in cur:
    c['rows'] = db.execute('select count(*) from agent_state_shadow_rows where machine_id=? and instance=?',
                           (c['machine'], c['instance'])).fetchone()[0]
print(json.dumps(dict(publishers=cur)))
`;
const observe = (container) => JSON.parse(must(docker(["exec", "-i", container, "python", "-"], { input: OBSERVE }), "observe").out.trim().split("\n").pop());
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function sandbox({ commsRef, envRef, expect, agent }) {
  const owned = [];
  let workDir = null;
  const primary = { ok: false, verdict: "ERROR" };
  try {
    log(`driver sha256 ${bytesSha(SELF)}; ${HELPERS.map((h) => `${h} sha256 ${bytesSha(path.join(HERE, h))}`).join("; ")}`);
    const commsSha = sha(REPO, commsRef), envSha = sha(ENV_REPO, envRef);
    log(`comms ${commsRef} = ${commsSha}; env ${envRef} = ${envSha}; agent ${agent || "none"}; expect ${expect}`);
    const id = `aify-sandbox-${randomUUID()}`;
    const names = { network: id, comms: `${id}-comms`, env: `${id}-env`, commsImage: `${id}-comms`, envImage: `${id}-env` };
    log(`run ${id}: no published ports, /data on tmpfs (no volume)`);
    workDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-sandbox-"));
    const withKey = { ...process.env, API_KEY: KEY, SANDBOX_COMMS_KEY: KEY, SANDBOX_DEFINE: agent };

    const tarPath = path.join(workDir, "comms.tar");
    must(run("git", ["-C", REPO, "archive", "--format=tar", "-o", tarPath, commsSha]), "git archive comms");
    create(owned, "image", names.commsImage, ["build", "-q", "-t", names.commsImage, "-"], { input: fs.readFileSync(tarPath), timeout: BUILD_MS });
    deadline();
    must(run("git", ["-C", ENV_REPO, "archive", "--format=tar", "-o", path.join(workDir, "env.tar"), envSha]), "git archive env");
    fs.mkdirSync(path.join(workDir, "env"));
    // Relative names: GNU tar reads "C:" in a path as a remote host.
    must(run("tar", ["-xf", "env.tar", "-C", "env"], { cwd: workDir }), "tar");
    for (const h of HELPERS) {
      fs.writeFileSync(path.join(workDir, "env", h === "env.Dockerfile" ? "Dockerfile" : h), fs.readFileSync(path.join(HERE, h), "utf8").replace(/\r\n/g, "\n"));
    }
    create(owned, "image", names.envImage, ["build", "-q", "-t", names.envImage, path.join(workDir, "env")], { timeout: BUILD_MS });
    deadline();

    create(owned, "network", names.network, ["network", "create", "--internal", names.network]);
    create(owned, "container", names.comms, ["run", "-d", "--name", names.comms, "--network", names.network, "--network-alias", "comms",
      "--tmpfs", "/data:uid=1000,gid=1000", "-e", "API_KEY", "-e", "DATA_DIR=/data",
      // The env calls comms by its network alias; comms answers only hosts it trusts (browser_origin.py).
      "-e", "TRUSTED_HOSTS=comms,comms:8800", names.commsImage], { env: withKey });
    let healthy = false;
    for (let i = 0; i < 90 && !healthy; i += 1) {
      deadline();
      healthy = docker(["exec", names.comms, "curl", "-sf", "http://127.0.0.1:8800/health"]).status === 0;
      if (!healthy) await sleep(1000);
    }
    if (!healthy) throw new Error(`comms never answered /health; log: ${docker(["logs", names.comms]).out.slice(-3000)}`);
    log(`comms healthy; before the env: ${JSON.stringify(observe(names.comms))}`);

    create(owned, "container", names.env, ["run", "-d", "--init", "--name", names.env, "--hostname", "sandbox-env",
      "--network", names.network, "-e", "SANDBOX_COMMS_KEY", "-e", "SANDBOX_DEFINE", names.envImage], { env: withKey });
    // Ready: every key it published was applied and, with an agent defined, the managed key holds its row.
    let before = null;
    for (let i = 0; i < 120 && before === null; i += 1) {
      deadline();
      const seen = observe(names.comms);
      const managed = seen.publishers.filter((p) => !isResidents(p));
      if (managed.length && seen.publishers.every((p) => p.applied) && (!agent || managed.every((p) => p.rows > 0))) before = seen;
      else await sleep(1000);
    }
    if (before === null) throw new Error(`no applied publication in 120 s; env log: ${docker(["logs", names.env]).out.slice(-3000)}`);
    log(`env published: ${JSON.stringify(before)}`);

    must(docker(["kill", "-s", "TERM", names.env]), "docker kill");
    log(`env stopped by SIGTERM, exit ${must(docker(["wait", names.env], { timeout: 60_000 }), "docker wait").out.trim()}`);
    const after = observe(names.comms);
    log(`after: ${JSON.stringify(after)}`);
    log(`env log, last lines:\n${docker(["logs", names.env]).out.split("\n").slice(-25).join("\n")}`);
    primary.ok = expect === "retired" ? retiredHolds(before, after) : keptHolds(before, after);
    primary.verdict = `${primary.ok ? "AS EXPECTED" : "NOT AS EXPECTED"} (${expect})`;
  } catch (error) {
    primary.verdict = `ERROR: ${redact(error.message)}`;
  } finally {
    const clean = cleanup(owned, workDir);
    const result = outcome(primary, clean);
    for (const line of result.lines) log(line);
    log(`terminal: exit ${result.exit}`);
    return result.exit;
  }
}

/** The witnesses for ownership, cleanup, secrets and the predicate, with a control for each that must pass. */
function selfCheck() {
  const results = [];
  const check = (name, ok) => results.push(`${ok ? "PASS" : "FAIL"} ${name}`);
  // Secrets: a child that fails and prints a synthetic key, run through this file's own error and log paths.
  const synthetic = randomBytes(16).toString("hex");
  const leakEnv = { ...process.env, SANDBOX_SELF_CHECK_KEY: synthetic };
  const direct = spawnSync(process.execPath, ["-e", LEAKER], { env: leakEnv, encoding: "utf8" });
  check("control: the synthetic child really prints its key", `${direct.stdout}${direct.stderr}`.includes(synthetic));
  const inner = spawnSync(process.execPath, [SELF, "--self-check-leak"], { env: leakEnv, encoding: "utf8", timeout: COMMAND_MS });
  const seen = `${inner.stdout}${inner.stderr}`;
  check("the key never reaches this file's output on a failing child", !seen.includes(synthetic));
  check("the failure is still reported, redacted", seen.includes("exited 3") && seen.includes("<redacted key>"));
  // Cleanup: a stub docker that cannot remove, one that cannot list, and one that works.
  const stub = (inspect) => (args) => (args[1] === "inspect" ? inspect : { status: 0, out: "", timedOut: false, leaked: false });
  const presentStub = stub({ status: 0, out: "sha256:x", timedOut: false });
  const owned = [{ kind: "container", ref: "c1" }];
  const stuck = cleanup(owned, null, presentStub);
  check("a resource still present fails cleanup and is named", !stuck.ok && stuck.remaining.includes("container c1"));
  check("a primary pass with failed cleanup exits nonzero", outcome({ ok: true, verdict: "x" }, stuck).exit === 1);
  const blind = cleanup(owned, null, stub({ status: null, out: "spawnSync docker ETIMEDOUT", timedOut: true }));
  check("a listing that times out fails cleanup (unknown, not empty)", !blind.ok && blind.unknown.includes("container c1"));
  const fine = cleanup(owned, null, stub({ status: 1, out: "Error: No such container: c1", timedOut: false }));
  check("control: a clean readback passes and exits 0", fine.ok && outcome({ ok: true, verdict: "x" }, fine).exit === 0);
  const adopted = [];
  let refused = false;
  try { create(adopted, "container", "aify-sandbox-taken", ["run"], {}, presentStub); } catch { refused = true; }
  check("an existing name is refused and never owned", refused && adopted.length === 0);
  // The predicate: each single-field countermodel fails; the real transition passes.
  const b = { machine: "m", instance: "default", generation: 7, publication: 1, kind: "snapshot", reason: null, rows: 1 };
  const r = { machine: "m", instance: "residents-x", generation: 8, publication: 1, kind: "snapshot", reason: null, rows: 0 };
  const good = { ...b, kind: "retired", reason: "shutdown", publication: 2, rows: 0 };
  const pair = (a) => retiredHolds({ publishers: [b, r] }, { publishers: [a, r] });
  check("control: the real retirement holds", pair(good));
  for (const [field, value] of [["generation", 8], ["reason", "other"], ["publication", 1], ["rows", 1], ["kind", "snapshot"]]) {
    check(`a retirement with ${field}=${value} does not hold`, !pair({ ...good, [field]: value }));
  }
  check("a changed residents key does not hold", !retiredHolds({ publishers: [b, r] }, { publishers: [good, { ...r, publication: 2 }] }));
  for (const line of results) console.log(line);
  return results.every((l) => l.startsWith("PASS")) ? 0 : 1;
}
const LEAKER = "console.log('child says ' + process.env.SANDBOX_SELF_CHECK_KEY); console.error('also ' + process.env.SANDBOX_SELF_CHECK_KEY); process.exit(3)";

const arg = (name) => (process.argv.find((a) => a.startsWith(`--${name}=`)) ?? "").slice(name.length + 3);
let exit;
if (process.argv.includes("--self-check")) exit = selfCheck();
else if (process.argv.includes("--self-check-leak")) {
  // The same paths a real failure takes: must() builds the error, the catch logs it.
  try { must(run(process.execPath, ["-e", LEAKER]), "synthetic child"); } catch (error) { log(`ERROR: ${error.message}`); }
  exit = 0;
} else {
  const commsRef = arg("comms"), envRef = arg("env"), expect = arg("expect");
  if (!commsRef || !envRef || !["retired", "kept"].includes(expect)) {
    console.error("usage: env-against-comms.mjs --comms=<ref> --env=<ref> --expect=retired|kept [--no-agent] | --self-check");
    process.exit(2);
  }
  exit = await sandbox({ commsRef, envRef, expect, agent: process.argv.includes("--no-agent") ? "" : "sandbox-probe" });
}
process.exit(exit);
