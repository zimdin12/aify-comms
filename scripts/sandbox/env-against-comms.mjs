// A throwaway sandbox: aify-comms built from one commit and aify-env built from another, on a Docker network with no
// route out, so neither can reach the live service, the live dashboard or the host's own aify-env. The env publishes
// to comms, is stopped with SIGTERM, and comms' own cursor and rows are read before and after. Everything it made is
// removed at the end, pass or fail, by the run's own label.
//
//   node scripts/sandbox/env-against-comms.mjs --comms=<ref> --env=<ref> --expect=retired|kept [--no-agent]
//
// Adapted from aify-dashboard docs/evidence/sandbox-2026-10-20/run.mjs. The key is generated here, passed to the
// containers through the environment, and never printed. comms runs on a tmpfs /data, so no volume is made at all.

import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.join(HERE, "..", "..");
const ENV_REPO = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const arg = (name) => (process.argv.find((a) => a.startsWith(`--${name}=`)) ?? "").slice(name.length + 3);
const COMMS_REF = arg("comms"), ENV_REF = arg("env"), EXPECT = arg("expect");
const AGENT = process.argv.includes("--no-agent") ? "" : "sandbox-probe";
if (!COMMS_REF || !ENV_REF || !["retired", "kept"].includes(EXPECT)) {
  console.error("usage: env-against-comms.mjs --comms=<ref> --env=<ref> --expect=retired|kept [--no-agent]");
  process.exit(2);
}

const ID = `aify-sandbox-${Date.now()}`;
const LABEL = `aify.sandbox=${ID}`;
const KEY = randomBytes(32).toString("hex");
const WORK = path.join(os.tmpdir(), ID);
//: What the live stack is called. The run refuses to start if any name it would use is one of these.
const LIVE = ["aify-comms-service", "aify-comms-dashboard-next", "aify-comms-network", "aify-comms-data", "aify-comms"];
const started = Date.now();
const stamp = () => `+${((Date.now() - started) / 1000).toFixed(1)}s`;
const log = (line) => console.log(`${stamp()} ${line}`);
const withKey = { ...process.env, API_KEY: KEY, SANDBOX_COMMS_KEY: KEY, SANDBOX_DEFINE: AGENT };

function run(command, args, { input, env, cwd, quiet = false, allowFail = false } = {}) {
  const done = spawnSync(command, args, { input, cwd, env: env ?? process.env, encoding: input === undefined || typeof input === "string" ? "utf8" : undefined, maxBuffer: 1 << 28 });
  const out = `${done.stdout ?? ""}${done.stderr ?? ""}`;
  if (done.status !== 0 && !allowFail) throw new Error(`${command} ${args.slice(0, 3).join(" ")} exited ${done.status}: ${String(out).slice(-2000)}`);
  if (!quiet && String(out).includes(KEY)) throw new Error("the key appeared in a command's output; nothing more is printed");
  return { status: done.status, out: String(out) };
}
const sha = (repo, ref) => run("git", ["-C", repo, "rev-parse", `${ref}^{commit}`]).out.trim();

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
const observe = (container) => JSON.parse(run("docker", ["exec", "-i", container, "python", "-"], { input: OBSERVE }).out.trim().split("\n").pop());

