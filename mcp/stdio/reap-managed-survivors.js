#!/usr/bin/env node
// Reap managed-hermes triad survivors scoped to ONE environment bridge's owned
// agents — ENV-SCOPED & SAFE. The companion to reap-managed-claude.js for the
// hermes triad (gateway host + delivery loop + daemon + console PTY).
//
// WHY: the managed-hermes triad is three independently-detached host processes
// (gateway host, delivery loop `hermes-managed-host.js run <agent>`, console
// PTY) plus a per-agent api_server daemon — all engineered to OUTLIVE the
// launcher (detached+unref / nohup+disown). A bridge restart or SIGKILL leaves
// every one of them orphaned. This module enumerates those survivors for the
// bridge's OWNED managed agents and kills each with the right primitive.
//
// SAFETY (mirrors reap-managed-claude.js):
//   - We ONLY ever touch a process whose agent is in `ownedAgentIds` — the set
//     of managed agents THIS env bridge owns within its cwdRoots. An agent id is
//     unique, so another env's children or another team's agent can never be
//     enumerated here.
//   - We NEVER touch a RESIDENT operator session. A resident session carries
//     `--aify-agent <x>` but is NOT a managed delivery loop; if its agent is not
//     in `ownedAgentIds` it is excluded outright. (A managed loop's own agent is
//     identified by the `hermes-managed-host.js run <agent>` cmdline, not by a
//     resident `--aify-agent` marker.)
//   - Fail-safe: empty/missing `ownedAgentIds` → enumerate NOTHING. Leaking a
//     survivor is acceptable; killing the operator's own session is not.
//
// Enumeration + kill primitives are injected so tests never touch real
// processes, ports, or files.

import { spawnSync as nodeSpawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { defaultKillByPort, stopDaemon as realStopDaemon } from "./hermes-daemon.js";
import { terminateProcessTree } from "./runtimes.js";

// --- cmdline classifiers ----------------------------------------------------

// The managed delivery loop is launched as `... hermes-managed-host.js run <agent>`.
// Return the agent id, or null if this cmdline is not a delivery loop.
export function cmdlineDeliveryLoopAgent(commandLine) {
  const s = String(commandLine || "");
  if (!s) return null;
  // Require the host script AND the `run <agent>` subcommand. `ensure-host` and
  // bare invocations are not long-lived delivery loops.
  const m = s.match(/hermes-managed-host\.js\s+run\s+([A-Za-z0-9_-]+)/);
  return m ? m[1] : null;
}

// A resident operator session carries `--aify-agent <x>` (space or = form) and
// is NOT a managed delivery loop. Return the agent id, or null. Used to EXCLUDE
// resident sessions from the survivor set (defence-in-depth — a resident agent
// not in ownedAgentIds is already excluded, but this makes the intent explicit
// and guards a cmdline that carries both markers).
export function cmdlineResidentAgent(commandLine) {
  const s = String(commandLine || "");
  if (!s) return null;
  if (cmdlineDeliveryLoopAgent(s)) return null; // a managed loop is not resident
  const m = s.match(/--aify-agent[=\s]+([A-Za-z0-9_-]+)/);
  return m ? m[1] : null;
}

// --- real enumeration sources (injectable) ----------------------------------

// Enumerate running processes as [{ pid, ppid, commandLine }].
//   - win32: Get-CimInstance Win32_Process (ProcessId + ParentProcessId + CommandLine)
//   - posix: `ps -eo pid=,ppid=,args=`
// Never throws → returns [] on failure.
export function defaultListProcesses(spawnSync = nodeSpawnSync) {
  try {
    if (process.platform === "win32") {
      const ps =
        "Get-CimInstance Win32_Process | " +
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }";
      const res = spawnSync(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", ps],
        { encoding: "utf8", windowsHide: true, timeout: 10000 },
      );
      return parseProcLines(String(res.stdout || ""));
    }
    const res = spawnSync("ps", ["-eo", "pid=,ppid=,args="], {
      encoding: "utf8",
      timeout: 10000,
    });
    return String(res.stdout || "")
      .split(/\r?\n/)
      .map((line) => {
        const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/);
        if (!m) return null;
        return { pid: Number(m[1]), ppid: Number(m[2]), commandLine: m[3] };
      })
      .filter(Boolean);
  } catch {
    return [];
  }
}

