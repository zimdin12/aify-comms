// Claude hook-independent turn-END detector (pure-event-status change #1,
// 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal).
//
// WHY: the claude Stop hook (install.sh -> POST /turn-end) is NOT a guaranteed
// turn terminator. It misses on interrupt/ESC, MCP-continuations, a crash, or
// when its short-timeout curl fails. When it misses, the agent stays
// turn_busy=1 with no event to clear it — the observed sc-claude "stuck at
// turn_busy=1" symptom. With STATUS now pure-event (change #3 drops the short
// status window), a missed Stop hook would leave the agent `working` until the
// single long ceiling. This detector gives claude an EVENT-DRIVEN turn-end that
// does not depend on the Stop hook firing, by watching the transcript directly.
//
// WHY STRUCTURAL, NOT GROWTH-BASED (the ship-blocker this rewrite fixes): the
// claude session transcript grows PER COMPLETED MESSAGE, not per token, and Task
// sub-agents write to a SEPARATE subagents/*.jsonl — the PARENT session file does
// NOT grow during a long blocking tool call (build/test >30s), a long generation,
// or any sub-agent dispatch. A "stopped growing for one tick" signal therefore
// FALSE-CLEARED turn_busy mid-turn (agent shows idle while actually working),
// firing constantly for a team that runs parallel sub-agents. So we no longer
// infer turn-end from "stopped growing"; we read the transcript TAIL STRUCTURE.
//
// HOW: the adapter produces a small structural summary of the transcript tail:
//   { lastRole, lastStopReason, pendingToolUse }
// derived from the real JSONL schema (see adapters/claude.js transcriptTail).
// A claude turn has ENDED only when the last assistant message YIELDED to the
// user: stop_reason in {end_turn, stop_sequence, max_tokens} with NO pending
// tool_use after it. Every other tail is IN-FLIGHT: a trailing assistant
// stop_reason "tool_use" (a long build, a pending tool, or a Task sub-agent
// dispatch), a trailing user/tool_result feeding the next step, an assistant
// mid-stream with no terminal stop_reason, or a null/unreadable tail. We fire
// /turn-end ONCE per ended turn and RE-ARM when a new in-flight turn is observed.
//
// ANTI-FEEDBACK-LOOP INVARIANT: this detector keys ONLY on transcript STRUCTURE
// (process truth), NEVER on the server's computed status — so it can never
// self-reinforce a derived status into turn_busy. It only ever CLEARS (fires
// turn-end); it never sets turn_busy.
//
// FALSE-CLEAR SAFETY: a null/unreadable/unrecognized summary is treated as
// NOT-ended (a failed read is not evidence the turn ended) and does NOT disturb
// the armed state, so a transient unreadable tick between an in-flight tail and
// the eventual ended tail still fires correctly.

// stop_reasons that mean the assistant yielded the turn back to the user.
const TERMINAL_STOP_REASONS = new Set(["end_turn", "stop_sequence", "max_tokens"]);

// Decide turn state from a structural tail summary.
// Returns "ended" | "in-flight" | "unknown".
function classify(summary) {
  if (!summary || typeof summary !== "object") return "unknown";
  const { lastRole, lastStopReason, pendingToolUse } = summary;
  if (!lastRole) return "unknown";
  // A turn ENDED iff the last message is an assistant that yielded to the user
  // (terminal stop_reason) with no pending tool_use awaiting a result.
  if (
    lastRole === "assistant" &&
    !pendingToolUse &&
    TERMINAL_STOP_REASONS.has(lastStopReason)
  ) {
    return "ended";
  }
  // Anything else with a known last role is the model still owing work:
  // assistant stop_reason "tool_use" (long build / pending tool / sub-agent),
  // a trailing user/tool_result, or an assistant mid-stream (null stop_reason).
  return "in-flight";
}

export function makeTurnEndDetector() {
  // armed === true once we have seen an IN-FLIGHT (or any non-ended, known)
  // turn that we have NOT yet fired turn-end for. We also arm on construction so
  // the FIRST observation of an already-ended tail still fires once (covers a
  // turn that ended just before the detector booted). After firing we disarm and
  // only re-arm on the next in-flight observation, so an ended tail that lingers
  // across ticks fires at most once per turn.
  let armed = true;

  return {
    // observe(summary): feed one structural tail summary
    // ({ lastRole, lastStopReason, pendingToolUse } | null). Returns true exactly
    // on the tick that should POST /turn-end, false otherwise.
    observe(summary) {
      const state = classify(summary);
      // Unknown / unreadable: not evidence of anything. Do not fire and do not
      // change arming (false-clear safety; preserves arming across a transient).
      if (state === "unknown") return false;
      if (state === "in-flight") {
        // A turn is running — (re-)arm so its eventual end fires.
        armed = true;
        return false;
      }
      // state === "ended": fire once iff armed, then disarm until a new in-flight
      // turn re-arms us.
      if (armed) {
        armed = false;
        return true;
      }
      return false;
    },
  };
}
