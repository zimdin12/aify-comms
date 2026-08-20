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
  bridgeCurrentVerdict,
  bridgeInstallVerdict,
  serviceBuildVerdict,
  skillsInstallVerdict,
  // USED IN A SPREAD, at `...SERVICE_RUNTIME_PATHS` / `...SERVICE_RUNTIME_EXCLUDE_PATHS` below.
  // The v0.5.4 dead-import sweep (3d4372a4) deleted both — its detector excluded a name preceded by
  // `.`, so that `obj.name` would not look like a use, and a spread's own dots made these read as
  // unused. `node --check` parses fine, the bridge suite never calls `checkService`, and JS has no
  // undefined-name sweep, so `aify-comms doctor` threw `ReferenceError: SERVICE_RUNTIME_PATHS is not
  // defined` on its FIRST line of real work — found only by running it against a real deploy.
  SERVICE_RUNTIME_PATHS,
  SERVICE_RUNTIME_EXCLUDE_PATHS,
  // Moved out of THIS file in v0.5.4 so they could be tested — see the note where they used to sit.
  readBoundAgentId,
  readProcEnv,
  versionToCompareWrappersAgainst,
  wrapperVersionVerdict,
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
  // Ask whether any commit since the build touched code the service EXECUTES, not merely
  // whether the sha differs — see serviceBuildVerdict for the false red this replaces
  // (a docs-only commit reported "Your service changes are NOT running") and for why the set
  // is runtime paths rather than Dockerfile COPY sources. Same shape as checkNativeBridge
  // below: two `git log` calls, pure unit-tested verdict.
  const totalCommits = sh("git", ["rev-list", "--count", `${sha}..HEAD`], repo.dir);
  const runtimeCommits = sh(
    "git",
    [
      "rev-list", "--count", `${sha}..HEAD`, "--",
      ...SERVICE_RUNTIME_PATHS,
      // Tests live under a runtime path but are not runtime — nothing in the image runs pytest.
      ...SERVICE_RUNTIME_EXCLUDE_PATHS.map((p) => `:(exclude)${p}`),
    ],
    repo.dir,
  );
  const verdict = serviceBuildVerdict({
    builtSha: sha,
    builtShort: ver.sha_short || "",
    headSha: repo.sha,
    headShort: repo.short,
    runtimeCommits: Number(runtimeCommits || 0),
    totalCommits: Number(totalCommits || 0),
  });
  return add("service", verdict.ok, verdict.code, verdict.detail, verdict.fix);
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
  // N13: ask whether any commit since the marker TOUCHED the bridge, not merely whether the sha
  // differs from HEAD — see bridgeInstallVerdict. The two `git log` calls are the only I/O; the
  // verdict itself is pure and unit-tested.
  const totalCommits = repo ? sh("git", ["rev-list", "--count", `${sha}..HEAD`], repo.dir) : "";
  const bridgeCommits = repo
    ? sh("git", ["rev-list", "--count", `${sha}..HEAD`, "--", "mcp/stdio"], repo.dir)
    : "";
  const verdict = bridgeInstallVerdict({
    installedSha: sha,
    headSha: repo ? repo.sha : "",
    headShort: repo ? repo.short : "",
    bridgeCommits: Number(bridgeCommits || 0),
    totalCommits: Number(totalCommits || 0),
  });
  return add("bridge-installed", verdict.ok, verdict.code, verdict.detail, verdict.fix);
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

// `readBoundAgentId` and `readProcEnv` moved to ./doctor-predicates.js in v0.5.4, byte-identical apart
// from an injectable `/proc` root. Same reason the env-bridge predicates went first: this file runs
// every check at import and ends in `process.exit()`, so nothing declared here can be reached by a
// test. A V8-coverage census of the bridge suite found both with a zero call count, which for the two
// readers behind the `agent-identity` check is the same shape as the false greens that moved the
// others.

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