// Parse "PID\tPPID\tCOMMANDLINE" lines (win path). Exported for tests.
export function parseProcLines(stdout) {
  return String(stdout || "")
    .split(/\r?\n/)
    .map((line) => {
      const parts = line.split("\t");
      if (parts.length < 3) return null;
      const pid = Number(parts[0].trim());
      const ppid = Number(parts[1].trim());
      const commandLine = parts.slice(2).join("\t");
      if (!Number.isInteger(pid) || pid <= 0) return null;
      return { pid, ppid: Number.isInteger(ppid) ? ppid : 0, commandLine };
    })
    .filter(Boolean);
}

// Read the hermes triad markers from tempDir as
// [{ kind:'port'|'daemon-pid', agentId, value }]. Mirrors the file conventions
// in hermes-endpoint.js (aify-hermes-port-<agent>) and hermes-daemon.js
// (aify-hermes-daemon-pid-<agent>). Never throws → [] on failure.
export function defaultReadMarkers(tempDir = os.tmpdir(), { fs: fsImpl = fs } = {}) {
  const out = [];
  let entries = [];
  try {
    entries = fsImpl.readdirSync(tempDir);
  } catch {
    return out;
  }
  for (const name of entries) {
    let kind = null;
    let prefix = "";
    if (name.startsWith("aify-hermes-port-")) {
      kind = "port";
      prefix = "aify-hermes-port-";
    } else if (name.startsWith("aify-hermes-daemon-pid-")) {
      kind = "daemon-pid";
      prefix = "aify-hermes-daemon-pid-";
    } else {
      continue;
    }
    const agentId = name.slice(prefix.length);
    if (!agentId) continue;
    let value;
    try {
      value = parseInt(String(fsImpl.readFileSync(path.join(tempDir, name), "utf8")).trim(), 10);
    } catch {
      continue;
    }
    if (!Number.isInteger(value) || value <= 0) continue;
    out.push({ kind, agentId, value });
  }
  return out;
}

// --- enumeration ------------------------------------------------------------

// Enumerate managed-hermes triad survivors for ONLY the owned/in-root agents.
//
//   ownedAgentIds  — Set/array of managed agent ids THIS env bridge owns.
//   listProcesses  — () => [{ pid, ppid, commandLine }] (injectable).
//   readMarkers    — () => [{ kind:'port'|'daemon-pid', agentId, value }].
//   consolePtyPids — [{ agentId, pid }] from terminal_sessions.process_id,
//                    already scoped by the caller to owned/in-root agents.
//
// Returns { gatewayHosts:[{agentId,port}], deliveryLoops:[{agentId,pid}],
//           daemons:[{agentId,pid,port?}], consolePtys:[{agentId,pid}] }.
// Fail-safe: no owned agents → all empty.
export function enumerateManagedSurvivors({
  ownedAgentIds = [],
  cwdRoots = [], // accepted for signature parity; caller pre-scopes owned ids by root
  listProcesses = defaultListProcesses,
  readMarkers = defaultReadMarkers,
  consolePtyPids = [],
} = {}) {
  const owned = new Set(
    (Array.isArray(ownedAgentIds) ? ownedAgentIds : Array.from(ownedAgentIds || []))
      .map((a) => String(a || "").trim())
      .filter(Boolean),
  );

  const found = { gatewayHosts: [], deliveryLoops: [], daemons: [], consolePtys: [] };

  // SAFETY: no owned agents → enumerate nothing (never a blanket sweep).
  if (owned.size === 0) return found;

  // 1. Delivery loops — cmdline `hermes-managed-host.js run <agent>`.
  let procs = [];
  try {
    procs = listProcesses() || [];
  } catch {
    procs = [];
  }
  for (const p of procs) {
    if (!p || typeof p.commandLine !== "string") continue;
    // NEVER a resident operator session.
    const residentAgent = cmdlineResidentAgent(p.commandLine);
    if (residentAgent && !owned.has(residentAgent)) continue;
    const loopAgent = cmdlineDeliveryLoopAgent(p.commandLine);
    if (!loopAgent || !owned.has(loopAgent)) continue;
    const pid = Number(p.pid);
    if (!Number.isInteger(pid) || pid <= 0) continue;
    found.deliveryLoops.push({ agentId: loopAgent, pid });
  }

  // 2 + 3. Gateway hosts (port markers) and daemons (daemon-pid markers).
  let markers = [];
  try {
    markers = readMarkers() || [];
  } catch {
    markers = [];
  }
  const daemonPortByAgent = new Map();
  for (const m of markers) {
    if (!m || !owned.has(String(m.agentId || "").trim())) continue;
    if (m.kind === "port") {
      const port = Number(m.value);
      if (Number.isInteger(port) && port > 0) {
        found.gatewayHosts.push({ agentId: m.agentId, port });
        daemonPortByAgent.set(m.agentId, port);
      }
    }
  }
  for (const m of markers) {
    if (!m || !owned.has(String(m.agentId || "").trim())) continue;
    if (m.kind === "daemon-pid") {
      const pid = Number(m.value);
      if (Number.isInteger(pid) && pid > 0) {
        found.daemons.push({
          agentId: m.agentId,
          pid,
          port: daemonPortByAgent.get(m.agentId),
        });
      }
    }
  }

  // 4. Console PTYs — caller-supplied terminal_sessions.process_id list.
  for (const c of consolePtyPids || []) {
    if (!c || !owned.has(String(c.agentId || "").trim())) continue;
    const pid = Number(c.pid);
    if (!Number.isInteger(pid) || pid <= 0) continue;
    found.consolePtys.push({ agentId: c.agentId, pid });
  }

  return found;
}

