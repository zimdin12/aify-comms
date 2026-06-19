// Claude hook-independent turn-STATE detector (pure-event-status change #1,
// 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal; made BIDIRECTIONAL
// 2026-06-02 to also SET working).
//
// WHY (clear direction): the claude Stop hook (install.sh -> POST /turn-end) is
// NOT a guaranteed turn terminator. It misses on interrupt/ESC, MCP-continuations,
// a crash, or when its short-timeout curl fails. When it misses, the agent stays
// turn_busy=1 with no event to clear it — the observed sc-claude "stuck at
// turn_busy=1" symptom.
//
// WHY (set direction — the RESIDENT under-report fix): a resident claude's only
// turn-START signal is UserPromptSubmit→/turn-start, which fires ONLY for
// operator-TYPED prompts. A channel-woken or scheduled-task turn never fires it,
// so turn_busy is never set and the dashboard shows the agent NOT working while it
// is. (The PostToolUse re-pulse that used to cover this was removed in the
// pure-event batch, 8efbbaf.) This detector is the hook-independent backstop in
// BOTH directions: it reads the transcript tail and SETs working when a turn is
// in-flight and CLEARs when it yields — works for typed, channel-woken, AND
// scheduled turns because it reads process truth, not a hook.
//
// WHY STRUCTURAL, NOT GROWTH-BASED: the claude session transcript grows PER
// COMPLETED MESSAGE, not per token, and Task sub-agents write to a SEPARATE
// subagents/*.jsonl — the PARENT session file does NOT grow during a long blocking
// tool call (build/test >30s), a long generation, or any sub-agent dispatch. A
// "stopped growing for one tick" signal therefore FALSE-CLEARED turn_busy mid-turn.
// So we read the transcript TAIL STRUCTURE.
//
// HOW: the adapter produces a small structural summary of the transcript tail:
//   { lastRole, lastStopReason, pendingToolUse }
// derived from the real JSONL schema (see adapters/claude.js transcriptTail).
// A claude turn has ENDED only when the last assistant message YIELDED to the
// user: stop_reason in {end_turn, stop_sequence, max_tokens} with NO pending
// tool_use after it. Anything else with a known last role is IN-FLIGHT: a trailing
// assistant stop_reason "tool_use" (a long build, a pending tool, or a Task
// sub-agent dispatch), a trailing user/tool_result feeding the next step, or an
// assistant mid-stream with no terminal stop_reason. A null/unreadable tail is
// UNKNOWN.
//
// EDGE-TRIGGERED + IDEMPOTENT: observe() returns a DIRECTIVE
//   "start" — transition into IN-FLIGHT from ended/unknown (POST /turn-start once)
//   "end"   — transition into ENDED   from in-flight/unknown (POST /turn-end once)
//   null    — steady state, or UNKNOWN (which never flips state in either direction)
// so neither endpoint is spammed every tick, and a new turn after an end re-sets
// working (re-armed in BOTH directions).
//
// ANTI-FEEDBACK-LOOP INVARIANT: this detector keys ONLY on transcript STRUCTURE
// (process truth), NEVER on the server's computed status — so it can never
// self-reinforce a derived status into turn_busy.
//
// FALSE-CLEAR / FALSE-SET SAFETY: a null/unreadable/unrecognized summary is UNKNOWN
// and does NOT change the last-known state, so a transient unreadable tick between
// an in-flight tail and the eventual ended tail still ends correctly (and a
// transient between two in-flight ticks does not spuriously re-start).

// stop_reasons that mean the assistant yielded the turn back to the user.
const TERMINAL_STOP_REASONS = new Set(["end_turn", "stop_sequence", "max_tokens"]);

// Decide turn state from a structural tail summary.
// Returns "ended" | "in-flight" | "unknown".
// Exported so the Stop-hook gate (claude-stop-gate.js) can reuse the SAME structural
// truth the detector uses — a premature Stop fired mid-turn is suppressed iff this says
// "in-flight"; on "ended"/"unknown" the gate falls through to the normal /turn-end.
export function classify(summary) {
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
  // last === the last DEFINITE state we acted on: "ended" | "in-flight" | null
  // (null at boot = unknown, so the first definite observation in either
  // direction fires once — covers a turn that ended OR began just before the
  // detector booted). An "unknown" tick never changes `last`, preserving the
  // edge across a transient unreadable read.
  let last = null;

  return {
    // observe(summary): feed one structural tail summary
    // ({ lastRole, lastStopReason, pendingToolUse } | null). Returns a directive
    // exactly on a transition tick:
    //   "start" -> POST /turn-start (set working)
    //   "end"   -> POST /turn-end   (clear working)
    //   null    -> no action this tick.
    observe(summary) {
      const state = classify(summary);
      // Unknown / unreadable: not evidence of anything. Do not fire and do not
      // change last state (edge-preservation across a transient).
      if (state === "unknown") return null;
      if (state === last) return null; // steady state -> idempotent, no fire.
      // A real transition: record it and emit the matching directive once.
      last = state;
      return state === "in-flight" ? "start" : "end";
    },
  };
}