// ── 6b. wrapper-current: is the launcher on PATH the build this checkout describes? ──
//
// The replacement for a guarantee v0.6 gives up. install.sh guarantees wrapper and bridge are the same
// build by doing both in one step; publishing the wrappers separately ends that by construction, and
// `bridge-installed` / `bridge-current` exist BECAUSE that guarantee kept being violated silently.
//
// READS, NEVER RUNS. `claude-aify --check` would be the obvious way to ask, and it is unsafe here: a
// wrapper installed before the contract does not know that flag and forwards it to the runtime, so
// doctor would LAUNCH CLAUDE on a live machine to find out whether claude-aify was current.
function checkWrapperVersions() {
  // aify-wrapper's VERSION, not aify-comms'. The marker in a launcher is stamped from the package
  // that rendered it, so the package is the only number it can honestly be measured against. Reading
  // the repo root compared two unrelated counters that happened to agree on 2026-08-20; the first
  // independent release on either side would have turned every clean install STALE.
  //
  // The CHECKOUT's copy first, then the installed bridge's. The check says REINSTALL, and a reinstall
  // runs install.sh from the checkout and renders from the checkout's node_modules -- so that is the
  // version a launcher would get. The installed copy is the fallback for a host with no checkout at
  // all; preferring it would compare against what the LAST install used, which is precisely the
  // number a staleness check must not treat as current.
  //
  // Neither readable gives "" and the verdict is unknown-all -- a fail that says it verified nothing,
  // rather than a confident comparison against the wrong number.
  const wrapperPackageVersion = (() => {
    const roots = [repo ? join(repo.dir, "mcp", "stdio") : null, BRIDGE_DIR].filter(Boolean);
    for (const root of roots) {
      try {
        return readFileSync(join(root, "node_modules", "aify-wrapper", "VERSION"), "utf8").trim();
      } catch { /* try the next root */ }
    }
    return "";
  })();
  const repoVersion = versionToCompareWrappersAgainst({
    wrapperPackageVersion,
    serviceVersion: repo ? (() => {
      try { return readFileSync(join(repo.dir, "VERSION"), "utf8").trim(); } catch { return ""; }
    })() : "",
  });

  const wrappers = [];
  for (const name of ["claude-aify", "codex-aify", "hermes-aify"]) {
    const resolved = sh("command", ["-v", name]) || sh("which", [name]);
    if (!resolved) continue;
    let version = null;
    try {
      const text = readFileSync(resolved.trim(), "utf8");
      const m = text.match(/HARNESS_WRAPPER_VERSION="([^"]*)"/);
      if (m && m[1]) version = m[1];
    } catch { /* unreadable — treated as no marker, i.e. stale */ }
    wrappers.push({ name, version });
  }

  const verdict = wrapperVersionVerdict({ repoVersion, wrappers });
  add("wrapper-current", verdict.ok, verdict.code, verdict.detail, verdict.fix);
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
//   online | degraded | offline   — accepted from a bridge registration
//   forgotten | disabled          — set server-side
// (Both were cited by line into api_v2.py when this was written. That file is now 53 lines of
// include_router calls, so those citations resolve to nothing; the live readers of this vocabulary
// are service/env_status.py and the `{"online", "degraded"}` gates across service/api_core/.)
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
    add("env-bridge", false, "none", detail, "Start one on the host: `aify-comms`.");
    // AND STILL REPORT bridge-current, which used to VANISH here. This `return` took the whole
    // rest of the function with it, so whenever no env bridge was online the report simply had no
    // `bridge-current` row — not a skip, not a failure, absent. An operator counting checks saw ten
    // and no sign that the eleventh question went unasked.
    //
    // It matters most exactly here. On Windows `bridge-running` and `agent-identity` both skip
    // (they read /proc), so `bridge-current` is the ONLY check that answers "are the live bridges
    // running current code" — and it disappeared precisely when the fleet was down, which is when
    // an operator is most likely to be reading this report.
    //
    // Same family as `a2f9e42`'s `unknown-all`: a check that could not gather evidence must say so.
    // `bridgeCurrentVerdict` already returns a proper skip for "no live environment bridge to
    // check", so reporting it needs nothing but not returning early.
    const noLiveBridge = bridgeCurrentVerdict({
      environments: list,
      headSha: repo ? repo.sha : "",
      headShort: repo ? repo.short : "",
      bridgeCommitsSince: {},
    });
    return add(
      "bridge-current",
      noLiveBridge.ok,
      noLiveBridge.code,
      noLiveBridge.detail,
      noLiveBridge.fix,
    );
  }
  // B1: are the LIVE bridges running current code? bridge-installed only proves the files on
  // disk; a process keeps what it loaded at boot, and bridge-running (which would catch that)
  // skips on Windows. See bridgeCurrentVerdict.
  // Ask the same question bridge-installed asks of the files on disk (N13): did any commit since
  // this bridge's build actually TOUCH `mcp/stdio`? Without it a docs-only commit made every live
  // bridge read STALE and told the operator to restart it — a wrong instruction, and after the
  // 2026-08-11 outage the last one to give lightly. An empty/failed count is left OUT of the map
  // so the verdict falls through to stale: unanswerable is not clean.
  const bridgeCommitsSince = {};
  for (const env of list) {
    const build = String(env?.metadata?.bridgeBuild || "").trim();
    if (!build || bridgeCommitsSince[build] !== undefined || !repo) continue;
    const n = sh("git", ["rev-list", "--count", `${build}..HEAD`, "--", "mcp/stdio"], repo.dir);
    if (n !== "" && Number.isFinite(Number(n))) bridgeCommitsSince[build] = Number(n);
  }
  const current = bridgeCurrentVerdict({
    environments: list,
    headSha: repo ? repo.sha : "",
    headShort: repo ? repo.short : "",
    bridgeCommitsSince,
  });
  add("bridge-current", current.ok, current.code, current.detail, current.fix);
  const unknown = list.filter(envStateIsUnknown);
  const detail = `${online.length} online: ${online.map((e) => e.id).join(", ")}`
    + (offline.length ? ` (${offline.length} registered but cannot host a spawn: ${offline.map(describeEnv).join(", ")})` : "")
    + (unknown.length ? ` — WARNING: unrecognised status on ${unknown.map(describeEnv).join(", ")}; doctor's state vocabulary may be stale` : "");
  add("env-bridge", true, "ok", detail);
}