// --- default kill primitives ------------------------------------------------

// Kill a pid + its process tree (taskkill /t /f on win32). Never throws.
export function defaultKillTree(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    terminateProcessTree({ pid: n }, "SIGKILL");
    return true;
  } catch {
    return false;
  }
}

// --- reap -------------------------------------------------------------------

// Kill every enumerated survivor with the right primitive:
//   gatewayHosts → killByPort(port)   (survives pid reuse: kill the listener)
//   deliveryLoops → killTree(pid)     (taskkill /t /f the node loop tree)
//   daemons      → stopDaemon({ agentId, port }) (port-kill + tracked-pid kill)
//   consolePtys  → killByPid(pid)     (TERMINAL_MANAGER tree-kill of the PTY)
//
// Pure + injectable. Logs (returns) what it kills; never silently drops — any
// primitive that throws is recorded in `errors` and does not abort the rest.
// Returns { killed: { gatewayHosts, deliveryLoops, daemons, consolePtys }, errors }.
export function reapManagedSurvivors(
  found = {},
  {
    killByPort = defaultKillByPort,
    stopDaemon = realStopDaemon,
    killTree = defaultKillTree,
    killByPid,
    log = (msg) => console.error(msg),
  } = {},
) {
  const killed = { gatewayHosts: [], deliveryLoops: [], daemons: [], consolePtys: [] };
  const errors = [];
  // Async kill primitives (killByPort/stopDaemon) return promises. Collect them
  // so a graceful (async) caller can `await Promise.allSettled(result.pending)`
  // before process.exit; a sync caller (cleanupOnExit) just ignores them.
  const pending = [];

  const gatewayHosts = found.gatewayHosts || [];
  const deliveryLoops = found.deliveryLoops || [];
  const daemons = found.daemons || [];
  const consolePtys = found.consolePtys || [];

  // Daemons first: stopDaemon kills the api_server by port + tracked pid and
  // clears the pid file. Doing it before the port-kill of gateway hosts is fine
  // (different ports per kind in practice; both best-effort).
  for (const d of daemons) {
    try {
      const r = stopDaemon({ agentId: d.agentId, port: d.port });
      if (r && typeof r.then === "function") pending.push(Promise.resolve(r).catch(() => {}));
      killed.daemons.push({ agentId: d.agentId, pid: d.pid, port: d.port });
      try { log(`[aify] reap-survivors: stopped daemon agent=${d.agentId} port=${d.port ?? "?"} pid=${d.pid}`); } catch { /* ignore */ }
    } catch (err) {
      errors.push({ kind: "daemon", agentId: d.agentId, error: String(err?.message || err) });
    }
  }

  for (const g of gatewayHosts) {
    try {
      const r = killByPort(g.port);
      if (r && typeof r.then === "function") pending.push(Promise.resolve(r).catch(() => {}));
      killed.gatewayHosts.push({ agentId: g.agentId, port: g.port });
      try { log(`[aify] reap-survivors: killed gateway host agent=${g.agentId} port=${g.port}`); } catch { /* ignore */ }
    } catch (err) {
      errors.push({ kind: "gatewayHost", agentId: g.agentId, port: g.port, error: String(err?.message || err) });
    }
  }

  for (const l of deliveryLoops) {
    try {
      killTree(l.pid);
      killed.deliveryLoops.push({ agentId: l.agentId, pid: l.pid });
      try { log(`[aify] reap-survivors: killed delivery loop agent=${l.agentId} pid=${l.pid}`); } catch { /* ignore */ }
    } catch (err) {
      errors.push({ kind: "deliveryLoop", agentId: l.agentId, pid: l.pid, error: String(err?.message || err) });
    }
  }

  for (const c of consolePtys) {
    try {
      if (typeof killByPid === "function") {
        killByPid(c.pid);
      } else {
        defaultKillTree(c.pid);
      }
      killed.consolePtys.push({ agentId: c.agentId, pid: c.pid });
      try { log(`[aify] reap-survivors: killed console PTY agent=${c.agentId} pid=${c.pid}`); } catch { /* ignore */ }
    } catch (err) {
      errors.push({ kind: "consolePty", agentId: c.agentId, pid: c.pid, error: String(err?.message || err) });
    }
  }

  return { killed, errors, pending };
}

