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
// reads each one back. A read-back is "absent" only when Docker answers that it does not exist; any other failure
// (Docker unavailable, a timeout) is UNKNOWN, and an unknown cleanup fails the run, naming what it could not confirm.
//
// Rows (plan guided-install/S2-chain.md, "Witnesses"). Each positive row runs on a host of its own that nothing ran
// on before (review E1), so a first-invocation defect cannot hide behind prerequisites an earlier row installed.
//   missing-key       no key supplied or stored: exit non-zero naming aify-comms.credentialRef; no registry, no launcher
//   unapproved        the artifact tuple is not approved: refused naming its --approve digest; nothing connected
//   render-after      cold host, aify-env's entry run from another directory with a RELATIVE key file (C6): acquires,
//                     connects, renders; claude absent, so exit non-zero naming `claude: pending (CLI absent)`; the
//                     launcher's fingerprint is current (aify-wrapper-check), so it was rendered after connect
//   key-from-store    that bridge, no key in its environment, authenticates through its production httpCall from the
//                     key connect published; control: the same call with the registry pointed away is refused
//   no-recursion      cold host, comms' installer as the entry with the key: entered once, registry unchanged by assets
//   no-key-in-argv    that run's claude children (argv and environment) never carry the key value (C1); controls: the
//                     instrument finds the key where it is, and finds the registration it inspects
//   keyed-continuation  that host's bridge then authenticates through httpCall from the published key alone
//   path-not-selection  codex on PATH, not selected: never run, no codex launcher, no codex skills; claude's skills are
//                     there (engaged); control: codex assets applied by hand make the same detector say PRESENT (E2)
//   no-early-store    cold host holding an OLD key under the default reference: a plain comms install with a NEW key
//                     whose artifact is unapproved is refused, and the stored key is still OLD (C2)

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
const OLD = randomBytes(24).toString("hex");
const owned = [];
const lines = [];
const log = (line) => { const text = String(line).replaceAll(KEY, "<key>").replaceAll(OLD, "<old-key>"); lines.push(text); console.log(text); };
const run = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", maxBuffer: 64 << 20, timeout: opts.timeout ?? 1_200_000, ...opts, env: { ...process.env, ...(opts.env ?? {}) } });
  return { status: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
};
// Only docker gets MSYS_NO_PATHCONV (its container paths must stay literal): stamp.sh under it handed node a /c/... path
// that Node reads as C:\c\..., wrote no spec, and the fixture served 503.
const docker = (args, opts = {}) => run("docker", args, { ...opts, env: { MSYS_NO_PATHCONV: "1", ...(opts.env ?? {}) } });
const must = (what, r) => { if (r.status !== 0) throw new Error(`${what} failed (${r.status}): ${r.out.slice(-2000)}`); return r; };
const own = (kind, name) => { owned.push({ kind, name }); log(`owned ${kind} ${name}`); };
const sh = (container, script, opts = {}) => docker(["exec", "-i", container, "bash", "-lc", script], { input: opts.input ?? "", timeout: opts.timeout });
const save = (file, text) => fs.writeFileSync(path.join(OUT, file), String(text).replaceAll(KEY, "<key>").replaceAll(OLD, "<old-key>"));

/** One owned resource read back: "absent" only on Docker's own not-found answer; anything else is unknown. */
function readBack({ kind, name }) {
  const r = docker(kind === "image" ? ["image", "inspect", name] : kind === "network" ? ["network", "inspect", name] : ["container", "inspect", name]);
  if (r.status === 0) return "present";
  return /no such (object|image|network|container)|not found/i.test(r.out) ? "absent" : `unknown (exit ${r.status}: ${r.out.trim().split("\n").pop()})`;
}

function cleanup() {
  for (const { kind, name } of [...owned].reverse()) {
    const rm = kind === "image" ? ["rmi", "-f", name] : kind === "network" ? ["network", "rm", name] : ["rm", "-f", "-v", name];
    const r = docker(rm);
    log(`remove ${kind} ${name}: exit ${r.status}`);
  }
  const states = owned.map((o) => ({ ...o, state: readBack(o) }));
  const open = states.filter((s) => s.state !== "absent");
  log(open.length ? `cleanup: NOT CONFIRMED, ${open.map((s) => `${s.kind} ${s.name} ${s.state}`).join("; ")}` : "cleanup: complete, every owned resource read back absent");
  return open.length === 0;
}

/** A fresh installer host on the run's network, holding the three sources and the key file. */
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

