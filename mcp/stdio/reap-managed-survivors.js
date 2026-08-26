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

import { defaultKillByPort, stopDaemon as realStopDaemon } from "./hermes-daemon.js";
// The process read side left for `proc-probes.js` in v0.5.4 — enumerate and identify there, decide
// what to kill here. They were already this file's most widely imported names.
import {
  cmdlineDeliveryLoopAgent,
  cmdlineResidentAgent,
  defaultKillTree,
  defaultListProcesses,
} from "./proc-probes.js";
// The marker-file read side left for `runtime-marker-files.js` in v0.5.4, with `proc-probes.js`
// taking the process table before it. What stays here decides what to kill.
import {
  defaultListMarkerFiles,
  defaultReadMarkers,
  sweepTombstonedMarkers,
  tombstonedMarkerAgentIds,
} from "./runtime-marker-files.js";

// --- cmdline classifiers ----------------------------------------------------

// --- real enumeration sources (injectable) ----------------------------------

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
  // Process truth wins over a stale backend mode: a live resident wrapper owns
  // this same gateway/loop triad. Never reap any artifact for that agent.
  for (const p of procs) {
    const residentAgent = p && typeof p.commandLine === "string"
      ? cmdlineResidentAgent(p.commandLine)
      : null;
    if (residentAgent) owned.delete(residentAgent);
  }
  if (owned.size === 0) return found;
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

// --- stop-control triad-teardown decision (the bridge stop-handler seam) ----

// Body sentinel a REMOVE stop control stamps (server: _REAP_TRIAD_BODY_SENTINEL)
// to carry the triad-reap intent forward when the agent row is already gone and
// sessionMode can no longer be resolved at claim time.
export const REAP_TRIAD_BODY_SENTINEL = "__aify_reap_triad__";

// fix/hermes-leak P2: decide whether a claimed terminal STOP control is for a
// MANAGED HERMES agent and therefore must trigger the agent-scoped triad
// teardown (gateway host + delivery loop + daemon) in addition to the PTY stop.
// Returns the agentId to scope the teardown to, or null when no triad reap
// applies. AGENT-SCOPED + fail-safe:
//   - action must be "stop" (input/resize/start never reap)
//   - runtime must be hermes (the "hermes" / "hermes-agent" family; claude has
//     its own reaper, other runtimes have no triad)
//   - the agent must be MANAGED, proven by EITHER sessionMode==="managed" OR the
//     REMOVE body sentinel (the agent row is gone post-delete, so sessionMode is
//     unresolvable then; the sentinel is the explicit triad-reap flag). A RESIDENT
//     hermes carries neither → NEVER reaped (operator's own session).
//   - agentId must be present (no id → nothing to scope → no reap)
/**
 * WHO ASKED for this stop, in words, for the one log line an operator reads about a dead worker.
 *
 * The teardown reason was the literal string "dashboard stop/remove", hardcoded at the call site
 * whatever the control said. MEASURED on the operator's live database: of 13 stop controls ever
 * recorded, **ZERO** came from the dashboard and **all 13** were requested by the agent being
 * stopped. So the only attribution available for a dying worker named the wrong actor, every time --
 * and an operator who never touched the dashboard reasonably concluded something else had.
 *
 * `requestedBy` was there the whole way: the service serialises it onto every control
 * (`terminal_controls_io.py`), and `environment-control-loop.mjs` already reads it.
 *
 * THE SELF-STOP GETS ITS OWN SENTENCE because it is the confusing one. "stopped on request" and
 * "the agent asked to be stopped" send an operator to completely different places -- one to their own
 * actions, the other to the agent's.
 */
export function stopRequestReason(control) {
  const who = String(control?.requestedBy || "").trim();
  const agent = String(control?.agentId || "").trim();
  if (!who) return "stop control, requester not recorded";
  if (agent && who === agent) return `stop requested by the agent itself (${who})`;
  return `stop requested by ${who}`;
}


export function stopControlTriadAgentId(control) {
  if (!control || typeof control !== "object") return null;
  if (String(control.action || "").trim().toLowerCase() !== "stop") return null;
  const runtime = String(control.runtime || "").trim().toLowerCase();
  const isHermes = runtime === "hermes" || runtime === "hermes-agent";
  if (!isHermes) return null;
  const managed = String(control.sessionMode || "").trim().toLowerCase() === "managed";
  const sentinel = String(control.body || "").includes(REAP_TRIAD_BODY_SENTINEL);
  if (!managed && !sentinel) return null;
  const agentId = String(control.agentId || "").trim();
  if (!agentId) return null;
  return agentId;
}

// --- default kill primitives ------------------------------------------------

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
//   - owningBridgeId === selfBridgeId → owned by THIS freshly-booted bridge.
//                            By DEFAULT this is skipped (never kill our own at
//                            runtime). On a FRESH BOOT, pass treatSelfAsOrphan:
//                            true — see below.
//   - ownerLive === true (a different live bridge) → SKIP (the live owner manages it).
//
// treatSelfAsOrphan (boot-only): on a freshly-booted env bridge that has not yet
// spawned ANY managed child, every enumerated survivor PROCESS necessarily
// predates this boot (it belongs to the dead predecessor). The predecessor's
// env-heartbeat re-sync can re-bind an agent's runtimeState.bridgeInstanceId to
// THIS bridge id before the sweep reads it (the sync-before-sweep race), or a
// SIGKILL can leave the env row briefly still showing the old bridge online — in
// either case the agent record reads "self" yet the running process is an orphan.
// With treatSelfAsOrphan the boot sweep reaps a self-owned agent's survivor too,
// because a fresh boot cannot legitimately own a running survivor. SAFETY is
// unchanged: an agent owned by a genuinely-live DIFFERENT bridge (owner !== self
// && ownerLive === true) is STILL skipped, so a co-located other-env's agents are
// never touched; and enumeration is still scoped to these agent ids + cwdRoots.
export function orphanedOwnedAgentIds(records = [], { selfBridgeId = "", treatSelfAsOrphan = false } = {}) {
  const self = String(selfBridgeId || "").trim();
  const out = [];
  for (const r of records || []) {
    const agentId = String(r?.agentId || "").trim();
    if (!agentId) continue;
    const owner = String(r?.owningBridgeId || "").trim();
    if (owner && owner === self) {
      // Owned by this bridge id. At runtime we never reap our own; on a fresh
      // boot a "self"-owned survivor is a predecessor's orphan (race / SIGKILL).
      if (treatSelfAsOrphan) out.push(agentId);
      continue;
    }
    if (r?.ownerLive === true) continue; // a live DIFFERENT bridge owns it → skip
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
  // Boot-only: treat a survivor whose agent record reads THIS bridge id as an
  // orphan too. A fresh env-bridge boot has spawned no managed children, so any
  // running survivor predates the boot regardless of whose id the agent record
  // now carries (closes the sync-before-sweep race + the SIGKILL stale-online
  // env-row gap). A genuinely-live DIFFERENT bridge's agents are still skipped.
  treatSelfAsOrphan = false,
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

  const orphanIds = orphanedOwnedAgentIds(records, { selfBridgeId, treatSelfAsOrphan });
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
  defaultListMarkerFiles,
  tombstonedMarkerAgentIds,
  sweepTombstonedMarkers,
  stopControlTriadAgentId,
};
