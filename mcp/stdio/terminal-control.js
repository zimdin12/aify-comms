// Kill-by-pid fallback decision (2026-06-02). The terminal-control loop calls
// TERMINAL_MANAGER.stop(id) on a `stop` control. When this bridge never owned
// the PTY in its in-memory Map (owning bridge restarted/died), stop() returns
// `{stopped:false}` and the orphaned PTY survives. If the stop control carries
// the persisted PTY root pid, reap the orphan by pid as a FALLBACK. Returns the
// positive integer pid to kill, or 0 when no fallback should run (owned path,
// or no/invalid pid). Pure + side-effect-free so it's directly testable.
export function orphanPidToKill(stopResult, control) {
  const ownedAndStopped = !!(stopResult && stopResult.stopped !== false);
  if (ownedAndStopped) return 0;
  const pid = Number(control && control.pid);
  if (!Number.isInteger(pid) || pid <= 0) return 0;
  return pid;
}

export function terminalControlFailurePatch(action = "", error) {
  const normalizedAction = String(action || "").trim().toLowerCase();
  const message = error?.message || String(error || "");
  const lateAfterExit = /terminal\s+"?.+?"?\s+is not running/i.test(message);
  if (lateAfterExit && ["input", "resize", "stop"].includes(normalizedAction)) {
    return { status: "failed", terminalStatus: "stopped", error: message };
  }
  return { status: "failed", terminalStatus: "failed", error: message };
}
