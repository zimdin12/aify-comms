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

// Identity guard for the kill-by-pid orphan fallback (2026-07-10 bughunt HIGH).
// The Stop control carries the persisted PTY root pid (terminal_sessions.process_id)
// from a PRIOR spawn. The fallback fires ONLY on the Map-miss path — i.e. exactly
// when the owning env-bridge restarted/died, the highest-probability window for the
// original PTY to be dead and its pid RECYCLED by Windows onto another live process.
// pidIsSelfProtected (in terminateProcessTree) already blocks the bridge/shell/init;
// this adds the SIBLING-AGENT protection it can't give: refuse the kill when the
// pid's command line positively identifies a DIFFERENT agent's managed console
// (its `--aify-agent <other>` wrapper marker).
//
// FAILS OPEN (returns true → proceed with the current kill) on EVERY uncertainty —
// no agentId on the control, no getCmdline, an unreadable/empty cmdline, a cmdline
// with no `--aify-agent` marker, or one that matches THIS agent. Rationale: the
// whole purpose of this fallback is to not silently drop the operator's Stop, so we
// only ever BLOCK when we can POSITIVELY prove the pid belongs to another agent.
// Pure + injectable (getCmdline) for tests.
export function orphanPidReapAllowed(pid, control, { getCmdline } = {}) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  const wantAgent = String((control && control.agentId) || "").trim();
  if (!wantAgent || typeof getCmdline !== "function") return true;
  let cmdline = "";
  try {
    cmdline = String(getCmdline(n) || "");
  } catch {
    return true; // cmdline unreadable → don't block a legitimate Stop
  }
  if (!cmdline) return true;
  // Charset mirrors the service's `SAFE_NAME_RE` (service/api_core/validation.py). Without the DOT
  // this compared a TRUNCATED id against the full one: for `team.coder` it extracted `team`, took
  // the `!==` branch, and reported "positively a different agent" about the agent's OWN process —
  // so Stop was permanently refused for any agent whose id contains a dot, which the API accepts.
  const m = cmdline.match(/--aify-agent[=\s]+([A-Za-z0-9][A-Za-z0-9._-]*)/);
  if (m && m[1] && m[1] !== wantAgent) return false; // POSITIVELY a different agent → recycled pid, skip
  return true;
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
