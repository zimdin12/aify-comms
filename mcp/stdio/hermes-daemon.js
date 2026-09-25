#!/usr/bin/env node
// Stop a per-agent hermes gateway and collect what a previous generation left.
//
// `stopDaemon` is the kill-prior step: the hermes-aify launcher runs `hermes-daemon-cli.js stop <agent>`
// before it starts a new gateway, and the managed delivery loop tears its gateway down by port with
// `defaultKillByPort`. Every kill here first confirms the target looks like hermes, because a stale port
// or pid marker can name a process the OS has since handed to something unrelated.
//
// The api_server `ensureDaemon` path that used to live here was retired on the wrapper side on
// 2026-06-02: nothing starts an api_server daemon any more, so nothing here ensures one.

import { execFile as nodeExecFile, spawnSync as nodeSpawnSync } from "node:child_process";
import { PS_UTF8_PRELUDE } from "./win32-text.js";
import { promisify } from "node:util";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { agentEndpoint, claimedByOtherAgents, clearGatewayMarkers as defaultClearGatewayMarkers } from "./hermes-endpoint.js";
import { terminateProcessTree } from "./runtimes.js";
import { reapPriorHermes } from "./hermes-prior-reap.mjs";
// The filename sanitiser has ONE owner (`hermes-endpoint.js`); this module carried a
// byte-identical copy until v0.5.4. Three copies of a function that turns an agent id into a
// PATH is three chances for the same agent to get two different files.
import { sanitizeAgentId } from "./hermes-endpoint.js";

const execFile = promisify(nodeExecFile);

// --- cmdline cross-check (anti-overkill under OS pid/port reuse) ------------
// SAFETY: a stale `aify-hermes-port-<agent>` / `aify-hermes-daemon-pid-<agent>`
// marker can outlive the daemon. The OS may then free that port/pid and hand it
// to an UNRELATED operator process (a dev server, an editor, anything). Killing
// it by the stale marker alone would take down the operator's own process. Before
// any port- or tracked-pid kill we therefore confirm the target's image/cmdline
// actually belongs to a hermes daemon. Mirrors reap-managed-claude.js's
// parentBelongsToAgent / defaultGetCmdline cmdline verification.

// Get the command line of a pid. Injectable. Returns "" when unknown.
//   - win32: PowerShell Get-CimInstance Win32_Process (CommandLine).
//   - posix: `ps -o args= -p <pid>`.
export function defaultGetCmdline(pid, spawnSync = nodeSpawnSync) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return "";
  try {
    if (process.platform === "win32") {
      // PS_UTF8_PRELUDE: cmdline/path fields feed parentBelongsToAgent path
      // matching; OEM-encoded output mangles non-ASCII profile paths.
      const ps =
        PS_UTF8_PRELUDE +
        `$p = Get-CimInstance Win32_Process -Filter "ProcessId=${n}" -ErrorAction SilentlyContinue;` +
        `if ($p) { "$($p.CommandLine)\`t$($p.ExecutablePath)\`t$($p.Name)" }`;
      const res = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], {
        encoding: "utf8", windowsHide: true, timeout: 5000,
      });
      return String(res.stdout || "").trim();
    }
    const res = spawnSync("ps", ["-o", "args=", "-p", String(n)], { encoding: "utf8", timeout: 5000 });
    return String(res.stdout || "").trim();
  } catch {
    return "";
  }
}

// Does this image/path/cmdline string belong to a hermes daemon? Case-insensitive
// substring match on "hermes" — the listener/tracked-pid must be a hermes process,
// never an unrelated operator process that recycled the same port/pid. Returns
// false for empty/unknown input → fail-safe (no confirmation ⇒ no kill).
export function looksLikeHermesProcess(cmdline) {
  return /hermes/i.test(String(cmdline || ""));
}