/** Stub runtime CLIs that record their argv and their environment's key variables, one log each. */
const stubs = (container, names) => must("stubs", sh(container, names.map((c) =>
  `printf '#!/usr/bin/env bash\\necho "%s $*" >> /tmp/${c}.log\\nenv | grep -i key >> /tmp/${c}.env || true\\n' ${c} > /usr/local/bin/${c}; chmod +x /usr/local/bin/${c}`).join("; ")));

/** The installed bridge's own httpCall, no key in its environment: "ok <status>" or the error it raised. */
const bridgeCall = (container, extraEnv = "") => sh(container, `cd ~/.aify-comms/mcp/stdio && env -u AIFY_API_KEY -u CLAUDE_MCP_API_KEY CLAUDE_MCP_SERVER_URL=http://comms:8800 AIFY_SERVER_URL=http://comms:8800 ${extraEnv} node --input-type=module -e '
  const m = await import(process.cwd() + "/aify-service-endpoint.mjs");
  try { await m.httpCall("GET", "/agents"); console.log("httpCall ok"); } catch (e) { console.log("httpCall refused:", String(e.message).slice(0, 120)); }' 2>&1`);

const results = [];
function row(id, ok, detail) { results.push({ id, ok, detail }); log(`row ${id}: ${ok ? "PASS" : "FAIL"} -- ${detail}`); }
const exists = (container, file) => sh(container, `test -e ${file}`).status === 0;
const ENV_ENTRY = "AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash /src/aify-env/install.sh --connect aify-comms=http://comms:8800 --client claude --no-prompt";
const COMMS_ENTRY = "AIFY_INSTALL_ENV_SOURCE=/src/aify-env AIFY_INSTALL_WRAPPER_SOURCE=/src/aify-wrapper bash install.sh --client claude http://comms:8800 2>&1 </dev/null";

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
  const spec = must("spec", docker(["exec", `s2w-${RUN}-comms`, "python", "-c", "import urllib.request;print(urllib.request.urlopen('http://localhost:8800/.well-known/aify-service').read().decode())"]));
  const revision = JSON.parse(spec.out).artifacts?.[0]?.source?.git?.revision;
  log(`served artifact revision ${revision}`);
  if (revision !== head) throw new Error(`the fixture serves revision ${revision}, not HEAD ${head}`);

  // Refusals, on one host: no key, then an unapproved tuple.
  const a = host("a");
  const missing = sh(a, `${ENV_ENTRY} 2>&1`);
  save("missing-key.txt", missing.out);
  row("missing-key", missing.status !== 0 && /missing aify-comms\.credentialRef/.test(missing.out) && !exists(a, "~/.aify/services.json") && !exists(a, "~/.local/bin/claude-aify"),
    `exit ${missing.status}; registry ${exists(a, "~/.aify/services.json") ? "written" : "absent"}; launcher ${exists(a, "~/.local/bin/claude-aify") ? "written" : "absent"}`);
  const unapproved = sh(a, `${ENV_ENTRY} --secret aify-comms.credentialRef=/tmp/key 2>&1`);
  save("unapproved.txt", unapproved.out);
  const digest = /--approve ([0-9a-f]{64})/.exec(unapproved.out)?.[1];
  row("unapproved", unapproved.status !== 0 && Boolean(digest) && !exists(a, "~/.aify/services.json"), `exit ${unapproved.status}; digest ${digest ?? "none"}`);

  // Cold positive through aify-env's entry, from /tmp with a RELATIVE key file.
  const c = host("c");
  const full = sh(c, `cd /tmp && ${ENV_ENTRY} --secret aify-comms.credentialRef=key --approve ${digest} 2>&1`);
  save("render-after.txt", full.out);
  const check = sh(c, "node \"$(npm root -g)/aify-wrapper/bin/aify-wrapper-check.mjs\" --json");
  save("render-after-check.json", check.out);
  let current = false;
  try { const j = JSON.parse(check.out); current = j.current?.length > 0 && !j.stale?.length && !j.unknown?.length; } catch { /* stays false */ }
  row("render-after", full.status !== 0 && /claude: pending \(CLI absent\)/.test(full.out) && /chain: entered/.test(full.out) && /connected aify-comms at http:\/\/comms:8800: committed/.test(full.out) && current,
    `exit ${full.status}; relative key file ${/committed/.test(full.out) ? "read" : "NOT read"}; launcher fingerprint ${current ? "current" : "NOT current"}`);
  const stored = bridgeCall(c), away = bridgeCall(c, "AIFY_SERVICE_REGISTRY=/nonexistent/services.json");
  save("key-from-store.txt", `${stored.out}\n--- control, registry pointed away ---\n${away.out}`);
  row("key-from-store", /httpCall ok/.test(stored.out) && /httpCall refused/.test(away.out), `published key: ${stored.out.trim().split("\n").pop()}; registry away: ${away.out.trim().split("\n").pop()}`);

  // Cold keyed positive through comms' own installer.
  const b = host("b");
  stubs(b, ["claude", "codex"]);
  must("aify-env deps", sh(b, "cd /src/aify-env && npm install --no-audit --no-fund >/dev/null 2>&1"));
  const entry = sh(b, `cd /src/aify-comms && AIFY_API_KEY=$(cat /tmp/key) AIFY_INSTALL_APPROVE=${digest} ${COMMS_ENTRY}`);
  save("no-recursion.txt", entry.out);
  const entered = (entry.out.match(/chain: entered/g) ?? []).length;
  const before = /registry before assets: ([0-9a-f]{64})/.exec(entry.out)?.[1], after = /registry after assets: ([0-9a-f]{64})/.exec(entry.out)?.[1];
  row("no-recursion", entry.status === 0 && entered === 1 && before && before === after,
    `exit ${entry.status}; entered ${entered}; registry ${before ?? "?"} -> ${after ?? "?"}`);
  const children = sh(b, "cat /tmp/claude.log /tmp/claude.env 2>/dev/null").out;
  save("no-key-in-argv.txt", children);
  const instrument = sh(b, `grep -c ${KEY} /tmp/key`).out.trim() === "1";
  const registration = /claude mcp add .*AIFY_SERVER_URL=http:\/\/comms:8800/.test(children);
  row("no-key-in-argv", !children.includes(KEY) && instrument && registration,
    `key in claude argv/env: ${children.includes(KEY) ? "PRESENT" : "absent"}; control: grep finds the key in /tmp/key ${instrument}, registration inspected ${registration}`);
  const continued = bridgeCall(b);
  save("keyed-continuation.txt", continued.out);
  row("keyed-continuation", /httpCall ok/.test(continued.out), continued.out.trim().split("\n").pop());
  const codexSkills = () => exists(b, "~/.codex/skills/aify-comms");
  const unselected = !exists(b, "/tmp/codex.log") && !exists(b, "~/.local/bin/codex-aify") && !codexSkills();
  const engaged = exists(b, "~/.claude/skills/aify-comms/SKILL.md");
  must("codex assets by hand", sh(b, "cd /src/aify-comms && bash install.sh --assets-only --client codex http://comms:8800 >/tmp/codex-by-hand.txt 2>&1 </dev/null"));
  const detects = codexSkills();
  row("path-not-selection", unselected && engaged && detects,
    `codex unselected: ${unselected ? "nothing" : "SOMETHING"} applied; claude skills ${engaged ? "present" : "ABSENT"}; control: codex skills after a by-hand codex assets run ${detects ? "detected" : "NOT detected"}`);

  // A credential already stored under the default reference survives a refused plain install (C2).
  const e = host("e");
  must("aify-env on e", sh(e, "cd /src/aify-env && npm install --no-audit --no-fund >/dev/null 2>&1 && npm install -g --no-audit --no-fund /src/aify-env >/dev/null 2>&1"));
  const ref = must("old key stored", sh(e, "aify-env credential set --service aify-comms --stdin", { input: OLD })).out.trim().split("\n").pop();
  const refused = sh(e, `cd /src/aify-comms && AIFY_API_KEY=$(cat /tmp/key) ${COMMS_ENTRY}`);
  save("no-early-store.txt", refused.out);
  const now = sh(e, `cat ~/.aify/credentials/${ref}`).out.replace(/\n$/, ""); // the store writes the value and one newline
  row("no-early-store", refused.status !== 0 && /--approve [0-9a-f]{64}/.test(refused.out) && now === OLD,
    `exit ${refused.status}; stored key under ${ref}: ${now === OLD ? "still OLD" : now === KEY ? "REPLACED by the new key" : "unreadable or other"}`);
  ok = results.every((r) => r.ok);
} catch (error) {
  log(`ERROR: ${error.message}`);
}
const clean = cleanup();
fs.writeFileSync(path.join(OUT, "results.json"), `${JSON.stringify({ run: RUN, envRef: ENV_REF, wrapperRef: WRAPPER_REF, results }, null, 1)}\n`);
for (const tar of ["aify-env", "aify-wrapper", "aify-comms"]) fs.rmSync(path.join(OUT, `${tar}.tar`), { force: true });
log(`terminal: ${ok && clean ? "exit 0" : "exit 1"}`);
fs.writeFileSync(path.join(OUT, "witness.txt"), `${lines.join("\n")}\n`);
process.exit(ok && clean ? 0 : 1);
