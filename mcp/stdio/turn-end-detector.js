// The Stop gate's structural judgement of a claude transcript tail: has the turn ended, is it in flight, or is
// there no evidence? claude-stop-gate.js suppresses a premature mid-turn Stop iff this says "in-flight".
//
// WHY STRUCTURAL, NOT GROWTH-BASED: the claude session transcript grows PER COMPLETED MESSAGE, not per token, and
// Task sub-agents write to a SEPARATE subagents/*.jsonl, so the PARENT file does NOT grow during a long blocking
// tool call, a long generation, or a sub-agent dispatch. The tail's structure says what growth cannot.
//
// The summary is summarizeTranscriptTail's (adapters/claude.js): { lastRole, lastStopReason, pendingToolUse,
// pendingToolNames }. A turn has ENDED only when the last assistant message YIELDED to the user: stop_reason in
// {end_turn, stop_sequence, max_tokens} with NO pending tool_use after it, or a pending tool that waits on a human.
// Anything else with a known last role is IN-FLIGHT. A null/unreadable tail is UNKNOWN.
//
// The edge-triggered detector that once posted turns from this went with the bridge turn posts (0.9.9 S2b; deleted
// 0.9.10). The file keeps its name because the Stop gate and its tests import it by that name.

// stop_reasons that mean the assistant yielded the turn back to the user.
const TERMINAL_STOP_REASONS = new Set(["end_turn", "stop_sequence", "max_tokens"]);

// Tools whose pending call YIELDS the turn to a HUMAN and blocks until they respond
// (AskUserQuestion → an answer; ExitPlanMode → plan approval). Unlike a real work tool,
// these never auto-resume via PostToolUse — so an assistant tail whose pending tool_use
// is one of these has YIELDED and the agent is IDLE awaiting input, even though
// stop_reason is "tool_use". Without this, such a tail reads as in-flight and strands
// the agent at `working` for the ENTIRE human wait (the resident-claude / comms-tech-lead
// "stuck working while idle, 2h" bug, 2026-06-20). A GENERIC pending tool stays in-flight,
// so this never reintroduces the premature-Stop working→online→working flicker.
const YIELDING_TOOLS = new Set(["AskUserQuestion", "ExitPlanMode"]);

// Decide turn state from a structural tail summary.
// Returns "ended" | "in-flight" | "unknown".
// On "ended"/"unknown" the Stop gate falls through to the normal turn-end.
export function classify(summary) {
  if (!summary || typeof summary !== "object") return "unknown";
  const { lastRole, lastStopReason, pendingToolUse, pendingToolNames } = summary;
  if (!lastRole) return "unknown";
  // The operator interrupted the turn (Esc): claude is idle and nothing will answer the marker
  // (`isInterruptMarker`, adapters/claude.js; v0.7.4).
  if (summary.interrupted === true) return "ended";
  // A turn ENDED iff the last message is an assistant that yielded to the user
  // (terminal stop_reason) with no pending tool_use awaiting a result.
  if (
    lastRole === "assistant" &&
    !pendingToolUse &&
    TERMINAL_STOP_REASONS.has(lastStopReason)
  ) {
    return "ended";
  }
  // A turn also YIELDED (idle, not working) when its tail is an assistant whose pending
  // tool_use is EXCLUSIVELY interactive-yield tool(s) (AskUserQuestion / ExitPlanMode):
  // the harness blocks the turn awaiting a human and it never auto-resumes. Require EVERY
  // pending tool to be a yielding one, so a yield tool batched with real work stays
  // in-flight (never strand pending work).
  if (
    lastRole === "assistant" &&
    Array.isArray(pendingToolNames) &&
    pendingToolNames.length > 0 &&
    pendingToolNames.every((name) => YIELDING_TOOLS.has(name))
  ) {
    return "ended";
  }
  // Anything else with a known last role is the model still owing work:
  // assistant stop_reason "tool_use" (long build / pending tool / sub-agent),
  // a trailing user/tool_result, or an assistant mid-stream (null stop_reason).
  return "in-flight";
}
