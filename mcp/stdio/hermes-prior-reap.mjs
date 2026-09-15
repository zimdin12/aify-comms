// What a previous generation of an agent's hermes left running, found by what it holds.
//
// MEASURED 2026-09-14. After the operator restarted `herdr-aify env`, mc-senior-dev refused every message:
// `Session 20260715_001441_960b8f already has a live owner (tui, pid 59544, running 3h43m)`. pid 59544 was
// the previous generation's `hermes dashboard --port 9272` host -- detached, parent gone, holding hermes'
// own session lease. Two more agents had the same leftover, one since 2026-09-12. Nothing reaped them:
//
//   1. kill-prior reaps with `pkill -f` and `lsof`, which on Git Bash cannot see native Windows processes;
//   2. its fallback, `stopDaemon`, killed whatever listened on the HASH port, while the gateway sat on the
//      port the agent had PERSISTED (9272);
//   3. `stopDaemon` then cleared the port marker anyway, destroying the only record of 9272, so the next
//      launch chose 9273 and the old gateway kept the lease;
//   4. the `--tui --resume <id>` matcher never matched, because the lease holder was the dashboard host.
//
// So this finds the leftovers by what they HOLD rather than by how they were started: a gateway host tree
// on a port this agent owns, and the process hermes records as the owner of the agent's session. It keeps
// the port marker while anything still listens there. It runs on the kill-prior path only -- the start of
// a new instance of this agent, whose launcher has already claimed the agent lease (aify-wrapper) -- and
// never kills its own ancestry.
//
// OWNED, NOT MERELY NAMED. Hash ports collide (two agents both hashed to 9341 on 2026-05-31) and
// `resolveGatewayPort` walks forward into a neighbour's slot, so a port is this agent's only when no
// OTHER agent's marker claims it; the hash port counts only for an agent that never persisted one. And a
// conversation can be named by more than one agent's session marker (four hermes agents shared one on
// 2026-08-31), so a session lease is collected only when no other agent names that session: otherwise
// starting one agent would end another's live TUI.

