#!/usr/bin/env node
// aify-doctor — verify an install/update actually took effect.
//
// WHY THIS EXISTS. Every flow against this repo (install the client integration, install the
// service container, update either) fails the SAME way: SILENTLY. Nothing errors, everything
// looks installed, and the thing you changed is not the thing that is running:
//   * the container serves a build from before your last `docker compose up --build`;
//   * `~/.aify-comms` holds new bridge code but every RUNNING wrapper still executes the copy it
//     loaded at boot — so a fix "ships" and changes nothing;
//   * an agent launched without `--aify-agent` registers and messages perfectly while its status
//     is structurally dead;
//   * the OpenAI quota panel reads a token from a file nobody has.
// Each of those cost real hours. A person cannot see any of them; a check can.
//
// So every check here PROVES its claim against the running system — build stamps, process start
// times, process environments, a live API call — and none of them infer from "the file is there".
//
// Usage:
//   aify-doctor                # human-readable report
//   aify-doctor --json         # machine-readable, for an INSTALLING AGENT to parse
//   aify-doctor --strict       # exit 1 if any check failed (default always exits 0)
//   aify-doctor --repo <path>  # compare against a specific checkout (else: cwd, else skipped)

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { checkOpenAiUsageAccess } from "./usage-collector.js";
// Pure env predicates live in their own module so they can be unit-tested — this script runs its
// checks at import and ends in process.exit(), so nothing here is importable by a test. See
// doctor-predicates.js for why (two shipped false greens, zero coverage).
import {
  describeEnv,
  envIsOnline,
  envStateIsUnknown,
} from "./doctor-predicates.js";

const args = process.argv.slice(2);
const asJson = args.includes("--json");
const strict = args.includes("--strict");
const repoArg = (() => {
  const i = args.indexOf("--repo");
  return i >= 0 ? args[i + 1] : "";
})();

const AIFY_HOME = process.env.AIFY_HOME || join(homedir(), ".aify-comms");
const BRIDGE_DIR = join(AIFY_HOME, "mcp", "stdio");
const SERVER_URL = (process.env.AIFY_COMMS_URL || process.env.AIFY_SERVER_URL || "http://localhost:8800").replace(/\/$/, "");

const checks = [];
const add = (id, ok, code, detail, fix = "") => checks.push({ id, ok, code, detail, ...(fix ? { fix } : {}) });
const skip = (id, detail) => checks.push({ id, ok: true, code: "skipped", detail });

