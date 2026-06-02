#!/usr/bin/env node
// Pure tests for the claude hook-independent turn-END detector (pure-event-status
// change #1, 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal).
//
// WHY structural, not growth-based: the original detector fired /turn-end after a
// SINGLE no-growth tick. But the claude session transcript grows PER COMPLETED
// MESSAGE, not per token, and Task sub-agents write to a SEPARATE subagents/*.jsonl
// — the PARENT session file does NOT grow during a long blocking tool call
// (build/test >30s), a long generation, or any sub-agent dispatch. So the
// growth-based detector FALSE-CLEARED turn_busy mid-turn (agent shows idle while
// actually working) — a ship-blocker for a team that runs parallel sub-agents.
//
// The fix reads the transcript TAIL STRUCTURE. The detector consumes a small
// structural summary { lastRole, lastStopReason, pendingToolUse } (produced by the
// adapter from the real JSONL schema) and decides:
//   ENDED  iff the last assistant message YIELDED to the user — stop_reason in
//          {end_turn, stop_sequence, max_tokens} with NO pending tool_use.
//   IN-FLIGHT otherwise: stop_reason "tool_use" (long build / pending tool / a
//          Task sub-agent dispatch), a trailing user/tool_result feeding the next
//          step, or an unknown/null tail.
// Fire /turn-end ONCE per ended turn; re-arm when a new in-flight turn starts.
// Null/unreadable summary => NOT-ended (never false-clear).
//
// ANTI-FEEDBACK-LOOP: the detector keys ONLY on transcript STRUCTURE (process
// truth), NEVER on the server's computed status — so it cannot self-reinforce. It
// only ever fires turn-end (a CLEAR); it never sets turn_busy.
import assert from "node:assert/strict";
import { makeTurnEndDetector } from "../turn-end-detector.js";

// Terminal (yielded-to-user) stop reasons that mean the turn ENDED.
const ENDED = (stopReason) => ({ lastRole: "assistant", lastStopReason: stopReason, pendingToolUse: false });
// A long build / pending tool / sub-agent dispatch: last assistant awaiting a tool.
const TOOL_USE = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true };
// A trailing user line (prompt or tool_result) — the model owes the next step.
const USER_PENDING = { lastRole: "user", lastStopReason: null, pendingToolUse: false };

// (a) the very first ENDED summary fires once (a completed turn we just observed).
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(ENDED("end_turn")), true, "last assistant end_turn -> FIRE turn-end");
  assert.equal(d.observe(ENDED("end_turn")), false, "same ended tail again -> do NOT re-fire");
}

// (b) a pending tool_use tail (long build / pending tool) NEVER fires — still working.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), false, "stop_reason tool_use -> still working, no fire");
  assert.equal(d.observe(TOOL_USE), false, "still tool_use -> no fire");
}

// (c) sub-agent dispatch: parent transcript static, last assistant = tool_use (Task),
//     pendingToolUse true across many ticks -> never fires.
{
  const d = makeTurnEndDetector();
  for (let i = 0; i < 10; i++) {
    assert.equal(d.observe(TOOL_USE), false, `sub-agent dispatch tick ${i} -> no fire`);
  }
}

// (d) end_turn fires exactly once, then a NEW in-flight turn re-arms and its
//     subsequent end_turn fires again.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(ENDED("end_turn")), true, "turn 1 ended -> FIRE");
  assert.equal(d.observe(ENDED("end_turn")), false, "turn 1 still ended -> no re-fire");
  // turn 2 begins (model working again on a new prompt / tool)
  assert.equal(d.observe(TOOL_USE), false, "turn 2 in-flight -> no fire (re-arms)");
  assert.equal(d.observe(ENDED("end_turn")), true, "turn 2 ended -> FIRE again (re-armed)");
}

// (e) null / unreadable tail NEVER fires (transient stat failure / unresolved
//     session id is NOT evidence the turn ended).
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(null), false, "null summary -> no fire");
  assert.equal(d.observe(undefined), false, "undefined summary -> no fire");
  assert.equal(d.observe({}), false, "empty summary (no lastRole) -> no fire");
}

// (f) between-tool-calls: a trailing user/tool_result line means the model owes
//     the next step -> in-flight, no fire.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(USER_PENDING), false, "trailing user/tool_result -> in-flight, no fire");
  assert.equal(d.observe(TOOL_USE), false, "then a tool_use -> in-flight, no fire");
}

// (g) other terminal stop reasons (stop_sequence, max_tokens) also fire once.
{
  for (const sr of ["stop_sequence", "max_tokens"]) {
    const d = makeTurnEndDetector();
    assert.equal(d.observe(ENDED(sr)), true, `stop_reason ${sr} -> FIRE`);
    assert.equal(d.observe(ENDED(sr)), false, `stop_reason ${sr} again -> no re-fire`);
  }
}

// (h) an unreadable tick in the MIDDLE of a turn does not lose arming: a null
//     between an in-flight tail and an ended tail still fires on the ended tail.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), false, "in-flight");
  assert.equal(d.observe(null), false, "transient unreadable -> no fire, no state loss");
  assert.equal(d.observe(ENDED("end_turn")), true, "ended after transient -> FIRE");
}

// (i) an assistant message with an unknown / null stop_reason (mid-stream, not yet
//     a terminal yield) is treated as in-flight, never fires.
{
  const d = makeTurnEndDetector();
  assert.equal(
    d.observe({ lastRole: "assistant", lastStopReason: null, pendingToolUse: false }),
    false,
    "assistant null stop_reason -> not a yield, no fire",
  );
}

console.log("turn-end-detector.test.js: all assertions passed");