// --- per-agent daemon pid tracking -----------------------------------------
// The retired api_server `ensureDaemon` persisted its daemon's pid in a file beside the per-agent
// port/key files (same tempDir + sanitizeAgentId convention as hermes-endpoint.js). Nothing writes one
// in production any more; `stopDaemon` still reads and clears a leftover so a stray daemon from that
// era is collected, and `writeDaemonPid` is how the tests seed one.

function daemonPidFile(agentId, tempDir, fsImpl) {
  return path.join(tempDir || os.tmpdir(), `aify-hermes-daemon-pid-${sanitizeAgentId(agentId)}`);
}

// Read the persisted daemon pid for an agent, or undefined. Never throws.
export function readDaemonPid(agentId, tempDir, { fs: fsImpl = fs } = {}) {
  if (!agentId) return undefined;
  try {
    const raw = String(fsImpl.readFileSync(daemonPidFile(agentId, tempDir), "utf8")).trim();
    const pid = parseInt(raw, 10);
    return Number.isInteger(pid) && pid > 0 ? pid : undefined;
  } catch {
    return undefined;
  }
}

// Persist the daemon pid for an agent. Best-effort; never throws.
export function writeDaemonPid(agentId, pid, tempDir, { fs: fsImpl = fs } = {}) {
  const n = Number(pid);
  if (!agentId || !Number.isInteger(n) || n <= 0) return false;
  try {
    fsImpl.writeFileSync(daemonPidFile(agentId, tempDir), String(n));
    return true;
  } catch {
    return false;
  }
}

// Remove the persisted daemon pid for an agent. Best-effort; never throws.
export function clearDaemonPid(agentId, tempDir, { fs: fsImpl = fs } = {}) {
  if (!agentId) return false;
  try {
    fsImpl.rmSync(daemonPidFile(agentId, tempDir), { force: true });
    return true;
  } catch {
    try {
      fsImpl.unlinkSync(daemonPidFile(agentId, tempDir));
      return true;
    } catch {
      return false;
    }
  }
}

// Is a pid alive right now? Best-effort, signal-0 probe. Never throws.
function defaultIsAlive(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    process.kill(n, 0);
    return true;
  } catch (err) {
    // EPERM = exists but not ours to signal → still alive.
    return err && err.code === "EPERM";
  }
}

// Default tree-killer keyed on a raw pid. Wraps terminateProcessTree (which
// takes a {pid} handle). Never throws.
function defaultKillTree(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    terminateProcessTree({ pid: n }, "SIGKILL");
    return true;
  } catch {
    return false;
  }
}