// ── 8. OpenAI usage (proves the connection, not the file) ────────────────────────────
async function checkUsage() {
  const r = await checkOpenAiUsageAccess().catch((e) => ({ ok: false, code: "error", message: String(e), detail: "" }));
  // Not every OK is the same OK. `stale-token` is green ON PURPOSE (the login self-heals, see
  // openAiUsageVerdict) but it carries information the operator wants — the quota panel may read
  // stale until codex renews. Collapsing every ok to "connected" threw that away, so the one
  // message this fix exists to deliver never reached a human (reviewer catch, 2026-08-09).
  if (r.ok) {
    const code = r.code || "ok";
    const detail = code === "ok" ? "OpenAI/ChatGPT quota is connected" : r.message;
    return add("usage-openai", true, code, detail, r.detail || "");
  }
  add("usage-openai", false, r.code, r.message, r.detail);
}

// Skills are a deploy path too — see skillsInstallVerdict. install.sh copies the skill trees out of
// the checkout, so editing .claude/skills/ changes nothing for the fleet until it is re-run.
function checkSkillsInstalled() {
  if (!repo) return skip("skills-installed", "no repo checkout to compare against");
  const dest = join(homedir(), ".claude", "skills");
  const missing = [];
  const differing = [];
  let total = 0;
  const walk = (srcDir, dstDir, rel) => {
    if (!existsSync(srcDir)) return;
    for (const entry of readdirSync(srcDir)) {
      const src = join(srcDir, entry);
      const dst = join(dstDir, entry);
      const here = rel ? `${rel}/${entry}` : entry;
      if (statSync(src).isDirectory()) { walk(src, dst, here); continue; }
      total += 1;
      if (!existsSync(dst)) { missing.push(here); continue; }
      try {
        if (readFileSync(src, "utf8") !== readFileSync(dst, "utf8")) differing.push(here);
      } catch { differing.push(here); }
    }
  };
  for (const name of ["aify-comms", "aify-comms-debug"]) {
    walk(join(repo.dir, ".claude", "skills", name), join(dest, name), name);
  }
  const v = skillsInstallVerdict({ missing, differing, total, dest });
  return add("skills-installed", v.ok, v.code, v.detail, v.fix);
}

// ── run ──────────────────────────────────────────────────────────────────────────────
await checkService();
checkNativeBridge();
checkBridgeTerminal();
checkSkillsInstalled();
checkRunningBridges();
await checkAgentIdentity();
checkWrappers();
checkWrapperVersions();
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
    // `partial` gets its own mark. It is OK — it must not fail --strict — but a plain ✓ beside
    // "0 live bridge(s) match repo HEAD" reads as a clean pass when the check is actually saying
    // it cannot tell yet. Same lesson as the stale-token verdict whose message doctor used to
    // collapse into "connected": a check must not look more confident than it is.
    const mark = c.code === "skipped" ? "–" : c.code === "partial" ? "~" : c.ok ? "✓" : "✗";
    console.log(`  ${mark} ${c.id.padEnd(18)} ${c.detail}`);
    if (!c.ok && c.fix) console.log(`      → ${c.fix}`);
  }
  console.log("");
  console.log(failed.length ? `  ${failed.length} check(s) need attention.` : "  All checks passed.");
  console.log("");
}

process.exit(strict && failed.length ? 1 : 0);
