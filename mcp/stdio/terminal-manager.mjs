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
import { httpCall } from "./aify-service-endpoint.mjs";
import { REMOTE_AGENT_STATE } from "./bridge-agent-state.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { decideConsolePulse } from "./console-pulse.mjs";
import { TerminalProcessManager } from "./terminal-runtime.js";

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