let verdict = "ERROR";
try {
  const commsSha = sha(REPO, COMMS_REF), envSha = sha(ENV_REPO, ENV_REF);
  log(`comms ${COMMS_REF} = ${commsSha}; env ${ENV_REF} = ${envSha}; agent ${AGENT || "none"}; expect ${EXPECT}`);
  const names = { network: ID, comms: `${ID}-comms`, env: `${ID}-env`, commsImage: `${ID}-comms`, envImage: `${ID}-env` };
  log(`resources: ${JSON.stringify(names)}, label ${LABEL}, no published ports, /data on tmpfs (no volume)`);
  const clash = Object.values(names).filter((n) => LIVE.includes(n));
  if (clash.length) throw new Error(`a sandbox name is a live one: ${clash}`);

  fs.mkdirSync(WORK, { recursive: true });
  run("docker", ["build", "-q", "--label", LABEL, "-t", names.commsImage, "-"],
    { input: spawnSync("git", ["-C", REPO, "archive", "--format=tar", commsSha], { maxBuffer: 1 << 28 }).stdout });
  log("comms image built");

  run("git", ["-C", ENV_REPO, "archive", "--format=tar", "-o", path.join(WORK, "env.tar"), envSha]);
  const context = path.join(WORK, "env");
  fs.mkdirSync(context);
  // Relative names: GNU tar reads "C:" in a path as a remote host.
  run("tar", ["-xf", "env.tar", "-C", "env"], { cwd: WORK });
  for (const file of ["env.Dockerfile", "sandbox-entry.sh"]) {
    fs.writeFileSync(path.join(context, file === "env.Dockerfile" ? "Dockerfile" : file),
      fs.readFileSync(path.join(HERE, file), "utf8").replace(/\r\n/g, "\n"));
  }
  run("docker", ["build", "-q", "--label", LABEL, "-t", names.envImage, context]);
  log("env image built");

  run("docker", ["network", "create", "--internal", "--label", LABEL, names.network]);
  run("docker", ["run", "-d", "--label", LABEL, "--name", names.comms, "--network", names.network, "--network-alias", "comms",
    "--tmpfs", "/data:uid=1000,gid=1000", "-e", "API_KEY", "-e", "DATA_DIR=/data",
    // The env calls comms by its network alias; comms answers only hosts it trusts (browser_origin.py).
    "-e", "TRUSTED_HOSTS=comms,comms:8800", names.commsImage], { env: withKey });
  let healthy = false;
  for (let i = 0; i < 90 && !healthy; i += 1) {
    healthy = run("docker", ["exec", names.comms, "curl", "-sf", "http://127.0.0.1:8800/health"], { allowFail: true }).status === 0;
    if (!healthy) await new Promise((r) => setTimeout(r, 1000));
  }
  if (!healthy) throw new Error(`comms never answered /health; log: ${run("docker", ["logs", names.comms], { allowFail: true }).out.slice(-3000)}`);
  log(`comms healthy; before the env: ${JSON.stringify(observe(names.comms))}`);

  run("docker", ["run", "-d", "--init", "--label", LABEL, "--name", names.env, "--hostname", "sandbox-env", "--network", names.network,
    "-e", "SANDBOX_COMMS_KEY", "-e", "SANDBOX_DEFINE", names.envImage], { env: withKey });
  // Ready means every key it published was applied, and, with an agent defined, the managed key holds its row.
  let before = null;
  for (let i = 0; i < 120 && before === null; i += 1) {
    const seen = observe(names.comms);
    const managed = seen.publishers.filter((p) => !p.instance.startsWith("residents-"));
    if (managed.length && seen.publishers.every((p) => p.applied) && (!AGENT || managed.every((p) => p.rows > 0))) before = seen;
    else await new Promise((r) => setTimeout(r, 1000));
  }
  if (before === null) throw new Error(`no applied publication in 120 s; env log: ${run("docker", ["logs", names.env], { allowFail: true }).out.slice(-3000)}`);
  log(`env published: ${JSON.stringify(before)}`);

  run("docker", ["kill", "-s", "TERM", names.env]);
  log(`env stopped by SIGTERM, exit ${run("docker", ["wait", names.env]).out.trim()}`);
  const after = observe(names.comms);
  log(`after: ${JSON.stringify(after)}`);
  log(`env log, last lines:\n${run("docker", ["logs", names.env], { allowFail: true }).out.split("\n").slice(-25).join("\n")}`);

  const key = (p) => `${p.machine}/${p.instance}`;
  const managedBefore = before.publishers.filter((p) => !p.instance.startsWith("residents-")).map(key);
  const retired = after.publishers.filter((p) => p.kind === "retired");
  const unchanged = (p) => JSON.stringify(after.publishers.find((q) => key(q) === key(p))) === JSON.stringify(p);
  const residentsKept = before.publishers.filter((p) => p.instance.startsWith("residents-")).every(unchanged);
  const ok = EXPECT === "retired"
    ? retired.length > 0 && retired.map(key).every((k) => managedBefore.includes(k)) && retired.every((p) => p.rows === 0)
      && managedBefore.every((k) => retired.map(key).includes(k)) && residentsKept
    : retired.length === 0 && before.publishers.every(unchanged);
  verdict = ok ? `AS EXPECTED (${EXPECT})` : `NOT AS EXPECTED (${EXPECT})`;
  log(`managed keys before: ${JSON.stringify(managedBefore)}; retired after: ${JSON.stringify(retired.map((p) => `${key(p)} pub ${p.publication} rows ${p.rows}`))}; residents unchanged ${residentsKept}; ${verdict}`);
} catch (error) {
  log(`ERROR: ${error.message}`);
} finally {
  const ids = (kind, ...extra) => run("docker", [kind, "ls", ...extra, "-q", "--filter", `label=${LABEL}`], { allowFail: true }).out.split(/\s+/).filter(Boolean);
  const containers = ids("container", "-a");
  if (containers.length) run("docker", ["rm", "-f", "-v", ...containers], { allowFail: true });
  for (const n of ids("network")) run("docker", ["network", "rm", n], { allowFail: true });
  const images = ids("image");
  if (images.length) run("docker", ["rmi", "-f", ...new Set(images)], { allowFail: true });
  fs.rmSync(WORK, { recursive: true, force: true });
  log(`cleaned up: containers left [${ids("container", "-a")}], networks left [${ids("network")}], images left [${ids("image")}], work dir exists ${fs.existsSync(WORK)}`);
}
console.log(`verdict: ${verdict}`);
process.exit(verdict.startsWith("AS EXPECTED") ? 0 : 1);
