// Guided install S2's witness table, run for real: a throwaway comms built from this checkout's commit, and fresh
// node+git containers that install from nothing. Nothing here can reach the live service: the comms under test
// publishes no port and holds a key made for this run, and every installer container starts empty.
//
//   node scripts/sandbox/install-chain-witness.mjs --env-ref <aify-env sha> --wrapper-ref <aify-wrapper sha> --out <dir>
//
// Needs: this checkout clean and its HEAD pushed (the served spec names HEAD as the artifact's git revision, and
// the chain clones it from GitHub); AIFY_ENV_REPO and AIFY_WRAPPER_REPO checkouts holding the two refs.
//
// OWNERSHIP: a run owns only the names it creates (s2w-<run>-*), records each as it is made, removes only those, and
// reads each one back. Cleanup prints anything it could not remove, for removal by hand.
//
// Rows (plan guided-install/S2-chain.md, "Witnesses"):
//   missing-key      no key supplied or stored: exit non-zero naming aify-comms.credentialRef; no registry, no launcher
//   unapproved       the artifact tuple is not approved: refused naming its --approve digest; nothing connected
//   render-after     acquires, connects, renders; claude absent so it exits non-zero naming `claude: pending (CLI absent)`;
//                    the launcher's fingerprint is current (aify-wrapper-check), so it was rendered after connect
//   no-recursion     comms' installer as the entry: the chain is entered once, the registry is unchanged across assets
//   key-from-store   the installed bridge, with no key in its environment, authenticates with the key connect published
//   path-not-selection  codex on PATH, not selected: codex is never run and no codex launcher is written

import { spawnSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const arg = (name) => { const i = process.argv.indexOf(`--${name}`); return i === -1 ? "" : process.argv[i + 1]; };
const ENV_REF = arg("env-ref"), WRAPPER_REF = arg("wrapper-ref"), OUT = arg("out");
const ENV_REPO = process.env.AIFY_ENV_REPO || path.join(os.homedir(), "projects", "aify-env");
const WRAPPER_REPO = process.env.AIFY_WRAPPER_REPO || path.join(os.homedir(), "projects", "aify-wrapper");
const IMAGE = process.env.WITNESS_NODE_IMAGE || "node:22-bookworm";
if (!ENV_REF || !WRAPPER_REF || !OUT) { console.error("usage: --env-ref <sha> --wrapper-ref <sha> --out <dir>"); process.exit(64); }
if (fs.existsSync(OUT)) { console.error(`${OUT} exists; a run writes a new directory`); process.exit(64); }
fs.mkdirSync(OUT, { recursive: true });

const RUN = randomBytes(4).toString("hex");
const KEY = randomBytes(24).toString("hex");
const owned = [];
const lines = [];
const log = (line) => { const text = String(line).replaceAll(KEY, "<key>"); lines.push(text); console.log(text); };
const run = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", maxBuffer: 64 << 20, timeout: opts.timeout ?? 1_200_000, ...opts, env: { ...process.env, MSYS_NO_PATHCONV: "1", ...(opts.env ?? {}) } });
  return { status: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
};
const docker = (args, opts) => run("docker", args, opts);
const must = (what, r) => { if (r.status !== 0) throw new Error(`${what} failed (${r.status}): ${r.out.slice(-2000)}`); return r; };
const own = (kind, name) => { owned.push({ kind, name }); log(`owned ${kind} ${name}`); };
const sh = (container, script, opts = {}) => docker(["exec", "-i", container, "bash", "-lc", script], { input: opts.input ?? "", timeout: opts.timeout });

function cleanup() {
  for (const { kind, name } of [...owned].reverse()) {
    const rm = kind === "image" ? ["rmi", "-f", name] : kind === "network" ? ["network", "rm", name] : ["rm", "-f", "-v", name];
    docker(rm);
  }
  const left = owned.filter(({ kind, name }) => docker(kind === "image" ? ["image", "inspect", name] : kind === "network" ? ["network", "inspect", name] : ["inspect", name]).status === 0);
  log(left.length ? `cleanup: INCOMPLETE, left ${left.map((o) => `${o.kind} ${o.name}`).join(", ")}` : "cleanup: complete, every owned resource read back absent");
  return left.length === 0;
}

/** A fresh installer host on the run's network, holding the two sources and the key file. */
function host(label) {
  const name = `s2w-${RUN}-${label}`;
  must("start host", docker(["run", "-d", "--name", name, "--network", `s2w-${RUN}`, IMAGE, "sleep", "infinity"]));
  own("container", name);
  for (const [repo, ref, dir] of [[ENV_REPO, ENV_REF, "aify-env"], [WRAPPER_REPO, WRAPPER_REF, "aify-wrapper"], [REPO, "HEAD", "aify-comms"]]) {
    const tar = path.join(OUT, `${dir}.tar`);
    if (!fs.existsSync(tar)) must(`archive ${dir}`, run("git", ["-C", repo, "archive", "--format=tar", "-o", tar, ref]));
    must(`copy ${dir}`, docker(["cp", tar, `${name}:/tmp/${dir}.tar`]));
    must(`extract ${dir}`, sh(name, `mkdir -p /src/${dir} && tar -xf /tmp/${dir}.tar -C /src/${dir}`));
  }
  must("key file", sh(name, "umask 077; cat > /tmp/key", { input: KEY }));
  return name;
}

const results = [];
function row(id, ok, detail) { results.push({ id, ok, detail }); log(`row ${id}: ${ok ? "PASS" : "FAIL"} -- ${detail}`); }
const exists = (container, file) => sh(container, `test -e ${file}`).status === 0;
const CONNECT = "--connect aify-comms=http://comms:8800 --client claude --no-prompt";

let ok = false;
try {
  const head = must("head", run("git", ["-C", REPO, "rev-parse", "HEAD"])).out.trim();
  if (must("status", run("git", ["-C", REPO, "status", "--porcelain"])).out.trim()) throw new Error("this checkout is dirty");
  log(`comms ${head}, aify-env ${ENV_REF}, aify-wrapper ${WRAPPER_REF}, run ${RUN}`);

  must("stamp", run("bash", [path.join(REPO, "scripts", "stamp.sh")], { cwd: REPO }));
  must("build comms", docker(["build", "-q", "-t", `s2w-${RUN}-comms`, REPO], { timeout: 1_800_000 }));
  own("image", `s2w-${RUN}-comms`);
  must("network", docker(["network", "create", `s2w-${RUN}`]));
  own("network", `s2w-${RUN}`);
  must("comms", docker(["run", "-d", "--name", `s2w-${RUN}-comms`, "--network", `s2w-${RUN}`, "--network-alias", "comms",
    "-e", "API_KEY", "-e", "DATA_DIR=/data", "-e", "TRUSTED_HOSTS=comms:8800,comms", `s2w-${RUN}-comms`], { env: { API_KEY: KEY } }));
  own("container", `s2w-${RUN}-comms`);
  for (let i = 0; i < 60 && docker(["exec", `s2w-${RUN}-comms`, "python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8800/health',timeout=3)"]).status !== 0; i += 1) spawnSync(process.execPath, ["-e", "setTimeout(()=>{},2000)"]);
  const spec = docker(["exec", `s2w-${RUN}-comms`, "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://localhost:8800/.well-known/aify-service').read().decode())"]);
  must("spec", spec);
  const revision = JSON.parse(spec.out).artifacts?.[0]?.source?.git?.revision;
  log(`served artifact revision ${revision}`);
  if (revision !== head) throw new Error(`the fixture serves revision ${revision}, not HEAD ${head}`);

  // Row: missing key, and the unapproved tuple, on one fresh host.
  const a = host("a");
  const missing = sh(a, `cd /src/aify-env && AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash install.sh ${CONNECT} 2>&1`);
  fs.writeFileSync(path.join(OUT, "missing-key.txt"), missing.out);
  row("missing-key", missing.status !== 0 && /missing aify-comms\.credentialRef/.test(missing.out) && !exists(a, "~/.aify/services.json") && !exists(a, "~/.local/bin/claude-aify"),
    `exit ${missing.status}; registry ${exists(a, "~/.aify/services.json") ? "written" : "absent"}; launcher ${exists(a, "~/.local/bin/claude-aify") ? "written" : "absent"}`);
  const unapproved = sh(a, `cd /src/aify-env && AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash install.sh ${CONNECT} --secret aify-comms.credentialRef=/tmp/key 2>&1`);
  fs.writeFileSync(path.join(OUT, "unapproved.txt"), unapproved.out);
  const digest = /--approve ([0-9a-f]{64})/.exec(unapproved.out)?.[1];
  row("unapproved", unapproved.status !== 0 && Boolean(digest) && !exists(a, "~/.aify/services.json"), `exit ${unapproved.status}; digest ${digest ?? "none"}`);

  // Row: render after connect (claude absent), on the same host.
  const full = sh(a, `cd /src/aify-env && AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash install.sh ${CONNECT} --secret aify-comms.credentialRef=/tmp/key --approve ${digest} 2>&1`);
  fs.writeFileSync(path.join(OUT, "render-after.txt"), full.out);
  const check = sh(a, "node \"$(npm root -g)/aify-wrapper/bin/aify-wrapper-check.mjs\" --json");
  fs.writeFileSync(path.join(OUT, "render-after-check.json"), check.out);
  let current = false;
  try { const c = JSON.parse(check.out); current = c.current?.length > 0 && !c.stale?.length && !c.unknown?.length; } catch { /* stays false */ }
  row("render-after", full.status !== 0 && /claude: pending \(CLI absent\)/.test(full.out) && /chain: entered/.test(full.out) && current,
    `exit ${full.status}; launcher fingerprint ${current ? "current" : "NOT current"}`);
  // The installer's modes take no key (7174c339): the installed bridge must find the one connect published, with no
  // key in its environment. Control: the same call with no key is refused, so a 200 is the key's doing.
  const probe = sh(a, `cd ~/.aify-comms && env -u AIFY_API_KEY -u CLAUDE_MCP_API_KEY node --input-type=module -e '
    const m = await import(process.cwd() + "/aify-service-endpoint.mjs");
    const key = m.destinationKeyResolver("http://comms:8800", { env: {} })("http://comms:8800");
    const call = (h) => fetch("http://comms:8800/api/v1/agents", { headers: h }).then((r) => r.status);
    console.log("store-key", key ? "found" : "none", "with", await call({ "X-API-Key": key }), "without", await call({}));' 2>&1`);
  fs.writeFileSync(path.join(OUT, "key-from-store.txt"), probe.out);
  row("key-from-store", /store-key found with 200 without 401/.test(probe.out), probe.out.trim().split("\n").pop());

  // Rows: no recursion and PATH is not selection, with comms' own installer as the entry on a fresh host.
  const b = host("b");
  must("stubs", sh(b, "for c in claude codex; do printf '#!/usr/bin/env bash\\necho \"%s $*\" >> /tmp/%s.log\\n' $c $c > /usr/local/bin/$c; chmod +x /usr/local/bin/$c; done"));
  must("aify-env deps", sh(b, "cd /src/aify-env && npm install --no-audit --no-fund >/dev/null 2>&1"));
  const entry = sh(b, `cd /src/aify-comms && AIFY_API_KEY=$(cat /tmp/key) AIFY_INSTALL_APPROVE=${digest} AIFY_INSTALL_ENV_SOURCE=/src/aify-env AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash install.sh --client claude http://comms:8800 2>&1 </dev/null`);
  fs.writeFileSync(path.join(OUT, "no-recursion.txt"), entry.out);
  const entered = (entry.out.match(/chain: entered/g) ?? []).length;
  const before = /registry before assets: ([0-9a-f]{64})/.exec(entry.out)?.[1], after = /registry after assets: ([0-9a-f]{64})/.exec(entry.out)?.[1];
  row("no-recursion", entry.status === 0 && entered === 1 && before && before === after && exists(b, "/tmp/claude.log"),
    `exit ${entry.status}; entered ${entered}; registry ${before ?? "?"} -> ${after ?? "?"}; claude assets ${exists(b, "/tmp/claude.log") ? "applied" : "NOT applied"}`);
  row("path-not-selection", !exists(b, "/tmp/codex.log") && !exists(b, "~/.local/bin/codex-aify") && exists(b, "/tmp/claude.log"),
    `codex ${exists(b, "/tmp/codex.log") ? "RUN" : "never run"}; codex-aify ${exists(b, "~/.local/bin/codex-aify") ? "written" : "absent"}`);
  ok = results.every((r) => r.ok);
} catch (error) {
  log(`ERROR: ${error.message}`);
}
const clean = cleanup();
fs.writeFileSync(path.join(OUT, "witness.txt"), `${lines.join("\n")}\n`);
fs.writeFileSync(path.join(OUT, "results.json"), `${JSON.stringify({ run: RUN, envRef: ENV_REF, wrapperRef: WRAPPER_REF, results }, null, 1)}\n`);
for (const tar of ["aify-env", "aify-wrapper", "aify-comms"]) fs.rmSync(path.join(OUT, `${tar}.tar`), { force: true });
log(`terminal: ${ok && clean ? "exit 0" : "exit 1"}`);
process.exit(ok && clean ? 0 : 1);