// --- teardown orchestrator (the server.js seam) -----------------------------

// Enumerate + reap in one call — the seam server.js wires into
// shutdownWithStatus (after TERMINAL_MANAGER.stopAll) and the supersede path,
// ONLY when IS_ENVIRONMENT_BRIDGE. Pure + injectable: pass the bridge's owned
// agent ids + cwdRoots, the process/marker/console-pty enumerators, and the
// kill primitives. Fail-safe: empty ownedAgentIds → no-op (enumerate finds
// nothing). Returns the reap result ({ killed, errors }).
export function runManagedTeardown({
  ownedAgentIds = [],
  cwdRoots = [],
  listProcesses,
  readMarkers,
  consolePtyPids = [],
  killByPort,
  stopDaemon,
  killTree,
  killByPid,
  log,
} = {}) {
  const found = enumerateManagedSurvivors({
    ownedAgentIds,
    cwdRoots,
    listProcesses,
    readMarkers,
    consolePtyPids,
  });
  return reapManagedSurvivors(found, { killByPort, stopDaemon, killTree, killByPid, log });
}

// --- owning-bridge liveness (the ownerLive signal) --------------------------

// Decide whether an agent's OWNING ENVIRONMENT BRIDGE is alive — the signal that
// feeds `ownerLive`. This MUST NOT be derived from the agent's status: after a
// SIGKILL/crash the survivor's detached delivery loop keeps heartbeating + holds
// its claimer lease, so the agent stays online/working and a status-based signal
// would mark the DEAD owner as live → the orphan it exists to kill would be
// skipped forever (the WS2 boot-sweep bug).
//
// Instead we mirror how the server judges bridge freshness (a fresh,
// non-superseded bridge_instances row) using the only host-side source that
// exposes it without a new endpoint: GET /environments. Each environment row
// reports its CURRENT bridgeId and a last_seen-derived online/offline status. An
// owning bridge is LIVE iff it is the current bridgeId of an ONLINE environment.
//
//   - A crash survivor's stored owningBridgeId points at the DEAD predecessor.
//     Once the new bridge heartbeats, the env's bridgeId is the NEW id, so the
//     old id matches no online env → NOT live → reaped. Before the new heartbeat
//     lands, the env row still carries the dead bridge with a stale last_seen →
//     status offline → NOT live → reaped. Both orderings classify it ORPHANED.
//   - A genuinely live DIFFERENT bridge IS the current bridgeId of an online env
//     → LIVE → skipped.
//   - The freshly-booted bridge's own id is always live (defensive; the
//     orphanedOwnedAgentIds self-skip also covers it).
export function bridgeOwnerIsLive(owningBridgeId, { environments = [], selfBridgeId = "" } = {}) {
  const owner = String(owningBridgeId || "").trim();
  if (!owner) return false; // never synced / unknown owner → not live → eligible
  if (owner === String(selfBridgeId || "").trim()) return true; // our own bridge
  for (const env of environments || []) {
    if (!env) continue;
    const envBridge = String(env.bridgeId || "").trim();
    if (envBridge && envBridge === owner && String(env.status || "").toLowerCase() === "online") {
      return true;
    }
  }
  return false;
}

