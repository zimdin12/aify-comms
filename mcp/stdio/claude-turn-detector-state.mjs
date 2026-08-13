// The claude turn-END detector: its state, and the three operations over it.
//
// v0.5.4 layer 0 of the server.js decomposition, Option A of `docs/JS_DETECTOR_TEARDOWN_PACKET.md` —
// relocation behind operations, with the teardown-registry reshape parked by the reviewer.
//
// THREE PIECES OF STATE, AND NONE OF THEM LEAVES THIS MODULE. `__effectiveAgentId` is read from seven
// places, all of them inside the arming function and its callbacks. `__claudeTurnDetectorArmed` has one
// reader outside (a re-arm check in the register path) and `__stopClaudeTurnEndDetector` has one caller
// outside (`cleanupOnExit`) — so two predicates and a stop operation cover every external need, and the
// raw handle stays private. That was the reviewer's condition, and it was measured before it was assumed:
// a handle added or replaced from outside would leave the detector running with nothing able to stop it.
//
// `stopClaudeTurnEndDetector` IS A FUNCTION, not the handle it wraps. Before this move `cleanupOnExit`
// called a mutable `let` that held either a no-op or the real stopper; now it calls a stable export that
// dispatches to whichever the module holds. Same behaviour, and the handle is no longer reachable.
//
// EFFECTIVE IDENTITY IS NOT LAUNCH IDENTITY, which is why this module holds it rather than
// `launch-identity.mjs`. `AIFY_AGENT_ID` is what the wrapper handed this process; the effective id is who
// the bridge is ACTING as, and it can change once — the comment below records the incident that made it a
// variable. A session launched without `--aify-agent` has no launch identity, learns one at
// `comms_register`, and the detector must arm THEN. It used to arm only at module load, so registering
// silently failed to turn status on: the general-manager incident.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { IS_REMOTE, httpCall } from "./aify-service-endpoint.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { startClaudeTurnEndDetector } from "./claude-turn-end-detector.js";
import { AIFY_AGENT_ID } from "./launch-identity.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";

// Claude hook-independent turn-END detector (pure-event-status change #1,
// 2026-06-02). The claude Stop hook (install.sh -> POST /turn-end) is NOT a
// guaranteed turn terminator — it misses on interrupt/ESC, MCP-continuations, a
// crash, or when its short-timeout curl fails. A missed Stop hook leaves the
// agent turn_busy=1 with no event to clear it (the sc-claude "stuck at
// turn_busy=1" symptom), and with STATUS now pure-event (no short status window)
// that would read `working` until the single long ceiling. This loop reads a
// STRUCTURAL summary of the transcript TAIL (transcriptTail → { lastRole,
// lastStopReason, pendingToolUse }) and fires /turn-end ONLY when the last
// assistant message YIELDED to the user (terminal stop_reason, no pending
// tool_use) — an event-driven turn-end independent of the Stop hook, for BOTH
// resident and managed claude (same wrapper). The Stop hook stays the fast-path
// clear; this is the backstop. NOT growth-based: the parent transcript is STATIC
// during a long blocking tool call, a long generation, or a Task sub-agent
// dispatch (sub-agents write a SEPARATE subagents/*.jsonl), so a "stopped
// growing" signal would FALSE-CLEAR turn_busy mid-turn — reading tail STRUCTURE
// keeps the agent `working` through all of those. ANTI-FEEDBACK-LOOP: keys ONLY
// on transcript STRUCTURE (process truth), never on the server's computed status,
// and only ever POSTs /turn-end (a CLEAR) — it can never re-arm turn_busy. A
// null/unreadable tail is treated as NOT-ended (never false-clear).
// LATE IDENTITY (2026-07-14). This detector used to be armed ONCE, at module load, from the
// AIFY_AGENT_ID env var — so a session launched without `--aify-agent` never armed it, and
// NOTHING later could. But `comms_register` is precisely the moment the bridge LEARNS its
// agent id (it writes the binding file from it). Registering therefore *should* turn status
// on, and operators reasonably expect it to. It didn't, silently — the general-manager
// incident. So: the effective agent id is a variable, not a constant, and the detector can be
// armed late by `armClaudeTurnEndDetector(agentId)` from the register handler.
let __effectiveAgentId = AIFY_AGENT_ID;
let __claudeTurnDetectorArmed = false;
let __stopClaudeTurnEndDetector = () => {};

export function armClaudeTurnEndDetector(agentId) {
  const id = String(agentId || "").trim();
  if (__claudeTurnDetectorArmed || !id) return false;
  if (
    !__runtimeAdapter ||
    __runtimeAdapter.name !== "claude-code" ||
    typeof __runtimeAdapter.transcriptTail !== "function"
  ) {
    return false;
  }
  __effectiveAgentId = id;
  __claudeTurnDetectorArmed = true;
  __stopClaudeTurnEndDetector = startClaudeTurnEndDetector({
    // PURE-EVENT (2026-06-19): 30s→5s. With the server-side turn-end GRACE removed, this
    // structural detector IS the flap fix for a managed claude's premature/duplicate Stop
    // hooks: a premature Stop clears turn_busy, and this detector re-asserts /turn-start
    // within one tick once it sees the transcript is still in-flight (pendingToolUse / non-
    // terminal tail). 5s keeps that heal window short (was up to 30s) without meaningful
    // transcript-read load. Mirrors the hermes gateway detector's ~3s cadence.
    intervalMs: 5_000,
    // Re-stamp /turn-start while the transcript stays in-flight (KEEP-FRESH,
    // 2026-06-12): the server's delivery-completion clear can wipe a LIVE turn's
    // turn_busy (steered message lands mid-turn → no reply-owing run → clear), and
    // an edge-triggered start never re-fires. Same value as the hermes detector.
    workingRefreshMs: 45_000,
    readTranscript: async () => __runtimeAdapter.transcriptTail({ agentId: __effectiveAgentId }),
    // SET working when the transcript tail transitions into in-flight. RESIDENT
    // under-report fix (2026-06-02): a channel-woken / scheduled claude turn never
    // fires UserPromptSubmit→/turn-start, so turn_busy stays 0 and the dashboard
    // shows the agent NOT working. Keying on the transcript (process truth) covers
    // typed, channel-woken, AND scheduled turns — the robust replacement for the
    // removed PostToolUse re-pulse. Idempotent (edge-triggered in the detector).
    postTurnStart: async () => {
      if (!__effectiveAgentId || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(__effectiveAgentId)}/turn-start`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "claude-code",
        source: "bridge-transcript-detector",
      });
    },
    postTurnEnd: async () => {
      if (!__effectiveAgentId || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(__effectiveAgentId)}/turn-end`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "claude-code",
        source: "bridge-transcript-detector",
      });
    },
  });
  return true;
}

// The two operations `server.js` needs, so the state above never leaves this module.
//
// A stable function rather than the mutable handle: `cleanupOnExit` used to call a `let` that held either a
// no-op or the real stopper, and the reviewer's rule is that a raw handle only leaves a module when
// measurement proves an external consumer needs the handle ITSELF. It does not — it needs the effect.
export function stopClaudeTurnEndDetector() {
  __stopClaudeTurnEndDetector();
}

// Read by the register path, which must not arm a second detector over a live one.
export function isClaudeTurnDetectorArmed() {
  return __claudeTurnDetectorArmed;
}