import { spawnSync as nodeSpawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { identify, isAlive, killTree, sleepMs, startTimes } from "aify-wrapper/lib/process-identity.mjs";

import { gatewaysInRange } from "./gateway-orphan-check.mjs";
import { PORT_BASE, PORT_SPAN, agentPort, claimedByOtherAgents, defaultMarkerTmpDir, readSessionIdMarker, sanitizeAgentId } from "./hermes-endpoint.js";
import { listListeners } from "./listening-ports.mjs";
import { cmdlineHermesGatewayPort, defaultListProcesses } from "./proc-probes.js";

const STOP_WAIT_MS = 8_000;


/** The port the agent persisted, or null when there is no readable in-range marker. */
export function persistedGatewayPort(agentId, { tempDir = defaultMarkerTmpDir(), io = fs } = {}) {
  try {
    const value = Number(String(io.readFileSync(path.join(tempDir, `aify-hermes-port-${sanitizeAgentId(agentId)}`), "utf8")).trim());
    return Number.isInteger(value) && value >= PORT_BASE && value < PORT_BASE + PORT_SPAN ? value : null;
  } catch {
    return null;
  }
}

/** Where hermes keeps its state: HERMES_HOME, else %LOCALAPPDATA%\hermes on Windows, else ~/.hermes. */
export function hermesHome({ env = process.env, platform = process.platform, home = os.homedir() } = {}) {
  if (env.HERMES_HOME) return env.HERMES_HOME;
  if (platform === "win32" && env.LOCALAPPDATA) return path.join(env.LOCALAPPDATA, "hermes");
  return path.join(home, ".hermes");
}

/**
 * The ports a reap of `agentId` may treat as its own: its persisted port, else its hash port, never one
 * another agent claims. None when the other agents' claims cannot be read: a port this cannot prove is
 * nobody else's is not one to stop a process on.
 */
export function ownedGatewayPorts(agentId, { tempDir = defaultMarkerTmpDir() } = {}) {
  const others = claimedByOtherAgents(tempDir, agentId, { strict: true });
  if (!others) return [];
  const persisted = persistedGatewayPort(agentId, { tempDir });
  const port = persisted ?? agentPort(agentId);
  return others.has(port) ? [] : [port];
}

/** Whether a session marker of any agent other than `agentId` names `sessionId`. */
export function sessionNamedByAnotherAgent(agentId, sessionId, { tempDir = defaultMarkerTmpDir(), io = fs } = {}) {
  if (!sessionId) return false;
  const own = `aify-hermes-session-${sanitizeAgentId(agentId)}`;
  try {
    return io.readdirSync(tempDir).some((name) => {
      if (!name.startsWith("aify-hermes-session-") || name === own) return false;
      try {
        return String(io.readFileSync(path.join(tempDir, name), "utf8")).trim() === sessionId;
      } catch {
        return false;
      }
    });
  } catch {
    // Unable to look is not the same as nobody else: do not collect the lease.
    return true;
  }
}

/** hermes' own session leases (`runtime/active_sessions.json`), or none when unreadable. */
export function readSessionLeases(home, { io = fs } = {}) {
  try {
    const entries = JSON.parse(io.readFileSync(path.join(home, "runtime", "active_sessions.json"), "utf8"))?.entries;
    return Array.isArray(entries) ? entries : [];
  } catch {
    return [];
  }
}

/** The pids from `pid` up through its parents, as far as `rows` can say. */
export function ancestry(pid, rows) {
  const parentOf = new Map((rows || []).map((row) => [row.pid, row.ppid]));
  const chain = new Set();
  for (let at = pid; at && !chain.has(at); at = parentOf.get(at)) chain.add(at);
  return chain;
}

/**
 * PURE. Which processes a reap of one agent stops, from a process table, hermes' session leases and the
 * start times of the lease holders.
 *
 * A gateway host tree is stopped when its port is one this agent owns. A lease holder is stopped only
 * when hermes' recorded start time for it is the one the OS reports -- a recycled pid is somebody
 * else -- and its command line is hermes. Nothing in `protect` is ever stopped.
 *
 * @returns {Array<{pid: number, why: string}>}
 */
export function planPriorReap({ ports, rows, leases, sessionId, leaseStarts, protect }) {
  const stop = new Map();
  const wanted = new Set(ports.filter(Boolean));
  for (const gateway of gatewaysInRange(rows, { toPort: cmdlineHermesGatewayPort, base: PORT_BASE, span: PORT_SPAN })) {
    if (wanted.has(gateway.port) && !protect.has(gateway.pid)) stop.set(gateway.pid, `gateway host on port ${gateway.port}`);
  }
  const commandOf = new Map((rows || []).map((row) => [row.pid, String(row.commandLine || "")]));
  for (const lease of sessionId ? leases : []) {
    const pid = Number(lease?.pid);
    if (lease?.session_id !== sessionId || !Number.isInteger(pid) || protect.has(pid) || stop.has(pid)) continue;
    if (!/hermes/i.test(commandOf.get(pid) || "")) continue;
    const state = identify({ startedAtMs: Number(lease.process_start_time) * 1000 }, { alive: commandOf.has(pid), startedAt: leaseStarts.get(pid) });
    if (state === "ours") stop.set(pid, `holds hermes' lease on session ${sessionId}`);
  }
  return [...stop].map(([pid, why]) => ({ pid, why }));
}

/**
 * Stop what the previous generation of `agentId` left, and say whether its port is still held.
 * Never throws.
 *
 * @returns {{stopped: Array<{pid: number, why: string}>, portStillHeld: boolean}}
 */
export function reapPriorHermes({
  agentId,
  tempDir = defaultMarkerTmpDir(),
  spawnSync = nodeSpawnSync,
  // STRICT: a failed or partial listing throws, so a port is never read as free on a table nobody saw.
  listProcesses = () => defaultListProcesses(spawnSync, { strict: true }),
  // The socket table, which names a listener even when its command line cannot be read (an elevated one).
  listeners = () => listListeners({ run: spawnSync }),
  leases = () => readSessionLeases(hermesHome()),
  sessionIdOf = (id) => readSessionIdMarker(id, { tempDir }),
  starts = startTimes,
  kill = killTree,
  alive = isAlive,
  self = process.pid,
  waitMs = STOP_WAIT_MS,
} = {}) {
  try {
    const ports = ownedGatewayPorts(agentId, { tempDir });
    const rows = listProcesses();
    const sessionLeases = leases();
    const ownSession = String(sessionIdOf(agentId) || "");
    const plan = planPriorReap({
      ports,
      rows,
      leases: sessionLeases,
      sessionId: sessionNamedByAnotherAgent(agentId, ownSession, { tempDir }) ? "" : ownSession,
      leaseStarts: starts(sessionLeases.map((lease) => Number(lease?.pid))),
      protect: ancestry(self, rows),
    });
    for (const entry of plan) {
      kill(entry.pid);
      try { console.error(`[hermes] kill-prior ${agentId}: stopped pid ${entry.pid} (${entry.why})`); } catch { /* ignore */ }
    }
    const deadline = Date.now() + waitMs;
    while (plan.some((entry) => alive(entry.pid)) && Date.now() < deadline) sleepMs(250);
    // The agent's own marker port counts as held even when another marker also names it: clearing the
    // marker while a gateway still listens there is how the 2026-09-14 leftover lost its only record.
    const held = new Set([...ports, persistedGatewayPort(agentId, { tempDir })].filter(Boolean));
    const after = listProcesses();
    const byCommandLine = after.some((row) => held.has(cmdlineHermesGatewayPort(row.commandLine)));
    // A port can be held by a process whose command line reads as empty -- an ELEVATED gateway, which
    // `hermes update` run from an Administrator terminal relaunches (2026-09-15). Unseen by the plan above
    // and unstoppable from here, it still holds the port, so the marker stays and the operator is told.
    const readable = new Set(after.filter((row) => String(row.commandLine || "").trim()).map((row) => row.pid));
    const bySocket = listeners().filter((listener) => held.has(listener.port));
    for (const listener of bySocket.filter((entry) => !readable.has(entry.pid))) {
      try {
        console.error(`[hermes] kill-prior ${agentId}: port ${listener.port} is held by pid ${listener.pid}, whose command line this process cannot read -- almost always an elevated process. It was not stopped; stop it from an Administrator terminal (taskkill /F /T /PID ${listener.pid}).`);
      } catch { /* ignore */ }
    }
    return { stopped: plan, portStillHeld: byCommandLine || bySocket.length > 0 };
  } catch {
    // Unable to look is not the same as nothing left: keep the marker.
    return { stopped: [], portStillHeld: true };
  }
}
