// The bridge's terminal process manager, and the two activity pulses it emits.
//
// `TERMINAL_MANAGER` owns every pty this bridge has open. Its `onOutput` callback does two things: forward
// the bytes to the service, and decide whether that output means the agent is WORKING.
//
// THE TWO PULSES ARE NOT THE SAME THING and the distinction is load-bearing:
//
//   pulseConsoleWorking     — the console is producing output right now. Recorded per terminal and used to
//                             tell whether a turn is in flight, which lets a transient "unknown" footer
//                             frame mid-turn be bridged instead of collapsing the status.
//   pulseTerminalTurnBusy   — tells the service the agent is busy, so dispatch does not deliver into a
//                             turn that is already running.
//
// Both self-clear after a quiet window rather than latching, because a pulse that never expires turns one
// burst of output into a permanently busy agent.
//
// Extracted from server.js in v0.5.4 as a measured group: nine declarations whose whole external surface is
// six names server.js already imports. `docs/JS_SERVER_REMAINDER_PACKET.md` measured the remainder per
// function and found nothing here; that criterion is corrected in an appendix to the packet.
//
// CONSTRUCTED AT IMPORT, exactly as it was at server.js's module scope — the constructor registers
// callbacks and starts nothing, so the timing is unchanged. `cleanupOnExit` still calls `stopAll()` from
// server.js, so the documented shutdown ordering (stopAll before the managed teardown) is untouched: the
// caller did not move.
//
// Bodies are byte-identical to those in server.js; the only substitution is the added `export `.


import { reportTurnBusy } from "./agent-heartbeat.mjs";
import { IS_REMOTE, httpCall, logTransientOrError } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { decideConsolePulse } from "./console-pulse.mjs";
import { isEnabled } from "./env-client.mjs";
import { reportDeadOwnedSessions } from "./dead-pty-reporter.js";
import { IS_ENVIRONMENT_BRIDGE } from "./launch-identity.mjs";
import { TerminalProcessManager, bridgeTerminalSupported } from "./terminal-runtime.js";

