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
import { defaultMachineId } from "./runtimes.js";
import { checkApiExposure } from "./api-exposure-check.mjs";
import { resolveDoctorApiKey } from "./doctor-api-key.mjs";
import { markFor } from "./doctor-mark.mjs";
import { checkEnvProcesses } from "./env-processes-check.mjs";
import { checkContextWindow } from "./context-window-check.mjs";
import { checkSessionHandles } from "./session-handle-check.mjs";
import { checkGatewayOrphans } from "./gateway-orphan-check.mjs";
import { PORT_BASE, PORT_SPAN } from "./hermes-endpoint.js";
import { checkService } from "./service-check.mjs";
import {
  describeEnv,
  envIsOnline,
  envStateIsUnknown,
  bridgeCurrentVerdict,
  bridgeInstallVerdict,
  skillDestinations,
  skillsInstallVerdict,
  // THE SERVICE_RUNTIME_* PAIR MOVED to service-check.mjs with the check that spreads them, and
  // their warning went with them: the v0.5.4 dead-import sweep (3d4372a4) deleted both because its
  // detector ignored a name preceded by `.` and a spread's own dots made them read as unused. `node
  // --check` passed, the suite passed, and `aify-comms doctor` threw ReferenceError on its first line
  // of real work. Their absence here is now MEASURED -- zero uses in this file -- not assumed.
  BRIDGE_RUNTIME_EXCLUDE_PATHS,
  // Moved out of THIS file in v0.5.4 so they could be tested — see the note where they used to sit.
  readBoundAgentId,
  readProcEnv,
  managedOrphanVerdict,
  launcherDelegation,
  spawnDelegationVerdict,
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
//: Whether the service ANSWERED and refused us, as opposed to not answering at all. A doctor that
//: cannot tell those apart tells an operator to check whether the service is up while it is up and
//: rejecting every request -- which is what happened the day `API_KEY` was first set.
let serviceRefusedTheKey = false;
const get = async (path) => {
  try {
    // WITH THE KEY. This sent nothing until 2026-09-01, which was invisible while no key was set and
    // blinded every service-reading check the moment one was. See doctor-api-key.mjs.
    const headers = DOCTOR_API_KEY.key ? { "X-API-Key": DOCTOR_API_KEY.key } : {};
    const res = await fetch(`${SERVER_URL}${path}`, { headers, signal: AbortSignal.timeout(5000) });
    if (res.status === 401 || res.status === 403) {
      serviceRefusedTheKey = true;
      return null;
    }
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
};

/** Why the service produced nothing, in the operator's terms rather than the transport's. */
const whyNoService = () => (serviceRefusedTheKey
  ? (DOCTOR_API_KEY.key
    ? `the service REFUSED the API key (from ${DOCTOR_API_KEY.source}). It is running -- the key is wrong.`
    : "the service requires an API key and this doctor has none. Set API_KEY in .env, or export AIFY_API_KEY.")
  : "the service did not answer");

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
// RESOLVED AFTER THE REPO, because `.env` lives in it. Shell first: an operator who exported a key is
// pointing this run somewhere specific and a file in the checkout must not override that.
const DOCTOR_API_KEY = resolveDoctorApiKey({
  env: process.env, repoDir: repo ? repo.dir : "", readFile: (f) => readFileSync(f, "utf8"), join,
});

// ── 1. service container: is it serving the build you think it is? ──────────────────
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
    ? sh("git", ["rev-list", "--count", `${sha}..HEAD`, "--", "mcp/stdio",
        // Test-only commits change nothing the bridge executes, and this check's remedy is a
        // wrapper relaunch that reaps managed workers. See BRIDGE_RUNTIME_EXCLUDE_PATHS.
        ...BRIDGE_RUNTIME_EXCLUDE_PATHS.map((p) => `:(exclude)${p}`)], repo.dir)
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
  if (!envs) {
    skip("env-bridge", whyNoService());
    // AND bridge-current, for the SAME reason spelled out fifty lines below: a bare `return` here
    // took the rest of this function with it, so an unreachable service produced a report with no
    // `bridge-current` row at all -- not a skip, not a failure, absent. That was fixed once for the
    // no-online-bridge branch and left standing on this one, which is the branch that fires when the
    // service is down or refusing the key. A check that could not be asked must SAY it was not asked.
    return add("bridge-current", false, "unknown-all",
      `${whyNoService().replace(/\.$/, "")}, so no live bridge could be asked which build it is running.`,
      "Check the `service` row above, then re-run.");
  }
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
// the checkout, so editing a skill changes nothing for the fleet until it is re-run.
//
// BOTH MIRRORS, as of 2026-08-25. This walked `.claude/skills` only, and the repo keeps a SECOND
// tree — `.agents/skills`, byte-identical by test_skill_mirror_parity.py — which install.sh copies
// to $CODEX_HOME/skills and $HERMES_HOME/skills/autonomous-ai-agents. Neither destination was
// compared against anything, so a Codex or hermes agent could run a stale skill for ever with the
// check reporting green. A SKILL.md is loaded into every agent's context on every turn, so a stale
// one is wrong instructions paid for continuously — the failure this check exists for, on half the
// surface it was covering.
//
// A destination is checked only when its RUNTIME HOME exists. That is the honest line: no ~/.codex
// means Codex is not installed here and there is nothing to be stale. But if the home exists and
// the skills under it do not, that is a missing install and is reported — absence of the skills is
// a finding, absence of the runtime is not.

function checkSkillsInstalled() {
  if (!repo) return skip("skills-installed", "no repo checkout to compare against");
  const destinations = skillDestinations();
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
  for (const { src, dst, label } of destinations) {
    for (const name of ["aify-comms", "aify-comms-debug"]) {
      // The label rides on the relative path so a stale file names WHICH mirror it is stale in.
      walk(join(repo.dir, ...src, name), join(dst, name), `${label}:${name}`);
    }
  }
  const dest = destinations.map((d) => d.label).join(", ");
  const v = skillsInstallVerdict({ missing, differing, total, dest });
  return add("skills-installed", v.ok, v.code, v.detail, v.fix);
}

// ── run ──────────────────────────────────────────────────────────────────────────────
await checkService({ get, add, sh, repo, serverUrl: SERVER_URL });
// IS EVERYTHING aify-env RUNS ACCOUNTED FOR? The operator watched a live PTY in aify-env that the
// dashboard could not show, and asked for exactly this. Both reads it needs -- a terminal listing
// and a pid on each terminal -- landed in e426e497; before that the comparison was unanswerable.
await checkEnvProcesses({
  get,
  add,
  skip,
  fetchJson: async (url) => {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  },
  launcherText: installedLauncherText(),
  machineId: defaultMachineId(),
});
// CAN THESE AGENTS STILL ANSWER? Measured 2026-08-31: five managed hermes agents produced nothing
// for over two hours while status read `online`, `lastSeen` refreshed every few seconds and their
// dispatch runs reported `delivered`. They were reading their messages and starting work, then
// dying on a context window filled by conversations resuming since June. Nothing in the control
// plane could say so -- and the auto-mirrored failure notice lists four candidate causes without
// naming this one, so the single diagnosis readable off the screen was the one nobody looked for.
await checkContextWindow({ get, add, skip });
// IS ANY CONVERSATION CLAIMED BY TWO AGENTS? The ids are already unique; the failure is several
// agents pointing at ONE. Two live instances on 2026-08-31, found by hand hours apart and invisible
// to every status badge: a re-registered resident left a ghost row holding its session handle, so
// every message to that id was refused and relayed for hours; and four hermes agents shared one
// conversation, which is how a thread reaches 1.1M tokens. One read answers it for the whole fleet.
await checkSessionHandles({ get, add });
// IS THE FLEET LISTING OPEN, AND DOES IT HAND OUT CREDENTIALS WHEN IT IS? Measured on the operator's
// host 2026-08-29: 200 with no key, 200 with a wrong one, and 16 of 47 agent rows carrying a live
// gateway token. Neither half is a defect alone -- running without a key is a configuration, and the
// token is in the listing because the console link needs it -- so the check fires only on the
// combination, and reports rather than repairs: every way out is a decision with a cost.
//
// ITS OWN fetch, deliberately: `get` carries the configured API key, and asking with a key can only
// ever answer "yes, with a key".
await checkApiExposure({
  add,
  baseUrl: SERVER_URL,
  fetchJson: async (url) => {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
      // A 401 body is the GOOD answer here and must reach the verdict rather than being flattened to
      // null the way an unreachable service is.
      return await response.json();
    } catch {
      return null;
    }
  },
});
checkNativeBridge();
// WHERE MANAGED SPAWNS RUN, and whether that place is answering.
//
// Delegation makes aify-env required for spawning: the bridge REFUSES rather than falling back, which
// is right -- two spawners on one host is the collision the environment tier exists to end -- and it
// is invisible, because what an operator sees is spawns failing rather than a daemon that is down.
//
// The launcher is READ, never run: a bare `aify-comms` starts an environment bridge and supersedes the
// live one, which is how this fleet lost nine managed agents.
// Managed delivery loops running for agents that belong to no live bridge. READ-ONLY: it enumerates
// and names them, and never kills. See `managedOrphanVerdict` for why reporting is the whole job.
async function checkManagedOrphans() {
  // The enumerator is imported lazily so a host that cannot list processes fails HERE, with the
  // reason, rather than taking doctor's module load with it.
  let loops = null;
  try {
    const probes = await import("./proc-probes.js");
    const rows = probes.defaultListProcesses();
    // An EMPTY process table is not an empty answer. `defaultListProcesses` returns [] both when the
    // host genuinely has no processes -- impossible -- and when its enumeration failed, and that
    // conflation is exactly what hid a broken default for a whole release (`b57abc9e`). This process
    // is running, so a table that does not contain it did not read the host.
    if (!rows.some((row) => row && row.pid === process.pid)) throw new Error("enumeration did not include this process");
    loops = rows
      .map((row) => ({ agentId: probes.cmdlineDeliveryLoopAgent(row.commandLine), pid: row.pid }))
      .filter((row) => row.agentId);
  } catch {
    loops = null;
  }

  const agentsBody = await get("/api/v1/agents");
  const agents = agentsBody && agentsBody.agents && typeof agentsBody.agents === "object"
    ? agentsBody.agents
    : null;

  const envs = await get("/api/v1/environments");
  const online = (envs && envs.environments ? envs.environments : []).filter(envIsOnline);
  const liveBridgeId = online.length === 1 ? String(online[0].bridgeId || "") : "";

  const verdict = managedOrphanVerdict({ loops, agents, liveBridgeId });
  return add("managed-orphans", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}

/**
 * The installed environment-bridge launcher's text, or null.
 *
 * READ, NEVER RUN. Executing it would start an environment bridge, which supersedes the one
 * already serving this host and reaps its managed workers -- the incident of 2026-08-11, from a
 * four-second run meant only to confirm the launcher still started.
 *
 * Hoisted out of `checkSpawnDelegation` when a second check needed the same file: two readers
 * spelling the same two candidate paths is how they start disagreeing about which one wins.
 */
function installedLauncherText() {
  for (const candidate of [
    join(homedir(), ".local", "bin", "aify-comms"),
    join(homedir(), ".local", "bin", "aify-comms.cmd"),
  ]) {
    try {
      return readFileSync(candidate, "utf8");
    } catch {
      // Try the next spelling; an absent launcher is reported by the verdict, not thrown here.
    }
  }
  return null;
}

async function checkSpawnDelegation() {
  const launcherText = installedLauncherText();
  let endpointAnswered = null;
  // PARSED ONCE, by the module that also renders the verdict. These were two more copies of regexes
  // that already lived in doctor-predicates.js, and the copy here decides whether to PROBE while the
  // copy there decides the ANSWER -- so fixing this one alone would have bought a real probe and then
  // handed it to a verdict that ignored it and reported ok:true.
  const { on: delegating, endpoint } = launcherDelegation(launcherText);
  if (delegating && endpoint) {
    try {
      const response = await fetch(`${endpoint}/health`, { signal: AbortSignal.timeout(3000) });
      endpointAnswered = response.ok;
    } catch {
      endpointAnswered = false;
    }
  }
  const verdict = spawnDelegationVerdict({ launcherText, endpointAnswered });
  return add("spawn-delegation", verdict.ok, verdict.code, verdict.detail, verdict.fix);
}

// LAUNCHER AND TERMINAL QUESTIONS ARE NOT THIS TOOL'S.
//
// `wrappers`, `wrapper-current` and `runtimes` moved to aify-wrapper, where
// `aify-wrapper-check` already answered them -- a second implementation of one question does
// not agree for free, it agrees until one of them is fixed, and the copy that lived here is
// the one that carried a Windows bug aify-wrapper's never had.
//
// `bridge-terminal` moved to `aify-env doctor`, which owns whether this host can open a
// terminal. See docs/AIFY_ENV_BOUNDARY.md for the table that assigns them.
checkSkillsInstalled();
await checkSpawnDelegation();
await checkManagedOrphans();
// WHAT IS HOLDING HERMES' FILES. Its sibling above watches the DELIVERY LOOPS; nothing watched the
// per-agent GATEWAY HOSTS, and on 2026-08-31 the operator's `hermes update` refused to run, listing
// 45 live processes from this install after aify-env was killed. Establishing whether those were
// ours took a port range, a marker directory and a process walk -- a question this row now answers
// directly. It reports STATE and makes no claim about why a gateway lives as long as it does; that
// was investigated, three explanations were wrong, and the honest answer is still unknown.
//
// THE PROBES ARE IMPORTED LAZILY, exactly as `checkManagedOrphans` does above: a host that cannot
// enumerate processes must fail in THIS row, with the reason, rather than taking the doctor's module
// load down and silencing every other check with it.
const gatewayProbes = await import("./proc-probes.js").catch(() => null);
await checkGatewayOrphans({
  get,
  add,
  listProcesses: () => {
    if (!gatewayProbes) throw new Error("the process probes could not be loaded");
    return gatewayProbes.defaultListProcesses();
  },
  toPort: (line) => (gatewayProbes ? gatewayProbes.cmdlineHermesGatewayPort(line) : null),
  loopAgent: (line) => (gatewayProbes ? gatewayProbes.cmdlineDeliveryLoopAgent(line) : null),
  // The markers live beside the session markers, one file per agent holding its port.
  readPortMarkers: () => {
    const dir = process.env.TEMP || process.env.TMP || "/tmp";
    const markers = {};
    for (const name of readdirSync(dir)) {
      if (!name.startsWith("aify-hermes-port-")) continue;
      try {
        markers[name.slice("aify-hermes-port-".length)] = readFileSync(join(dir, name), "utf8").trim();
      } catch { /* a marker we cannot read is one we cannot attribute; skip it */ }
    }
    return markers;
  },
  // FROM THE MODULE THAT OWNS THE RANGE, never a second 8642 written here.
  base: PORT_BASE,
  span: PORT_SPAN,
});
checkRunningBridges();
await checkAgentIdentity();
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
    // `ok` IS CONSULTED BEFORE `partial`, and the old order made a FAILURE look like a note.
    //
    // `partial` says how much evidence was gathered; `ok` says what the answer was. They are
    // independent, and both combinations exist in this tool today:
    //
    //   * `doctor-predicates.js` returns {ok: true,  code: "partial"} -- some bridges reported a
    //     build, the rest are pre-B1. Genuinely benign, and `~` is right for it.
    //   * `context-window-check.mjs` returns {ok: false, code: "partial"} from `cappedVerdict`, whose
    //     own text reads "this row is not a clean result: the agent this check exists to find may be"
    //     among the ones not measured. That is a failure, and it rendered as the same `~`.
    //
    // Testing `partial` first meant the second case wore the first case's glyph. An operator scanning
    // a report for `✗` saw none, in a run that had failed to answer the question -- the exact shape of
    // `a2f9e42`'s false green, one layer up in the rendering rather than in the verdict.
    //
    // `skipped` stays first because a skip carries ok:true and is not a result at all.
    const mark = markFor(c);
    console.log(`  ${mark} ${c.id.padEnd(18)} ${c.detail}`);
    if (!c.ok && c.fix) console.log(`      → ${c.fix}`);
  }
  console.log("");
  console.log(failed.length ? `  ${failed.length} check(s) need attention.` : "  All checks passed.");
  console.log("");
}

process.exit(strict && failed.length ? 1 : 0);