const sh = (cmd, cmdArgs, cwd) => {
  try { return execFileSync(cmd, cmdArgs, { cwd: cwd || undefined, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim(); }
  catch { return ""; }
};
const get = async (path) => {
  try {
    const res = await fetch(`${SERVER_URL}${path}`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
};

// ── the checkout we are comparing against (the only source of "should be running") ──
function findRepo() {
  const candidates = [repoArg, process.env.AIFY_REPO, process.cwd(), join(dirname(fileURLToPath(import.meta.url)), "..", "..")];
  for (const c of candidates) {
    if (!c) continue;
    if (existsSync(join(c, "install.sh")) && existsSync(join(c, ".git"))) {
      const sha = sh("git", ["rev-parse", "HEAD"], c);
      if (sha) return { dir: c, sha, short: sha.slice(0, 7) };
    }
  }
  return null;
}
const repo = findRepo();

// ── 1. service container: is it serving the build you think it is? ──────────────────
async function checkService() {
  const health = await get("/health");
  if (!health) {
    return add("service", false, "unreachable",
      `No healthy service at ${SERVER_URL}.`,
      "Start it: `docker compose up -d --build` (in the repo), then re-run.");
  }
  const ver = await get("/version");
  const sha = String(ver?.sha || "");
  if (!repo) {
    return add("service", true, "ok", `healthy — build ${ver?.sha_short || "?"} (no checkout to compare against)`);
  }
  if (!sha) {
    return add("service", false, "unknown-build", "healthy, but it reports no build sha.",
      "Run `scripts/stamp.sh` before `docker compose up -d --build` — otherwise /version lies.");
  }
  if (sha === repo.sha) {
    return add("service", true, "ok", `healthy — build ${ver.sha_short} == repo HEAD`);
  }
  const behind = sh("git", ["rev-list", "--count", `${sha}..HEAD`], repo.dir);
  return add("service", false, "stale",
    `serving build ${ver.sha_short}, repo HEAD is ${repo.short}${behind ? ` (${behind} commit(s) behind)` : ""}.`,
    "Your service changes are NOT running. Rebuild: `scripts/stamp.sh && docker compose up -d --build`.");
}

// ── 2. the installed bridge copy: does it match the checkout? ───────────────────────
function checkNativeBridge() {
  const marker = join(AIFY_HOME, ".aify-version");
  if (!existsSync(BRIDGE_DIR)) {
    return add("bridge-installed", false, "missing", `No bridge at ${BRIDGE_DIR}.`,
      "Run `bash install.sh --client <claude|codex|hermes>`.");
  }
  if (!existsSync(marker)) {
    return add("bridge-installed", false, "unknown-version", "Bridge present but has no version marker.",
      "Re-run install.sh to stamp it.");
  }
  const sha = (readFileSync(marker, "utf8").match(/^sha=(\S+)/m) || [])[1] || "";
  if (!repo) return add("bridge-installed", true, "ok", `installed — ${sha.slice(0, 7)} (no checkout to compare against)`);
  if (sha === repo.sha) return add("bridge-installed", true, "ok", `installed — ${sha.slice(0, 7)} == repo HEAD`);
  const behind = sh("git", ["rev-list", "--count", `${sha}..HEAD`], repo.dir);
  return add("bridge-installed", false, "stale",
    `installed copy is ${sha.slice(0, 7)}, repo HEAD is ${repo.short}${behind ? ` (${behind} commit(s) behind)` : ""}.`,
    "Re-run `bash install.sh --client <runtime>` — bridge edits do NOT take effect from the checkout.");
}

function checkBridgeTerminal() {
  try {
    execFileSync(process.execPath, ["-e", "require('node-pty')"], {
      cwd: BRIDGE_DIR,
      stdio: "ignore",
    });
    return add("bridge-terminal", true, "ok", "installed node-pty native module loads");
  } catch {
    return add("bridge-terminal", false, "unloadable", "installed node-pty native module does not load; terminal-backed runtimes cannot start.",
      "Run `cd ~/.aify-comms/mcp/stdio && npm rebuild node-pty`, then restart the environment bridge.");
  }
}

// ── 3. RUNNING bridges: a process keeps the code it loaded at boot ──────────────────
// This is the check that would have saved the most time. A fix can be committed, installed, and
// still not be running anywhere — because every live wrapper is executing the copy it read at
// startup. Compare each bridge process's start time against the install marker;
// mirrored source files preserve their old mtimes and cannot prove install time.
function checkRunningBridges() {
  if (process.platform !== "linux") return skip("bridge-running", "process inspection is Linux-only");
  const marker = join(AIFY_HOME, ".aify-version");
  if (!existsSync(marker)) return skip("bridge-running", "no install marker to compare against");
  const installedAtMs = statSync(join(AIFY_HOME, ".aify-version")).mtimeMs;
  const stale = [];
  let running = 0;
  for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
    let cmdline = "";
    try { cmdline = readFileSync(`/proc/${pid}/cmdline`, "utf8"); } catch { continue; }
    if (!cmdline.includes("mcp/stdio/server.js")) continue;
    running += 1;
    let startedMs;
    try { startedMs = statSync(`/proc/${pid}`).mtimeMs; } catch { continue; }
    if (startedMs < installedAtMs) {
      const env = readProcEnv(pid);
      stale.push(env.AIFY_AGENT_ID || `pid ${pid}`);
    }
  }
  if (!running) return add("bridge-running", true, "none", "no bridge processes are running");
  if (!stale.length) return add("bridge-running", true, "ok", `${running} bridge process(es), all started after the last install`);
  return add("bridge-running", false, "stale",
    `${stale.length} of ${running} bridge process(es) started BEFORE the last install, so they are still running the OLD code: ${stale.join(", ")}.`,
    "Restart those sessions/agents — installing does not reload a running bridge.");
}

// The agent binding comms_register writes, keyed by the CLAUDE pid (the bridge's parent).
function readBoundAgentId(bridgePid) {
  let ppid = "";
  try { ppid = (readFileSync(`/proc/${bridgePid}/stat`, "utf8").split(" ")[3] || "").trim(); } catch { return ""; }
  const tmp = process.env.TMPDIR || process.env.TEMP || "/tmp";
  for (const pid of [ppid, String(bridgePid)]) {
    if (!pid) continue;
    try {
      const raw = readFileSync(join(tmp, `aify-agent-${pid}`), "utf8").trim();
      if (!raw) continue;
      const id = raw.startsWith("{") ? String(JSON.parse(raw).agentId || "") : raw;
      if (id) return id;
    } catch { /* no binding for this pid */ }
  }
  return "";
}

function readProcEnv(pid) {
  const out = {};
  try {
    for (const kv of readFileSync(`/proc/${pid}/environ`, "utf8").split("\0")) {
      const i = kv.indexOf("=");
      if (i > 0) out[kv.slice(0, i)] = kv.slice(i + 1);
    }
  } catch { /* process gone / not ours */ }
  return out;
}

// ── 4. anonymous agent sessions: registered, but with no identity in the process ─────
// An agent launched without --aify-agent has NO AIFY_AGENT_ID, which silently disables every
// turn-state path: it registers, messages and heartbeats perfectly while its status latches
// forever. Invisible from the database — you must read the process environment.
async function checkAgentIdentity() {
  if (process.platform !== "linux") return skip("agent-identity", "process inspection is Linux-only");
  const agents = await get("/api/v1/agents");
  if (!agents) return skip("agent-identity", "service unreachable");
  const named = new Set();
  const broken = [];   // REGISTERED as an agent, but the process has no identity -> status is dead
  let plain = 0;       // a plain claude+comms session that never registered -> legitimately id-less
  for (const pid of readdirSync("/proc").filter((d) => /^\d+$/.test(d))) {
    let cmdline = "";
    try { cmdline = readFileSync(`/proc/${pid}/cmdline`, "utf8"); } catch { continue; }
    if (!cmdline.includes("mcp/stdio/server.js")) continue;
    if (cmdline.includes("--environment-bridge")) continue; // legitimately id-less
    const env = readProcEnv(pid);
    if (env.AIFY_AGENT_ID) { named.add(env.AIFY_AGENT_ID); continue; }
    // Anonymous. Did this session nonetheless REGISTER as an agent? comms_register writes a
    // binding file keyed by the claude pid — that is the difference between "an agent whose
    // status is silently dead" and "a plain session that simply isn't an agent". Do not cry wolf.
    const bound = readBoundAgentId(pid);
    if (bound) broken.push(`${bound} (pid ${pid})`);
    else plain += 1;
  }
  const note = plain ? ` (${plain} unregistered session(s) are legitimately id-less)` : "";
  if (!broken.length) {
    return add("agent-identity", true, "ok", `${named.size} agent session(s), all carry an agent id${note}`);
  }
  return add("agent-identity", false, "anonymous",
    `${broken.length} REGISTERED agent(s) are running with NO AIFY_AGENT_ID: ${broken.join(", ")}. They register, message and heartbeat fine, but their status is structurally dead — nothing can ever clear 'working'.${note}`,
    "Relaunch each with `--aify-agent <id>` (add `--resume <handle>` to keep its conversation).");
}

// ── 5/6. wrappers + runtimes actually on PATH ────────────────────────────────────────
function checkWrappers() {
  const found = ["claude-aify", "codex-aify", "hermes-aify", "aify-comms"].filter((w) => sh("command", ["-v", w]) || sh("which", [w]));
  if (!found.length) {
    return add("wrappers", false, "missing", "no aify wrappers on PATH.",
      "Run install.sh, and make sure ~/.local/bin is on PATH.");
  }
  add("wrappers", true, "ok", `on PATH: ${found.join(", ")}`);
}

function checkRuntimes() {
  const rt = ["claude", "codex", "hermes"].filter((r) => sh("which", [r]));
  add("runtimes", true, "ok", rt.length ? `installed: ${rt.join(", ")}` : "none of claude/codex/hermes found on PATH");
}

// ── 7. environment bridge: is one actually connected? ────────────────────────────────
// REGISTERED IS NOT CONNECTED (fixed 2026-07-26). This counted rows in /environments and
// reported every row as "connected", so it stayed green forever: an environment row is never
// deleted, it just goes stale. Observed live — "✓ 2 connected" while BOTH rows read
// status='offline', one stale by 24h and the other by ~7 weeks, i.e. zero bridges alive and no
// managed spawn possible. That is precisely the false green aify-doctor exists to prevent, on
// the one check that is supposed to prove managed spawns can run. The server already derives
// liveness per row, so trust ITS `status` and name the dead ones instead of hiding them.
// The REAL environments.status vocabulary, read off the service rather than guessed:
//   online | degraded | offline   — accepted from a bridge registration (api_v2.py:10105)
//   forgotten | disabled          — set server-side (api_v2.py:10496 / :10529)
// `degraded` still heartbeats and can host spawns, so it counts as connected; the other three
// cannot. An unknown/absent value is treated as NOT connected — this check exists to fail loudly,
// so the unknown case must never be the optimistic one.
// (First cut of this fix keyed on an INVENTED set {online,connected,ready,active}: three values
// the service never emits, while omitting the real `degraded` — which would have reported a live
// degraded bridge as "none online", a false RED. Verified against api_v2.py before rewriting.)
async function checkEnvBridge() {
  const envs = await get("/api/v1/environments");
  if (!envs) return skip("env-bridge", "service unreachable");
  const list = envs.environments || [];
  const online = list.filter(envIsOnline);
  const offline = list.filter((e) => !envIsOnline(e));
  if (!online.length) {
    const detail = list.length
      ? `No environment bridge is ONLINE — dashboard-managed spawns cannot run. ${list.length} registered but not connected: ${offline.map(describeEnv).join(", ")}`
      : "No environment bridge is registered — dashboard-managed spawns cannot run.";
    return add("env-bridge", false, "none", detail, "Start one on the host: `aify-comms`.");
  }
  const unknown = list.filter(envStateIsUnknown);
  const detail = `${online.length} online: ${online.map((e) => e.id).join(", ")}`
    + (offline.length ? ` (${offline.length} registered but cannot host a spawn: ${offline.map(describeEnv).join(", ")})` : "")
    + (unknown.length ? ` — WARNING: unrecognised status on ${unknown.map(describeEnv).join(", ")}; doctor's state vocabulary may be stale` : "");
  add("env-bridge", true, "ok", detail);
}

// ── 8. OpenAI usage (proves the connection, not the file) ────────────────────────────
async function checkUsage() {
  const r = await checkOpenAiUsageAccess().catch((e) => ({ ok: false, code: "error", message: String(e), detail: "" }));
  if (r.ok) return add("usage-openai", true, "ok", "OpenAI/ChatGPT quota is connected");
  add("usage-openai", false, r.code, r.message, r.detail);
}

// ── run ──────────────────────────────────────────────────────────────────────────────
await checkService();
checkNativeBridge();
checkBridgeTerminal();
checkRunningBridges();
await checkAgentIdentity();
checkWrappers();
checkRuntimes();
await checkEnvBridge();
await checkUsage();

const failed = checks.filter((c) => !c.ok);
const result = {
  ok: failed.length === 0,
  repo: repo ? { dir: repo.dir, head: repo.short } : null,
  service_url: SERVER_URL,
  checks,
};

if (asJson) {
  console.log(JSON.stringify(result, null, 2));
} else {
  console.log("");
  console.log(`aify-doctor — service ${SERVER_URL}${repo ? `, repo HEAD ${repo.short}` : " (no checkout to compare against)"}`);
  console.log("");
  for (const c of checks) {
    const mark = c.code === "skipped" ? "–" : c.ok ? "✓" : "✗";
    console.log(`  ${mark} ${c.id.padEnd(18)} ${c.detail}`);
    if (!c.ok && c.fix) console.log(`      → ${c.fix}`);
  }
  console.log("");
  console.log(failed.length ? `  ${failed.length} check(s) need attention.` : "  All checks passed.");
  console.log("");
}

process.exit(strict && failed.length ? 1 : 0);