// Terminal-activity-driven turn-busy pulses. When a managed PTY produces
// sustained output (claude-aify, pi-aify, etc. working autonomously
// BETWEEN dispatch runs), the backend status engine has no authoritative
// signal that the agent is busy — dispatch_run is completed, no managed
// worker heartbeat. So the agent shows "active" while clearly working,
// which the operator has flagged repeatedly. This emits a debounced
// turn_busy=true while terminal output is fresh, and clears it after a
// short quiet window. Additive to authoritative signals: an active
// dispatch_run still keeps status='working' independently via the
// backend's status engine; this just fills the autonomous-work gap.
export const TERMINAL_TURN_BUSY_REMIT_MS = 5000;
export const TERMINAL_TURN_BUSY_QUIET_MS = 8000;
export const TERMINAL_TURN_BUSY_TIMERS = new Map();
export function pulseTerminalTurnBusy(terminalId, agentId) {
  const aid = String(agentId || "").trim();
  if (!aid) return;
  let entry = TERMINAL_TURN_BUSY_TIMERS.get(terminalId);
  if (!entry) {
    entry = { agentId: aid, lastEmit: 0, timer: null };
    TERMINAL_TURN_BUSY_TIMERS.set(terminalId, entry);
  }
  const now = Date.now();
  if (now - entry.lastEmit > TERMINAL_TURN_BUSY_REMIT_MS) {
    entry.lastEmit = now;
    const state = REMOTE_AGENT_STATE.get(aid) || {};
    reportTurnBusy(aid, state, { busy: true }).catch(() => {});
  }
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => {
    const state = REMOTE_AGENT_STATE.get(aid) || {};
    reportTurnBusy(aid, state, { busy: false }).catch(() => {});
    TERMINAL_TURN_BUSY_TIMERS.delete(terminalId);
  }, TERMINAL_TURN_BUSY_QUIET_MS);
}
export const CONSOLE_WORKING_REMIT_MS = 2000;
export const CONSOLE_WORKING_TURN_WINDOW_MS = 15000;
export const CONSOLE_WORKING_TIMERS = new Map();
export function pulseConsoleWorking(terminalId, agentId, subagents = false) {
  const aid = String(agentId || "").trim();
  if (!aid) return;
  const last = CONSOLE_WORKING_TIMERS.get(terminalId) || 0;
  const now = Date.now();
  if (now - last < CONSOLE_WORKING_REMIT_MS) return;
  CONSOLE_WORKING_TIMERS.set(terminalId, now);
  httpCall("POST", `/agents/${encodeURIComponent(aid)}/console-working`, { subagents: !!subagents }).catch(() => {});
}
export const TERMINAL_MANAGER = new TerminalProcessManager({
  // WIRED, because for a while it was not. The constructor defaults envDelegation to null, this call
  // site omitted it, and the whole flag was therefore a placebo — setting the environment variable did
  // nothing at all. A unit test of the seam could not see it, because those tests inject the very
  // dependency they are testing.
  //
  // Read at START time rather than at construction, so a value exported after this module loaded is
  // still honoured.
  envDelegation: { isEnabled: () => isEnabled(process.env) },
  onOutput: async (terminalId, output) => {
    await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/output`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      output,
      status: "attached",
    });
    // Status-precision pulse (mismatch #4): keep status='working' while
    // the agent's terminal is actively producing output even when no
    // dispatch_run is in flight. Self-clears after the quiet window.
    try {
      const st = TERMINAL_MANAGER.stateFor?.(terminalId) || {};
      // A turn is "known in flight" if we emitted a console-working pulse recently (claude showed
      // its spinner within the window) — used to bridge transient "unknown" footer frames mid-turn
      // without ever manufacturing working from a cold/idle console (see decideConsolePulse).
      const lastWorking = CONSOLE_WORKING_TIMERS.get(terminalId) || 0;
      const turnInFlight = lastWorking > 0 && (Date.now() - lastWorking) < CONSOLE_WORKING_TURN_WINDOW_MS;
      const decision = decideConsolePulse({
        runtime: st.runtime,
        consoleClass: st.consoleClass,
        agentId: st.agentId,
        turnInFlight,
      });
      if (decision.kind === "console-working") pulseConsoleWorking(terminalId, decision.agentId, st.subagentsActive);
      else if (decision.kind === "terminal-pulse") pulseTerminalTurnBusy(terminalId, decision.agentId);
    } catch {}
  },
  onExit: async (terminalId, detail = {}) => {
    const error = detail?.error?.message || "";
    await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/output`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      output: error ? `\n[terminal failed] ${error}\n` : `\n[terminal exited]\n`,
      status: error ? "failed" : "stopped",
    });
  },
  onHeal: async (_terminalId, detail = {}) => {
    const agentId = String(detail.agentId || "").trim();
    if (!agentId || !detail.previousSessionHandle) return;
    try {
      await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/session-handle`, {
        sessionHandle: "",
        requestedBy: "terminal-runtime-heal",
      });
    } catch (error) {
      console.error(`[aify] failed to clear stale ${detail.runtime || "runtime"} session handle for "${agentId}":`, error?.message || error);
    }
  },
  // Auto-answer managed-claude TUI prompts (resume/compaction/perms/channel) unless the
  // operator opts out with AIFY_NO_AUTO_ANSWER=1.
  autoAnswer: process.env.AIFY_NO_AUTO_ANSWER !== "1",
  // Repaint keepalive for managed claude PTYs so the console-working lease stays fresh when the
  // Console is closed (2026-06-05). Opt out with AIFY_NO_CONSOLE_KEEPALIVE=1; override cadence
  // with AIFY_CONSOLE_KEEPALIVE_MS.
  consoleKeepaliveMs: process.env.AIFY_NO_CONSOLE_KEEPALIVE === "1"
    ? 0
    : (Number(process.env.AIFY_CONSOLE_KEEPALIVE_MS) || 4000),
});

// ---------------------------------------------------------------------------------------------------
// Reporting terminals whose PTY has died, appended in a later v0.5.4 slice.
//
// It joins this module because it reports on the manager above: `TERMINAL_MANAGER.listOwnedSessions()` is
// its only source, and splitting a reader from the state it reads is what this series keeps undoing.
//
// It REPORTS, it does not reap — the service decides what to stop. That distinction is why this is an
// ordinary slice while `runManagedTeardownForBridge` and `runBootSurvivorSweep` are not: those kill
// processes, and docs/JS_SERVER_REMAINDER_PACKET.md flags them as needing a deliberate go-ahead.
//
// IT WAS WRONGLY MARKED BLOCKED for two slices. The closure survey reported it needing `server` — the MCP
// server instance, which cannot move — so it was set aside. The only occurrence of that word in the
// function is inside a log string: "reported to server for stop/reconcile". The survey deliberately does
// not strip string literals, because doing so is where three earlier parsers went wrong; the cost is
// exactly this, a false edge that refuses a movable group. When a closure names a surprising blocker, look
// at the reference before believing it.

// WS4 Task 4.2: host-reported dead-PTY marking. The server cannot probe a
// remote host pid; only the OWNING env bridge can. For each console PTY this
// bridge owns in-memory that is still `attached` but whose local pid is no
// longer alive, POST /terminals/{id}/report-dead so the server marks the row
// stopped + invalidates live-state (a frozen/crashed console can otherwise keep
// manufacturing presence). Best-effort; never throws. Does NOT kill anything —
// the in-memory exit path owns real teardown; this only reconciles stale rows.
export async function reportDeadOwnedTerminals() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || !bridgeTerminalSupported()) return [];
  try {
    const owned = TERMINAL_MANAGER.listOwnedSessions?.() || [];
    if (!owned.length) return [];
    return await reportDeadOwnedSessions(owned, {
      report: async ({ terminalId, pid }) => {
        await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/report-dead`, {
          bridgeId: BRIDGE_INSTANCE_ID,
          processId: pid != null ? String(pid) : "",
          reason: "Console PTY process is no longer alive (host-reported).",
        });
        console.error(`[aify] terminal ${terminalId} (pid ${pid}) is dead locally — reported to server for stop/reconcile`);
      },
    });
  } catch (error) {
    logTransientOrError("[aify] dead-PTY report failed", error);
    return [];
  }
}