// --- boot-time orphan sweep -------------------------------------------------

// Given per-agent ownership records [{ agentId, owningBridgeId, ownerLive }],
// return the agent ids whose managed survivors should be reaped on boot:
//   - ownerLive === false  → the owning bridge is NOT fresh in bridge_instances
//                            (dead/crashed/SIGKILLed predecessor) → REAP.
//   - owningBridgeId === selfBridgeId → owned by THIS freshly-booted bridge →
//                            never reap (defensive: a fresh boot can't legitimately
//                            own a survivor, but never kill our own).
//   - ownerLive === true (a different live bridge) → SKIP (the live owner manages it).
export function orphanedOwnedAgentIds(records = [], { selfBridgeId = "" } = {}) {
  const self = String(selfBridgeId || "").trim();
  const out = [];
  for (const r of records || []) {
    const agentId = String(r?.agentId || "").trim();
    if (!agentId) continue;
    const owner = String(r?.owningBridgeId || "").trim();
    if (owner && owner === self) continue; // never our own
    if (r?.ownerLive === true) continue; // a live different bridge owns it → skip
    out.push(agentId);
  }
  return out;
}

// Env-bridge BOOT sweep (before ensureSpawnLoop). Reap managed-triad survivors
// for agents in this env whose owning bridge is NOT live, and SKIP any owned by
// a currently-live different bridge.
//
//   selfBridgeId   — this freshly-booted bridge's instance id.
//   cwdRoots       — this env's roots (parity; ownership is already env-scoped).
//   fetchOwnership — () => [{ agentId, owningBridgeId, ownerLive }] for the
//                    managed agents in this env (derived from the server).
//   listProcesses / readMarkers — survivor enumerators (injectable).
//   killByPort / stopDaemon / killTree / killByPid — kill primitives.
//
// FAIL-SAFE: if fetchOwnership throws or returns nothing usable, reap NOTHING
// (an unknown scope must never become a blanket sweep). Returns the reap result,
// or { skipped:"ownership-unavailable" } when scope could not be determined.
export function reapOrphanedManagedSurvivors({
  selfBridgeId = "",
  cwdRoots = [],
  fetchOwnership,
  listProcesses,
  readMarkers,
  consolePtyPids = [],
  killByPort,
  stopDaemon,
  killTree,
  killByPid,
  log = (msg) => console.error(msg),
} = {}) {
  let records;
  try {
    records = fetchOwnership ? fetchOwnership() : null;
  } catch (err) {
    try { log(`[aify] boot reap: ownership query failed (${err?.message || err}) — reaping nothing (fail-safe)`); } catch { /* ignore */ }
    return { skipped: "ownership-unavailable", killed: { gatewayHosts: [], deliveryLoops: [], daemons: [], consolePtys: [] }, errors: [] };
  }
  if (!Array.isArray(records)) {
    return { skipped: "ownership-unavailable", killed: { gatewayHosts: [], deliveryLoops: [], daemons: [], consolePtys: [] }, errors: [] };
  }

  const orphanIds = orphanedOwnedAgentIds(records, { selfBridgeId });
  if (orphanIds.length === 0) {
    return { killed: { gatewayHosts: [], deliveryLoops: [], daemons: [], consolePtys: [] }, errors: [] };
  }

  const found = enumerateManagedSurvivors({
    ownedAgentIds: orphanIds,
    cwdRoots,
    listProcesses,
    readMarkers,
    consolePtyPids,
  });
  return reapManagedSurvivors(found, { killByPort, stopDaemon, killTree, killByPid, log });
}

export default {
  enumerateManagedSurvivors,
  reapManagedSurvivors,
  runManagedTeardown,
  orphanedOwnedAgentIds,
  bridgeOwnerIsLive,
  reapOrphanedManagedSurvivors,
};