// Resolve the PID(s) LISTENING on `port`. Platform-aware: Windows uses
// PowerShell's Get-NetTCPConnection (→ OwningProcess); POSIX uses lsof. Returns
// an array of pids (possibly empty). Never throws → [] on any failure. Injectable
// so killByPort's listener resolution is testable without binding a real socket.
export async function defaultResolveListenerPids(port) {
  if (!port) return [];
  try {
    if (process.platform === "win32") {
      const ps =
        `$ErrorActionPreference='SilentlyContinue';` +
        `$c = Get-NetTCPConnection -LocalPort ${Number(port)} -State Listen;` +
        `if (-not $c) { exit 3 };` +
        `$c | Select-Object -ExpandProperty OwningProcess`;
      const { stdout } = await execFile("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps]);
      return String(stdout).split(/\s+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n > 0);
    }
    const { stdout } = await execFile("lsof", ["-ti", `tcp:${Number(port)}`, "-sTCP:LISTEN"]);
    return String(stdout).split(/\s+/).map((s) => parseInt(s, 10)).filter((n) => Number.isFinite(n) && n > 0);
  } catch {
    return [];
  }
}

// IS THIS A PID WE MAY SIGNAL? Its own predicate, so the rule can fail a test instead of only
// failing in production — the `doctor-predicates.js` pattern.
//
// It was inline in `defaultKillOnePid` and could not be proved ON THIS HOST: with the check removed,
// Windows `Stop-Process -Id 0` simply errors and the helper still returns false, so every mutation
// weakening the guard survived. The rule it encodes only bites on POSIX, where `process.kill(0,
// SIGTERM)` signals THE ENTIRE PROCESS GROUP — the wrapper, the bridge and every sibling the
// operator's shell started — and a negative pid signals a process group by number. A guard whose
// whole purpose is a platform this machine is not can still be tested as a predicate.
//
// STRINGS ARE REFUSED even when they parse: a pid arriving as text came from a marker file or a
// command's output, and the caller has not established it is still the process it thinks it is.
export function isKillablePid(value) {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

// Kill one pid (no tree). Platform-aware: Windows Stop-Process -Force; POSIX
// SIGTERM. Never throws → false on failure. Injectable.
export async function defaultKillOnePid(pid) {
  if (!isKillablePid(pid)) return false;
  const n = pid;
  try {
    if (process.platform === "win32") {
      await execFile("powershell.exe", [
        "-NoProfile", "-NonInteractive", "-Command",
        `$ErrorActionPreference='SilentlyContinue'; Stop-Process -Id ${n} -Force`,
      ]);
      return true;
    }
    process.kill(n, "SIGTERM");
    return true;
  } catch {
    return false;
  }
}

// Default port→PID→kill. Find the process LISTENING on `port`, VERIFY it is a
// hermes daemon, and only then kill it. NEVER throws — on any failure or no-match
// it resolves { killed:false }. Resolution / cmdline lookup / kill are all
// injectable so tests never touch a real process or socket.
//
// SAFETY (anti-overkill): a stale `aify-hermes-port-<agent>` marker can name a
// port the OS has since freed and handed to an UNRELATED operator process. Before
// killing, we resolve the listener's cmdline/image and require it to look like
// hermes (looksLikeHermesProcess). If it does not, we SKIP and resolve
// { killed:false, skipped:true } so we never kill the operator's own dev server.
export async function defaultKillByPort(
  port,
  {
    getCmdline = defaultGetCmdline,
    resolveListenerPids = defaultResolveListenerPids,
    killOnePid = defaultKillOnePid,
  } = {},
) {
  if (!port) return { killed: false };
  try {
    const pids = await resolveListenerPids(port);
    if (!Array.isArray(pids) || pids.length === 0) return { killed: false };
    let killedAny;
    let skippedAny = false;
    for (const pid of pids) {
      // VERIFY: the listener must be a hermes process before we kill it.
      if (!looksLikeHermesProcess(getCmdline(pid))) {
        skippedAny = true;
        try { console.error(`[hermes] killByPort: listener on port ${Number(port)} (pid ${pid}) is not hermes — SKIP (stale port marker, port reused by unrelated process)`); } catch { /* ignore */ }
        continue;
      }
      if (await killOnePid(pid) && killedAny === undefined) killedAny = pid;
    }
    if (killedAny !== undefined) return { killed: true, pid: killedAny };
    return { killed: false, skipped: skippedAny || undefined };
  } catch {
    // lsof/powershell missing, no match, or non-zero exit → treat as not-found.
    return { killed: false };
  }
}

// Tear down the per-agent `hermes gateway run` daemon for an agent by killing
// whatever process is LISTENING on its port.
//   - Resolve the port: explicit endpoint.port/port wins; else agentEndpoint(agentId).
//   - killByPort(port) is injectable (defaults to defaultKillByPort). Skipped when another agent's
//     marker claims that port.
//   - With `reapPrior` (kill-prior only), also collect what a previous generation of this agent left,
//     and keep the gateway markers while its port is still held.
//   - Idempotent: nothing to stop → { stopped:false }.
//   - NEVER throws.
// Returns { stopped:bool, pid? }.
export async function stopDaemon({
  agentId,
  endpoint,
  tempDir,
  port,
  killByPort = defaultKillByPort,
  // Injectable pid-tracking + killer (defaults to the real fs / tree-killer /
  // alive-probe) so tests assert the tracked-pid kill without real processes.
  killTree = defaultKillTree,
  isAlive = defaultIsAlive,
  readPid = readDaemonPid,
  clearPid = clearDaemonPid,
  // Injectable cmdline lookup so the tracked-pid cross-check is testable without
  // touching real processes.
  getCmdline = defaultGetCmdline,
  // Terminal teardown clears the agent's port/key markers (Task 4.1) -- except, with reapPrior, while a
  // gateway still holds the port (see below). Injectable for tests.
  clearGatewayMarkers = defaultClearGatewayMarkers,
  // KILL-PRIOR ONLY (`hermes-daemon-cli.js stop`): also collect what a previous generation of this
  // agent left -- its gateway host tree on the PERSISTED port and hermes' session-lease holder -- and
  // keep the port marker while that port is still held. See hermes-prior-reap.mjs for the incident.
  reapPrior = false,
  reap = reapPriorHermes,
} = {}) {
  let stopped = false;
  let pid;
  try {
    let derived = endpoint;
    if (!derived && agentId) {
      derived = agentEndpoint(agentId, tempDir ? { tempDir } : undefined);
    }
    const effPort = port ?? derived?.port;

    // 1. Port-based kill: take down whatever is LISTENING on the current port.
    //    NOT when another agent's marker claims that port: the derived port is the HASH port, which
    //    collides, and resolveGatewayPort walks a neighbour's gateway into it -- while killByPort only
    //    checks that the listener looks like hermes. This agent's own daemon is never on a claimed port.
    const claimedElsewhere = effPort && agentId && claimedByOtherAgents(tempDir || undefined, agentId).has(effPort);
    if (effPort && !claimedElsewhere) {
      const res = await killByPort(effPort);
      if (res && res.killed) {
        stopped = true;
        pid = res.pid;
      }
    }

    // 2. Tracked-pid kill: covers a daemon whose port has already changed/been
    //    abandoned (so killByPort on the current port would miss the stray). Kill
    //    the tracked pid's TREE if it is still alive AND its cmdline confirms it is
    //    hermes, then clear the pid file. Stale-pid-safe two ways: (a) only signal
    //    a pid that is alive; (b) anti-overkill — under OS pid reuse a stale
    //    daemon-pid marker can name an UNRELATED operator process, so verify the
    //    pid's cmdline looks like hermes before killTree. If it does not match, we
    //    SKIP the kill, log it, and still clear the stale marker.
    if (agentId) {
      const trackedPid = readPid(agentId, tempDir);
      if (trackedPid && isAlive(trackedPid)) {
        if (looksLikeHermesProcess(getCmdline(trackedPid))) {
          killTree(trackedPid);
          stopped = true;
          if (pid === undefined) pid = trackedPid;
        } else {
          try { console.error(`[hermes] stopDaemon: tracked pid ${trackedPid} for agent ${agentId} is not hermes — SKIP (stale daemon-pid marker, pid reused by unrelated process)`); } catch { /* ignore */ }
        }
      }
      clearPid(agentId, tempDir);
      let portStillHeld = false;
      if (reapPrior) {
        const reaped = reap(tempDir ? { agentId, tempDir } : { agentId });
        if (reaped.stopped.length) {
          stopped = true;
          if (pid === undefined) pid = reaped.stopped[0].pid;
        }
        portStillHeld = reaped.portStillHeld;
      }
      // Terminal stop → also drop the agent's port/key gateway markers so a
      // restart is a clean slate (Task 4.1). Best-effort; never throws. NOT while a gateway still
      // holds the port: clearing it then is what sent the next launch to a new port and left the old
      // gateway holding the session.
      try {
        if (!portStillHeld) clearGatewayMarkers(agentId, tempDir);
      } catch {
        /* best-effort */
      }
    }
  } catch {
    // Best-effort teardown must never throw — a failed reap is logged by callers.
  }
  return stopped ? { stopped: true, pid } : { stopped: false };
}
