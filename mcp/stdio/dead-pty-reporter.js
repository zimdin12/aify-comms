#!/usr/bin/env node
// Host-reported dead-PTY detection + reporting (WS4 Task 4.2).
//
// The aify-comms SERVER cannot probe a remote host's PID — only the OWNING
// environment bridge can. When a console PTY this bridge owns is still
// `attached` server-side but its local process is no longer alive (killed
// externally, crashed without the in-memory exit handler firing, etc.), the
// bridge must REPORT it so the server marks the terminal_sessions row stopped
// and invalidates the agent's live state. Otherwise a frozen/crashed console
// keeps manufacturing presence (the "online but no worker" defect).
//
// This module is pure + injectable: `findDeadOwnedSessions` does the liveness
// classification against an injected `isAlive`, and `reportDeadOwnedSessions`
// POSTs each dead row through an injected reporter. Nothing here KILLS anything.

// Default local pid-liveness probe. `process.kill(pid, 0)` does not signal — it
// only checks for existence/permission. EPERM = the process exists but isn't
// ours to signal → still alive. Anything else (ESRCH) → dead.
export function defaultIsPidAlive(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    process.kill(n, 0);
    return true;
  } catch (err) {
    return Boolean(err && err.code === "EPERM");
  }
}

// Given the bridge's owned console sessions (from
// TerminalProcessManager.listOwnedSessions) return the subset that are still
// `attached` but whose local pid is NOT alive. Only `attached` rows are
// considered — a row already transitioning (stopping/stopped) is left to the
// normal exit path. Injectable `isAlive` so tests never touch real processes.
export function findDeadOwnedSessions(ownedSessions = [], { isAlive = defaultIsPidAlive } = {}) {
  const dead = [];
  for (const session of ownedSessions || []) {
    const status = String(session?.status || "").trim().toLowerCase();
    if (status !== "attached") continue;
    const pid = Number(session?.pid);
    if (!Number.isInteger(pid) || pid <= 0) continue;
    if (isAlive(pid)) continue;
    dead.push(session);
  }
  return dead;
}

// Report each dead owned session to the server via the injected `report`
// function (POST /terminals/{id}/report-dead). Best-effort: a failed report for
// one session never aborts the rest and never throws. Returns the list of
// terminalIds reported. `report` receives ({ terminalId, pid, agentId }).
export async function reportDeadOwnedSessions(ownedSessions = [], { isAlive = defaultIsPidAlive, report } = {}) {
  if (typeof report !== "function") return [];
  const dead = findDeadOwnedSessions(ownedSessions, { isAlive });
  const reported = [];
  for (const session of dead) {
    try {
      // eslint-disable-next-line no-await-in-loop
      await report({ terminalId: session.terminalId, pid: session.pid, agentId: session.agentId });
      reported.push(session.terminalId);
    } catch {
      /* best-effort: a failed report is retried next sweep */
    }
  }
  return reported;
}

export default { findDeadOwnedSessions, reportDeadOwnedSessions, defaultIsPidAlive };
