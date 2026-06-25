#!/usr/bin/env node
// Pure tests for the claude hook-independent turn-state detector (pure-event-status
// change #1, 2026-06-02; rewritten 2026-06-02 to a STRUCTURAL signal; made
// BIDIRECTIONAL 2026-06-02 to also SET working — fixes RESIDENT-claude
// under-report where a channel-woken / scheduled turn never fires
// UserPromptSubmit→/turn-start so turn_busy is never set).
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
//          step, or an assistant mid-stream (null stop_reason).
//   UNKNOWN  null/unreadable tail or no last role.
//
// BIDIRECTIONAL, edge-triggered, idempotent: observe() returns a DIRECTIVE
//   "start" — on a transition into IN-FLIGHT from ended/unknown (POST /turn-start)
//   "end"   — on a transition into ENDED from in-flight/unknown (POST /turn-end)
//   null    — no transition (steady state, or UNKNOWN which never flips anything)
// so it never spams either endpoint, and re-arms in BOTH directions across turns.
//
// ANTI-FEEDBACK-LOOP: the detector keys ONLY on transcript STRUCTURE (process
// truth), NEVER on the server's computed status — so it cannot self-reinforce.
import assert from "node:assert/strict";
import { makeTurnEndDetector, classify } from "../turn-end-detector.js";

// Terminal (yielded-to-user) stop reasons that mean the turn ENDED.
const ENDED = (stopReason) => ({ lastRole: "assistant", lastStopReason: stopReason, pendingToolUse: false });
// A long build / pending tool / sub-agent dispatch: last assistant awaiting a tool.
const TOOL_USE = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true };
// A trailing user line (prompt or tool_result) — the model owes the next step.
const USER_PENDING = { lastRole: "user", lastStopReason: null, pendingToolUse: false };

// (a) the very first ENDED summary emits "end" once (a completed turn we just
//     observed at boot); the same ended tail again emits nothing.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(ENDED("end_turn")), "end", "last assistant end_turn -> END");
  assert.equal(d.observe(ENDED("end_turn")), null, "same ended tail again -> no re-fire");
}

// (b) an in-flight tail when state is unknown/cleared emits "start" ONCE, then
//     steady in-flight emits nothing (no /turn-start spam every tick).
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), "start", "first in-flight (tool_use) -> START (set working)");
  assert.equal(d.observe(TOOL_USE), null, "still tool_use -> no re-fire");
}

// (c) sub-agent dispatch: parent transcript static, last assistant = tool_use (Task),
//     pendingToolUse true across many ticks -> START once, then nothing (stays working).
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), "start", "sub-agent dispatch begins -> START");
  for (let i = 0; i < 10; i++) {
    assert.equal(d.observe(TOOL_USE), null, `sub-agent dispatch tick ${i} -> no fire (stays working)`);
  }
}

// (d) full bidirectional re-arm: in-flight->ended->new-in-flight->ended fires
//     start, end, start, end.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), "start", "turn 1 in-flight -> START");
  assert.equal(d.observe(ENDED("end_turn")), "end", "turn 1 ended -> END");
  assert.equal(d.observe(ENDED("end_turn")), null, "turn 1 still ended -> no re-fire");
  assert.equal(d.observe(TOOL_USE), "start", "turn 2 in-flight -> START again (re-armed)");
  assert.equal(d.observe(ENDED("end_turn")), "end", "turn 2 ended -> END again (re-armed)");
}

// (e) null / unreadable tail NEVER fires either way and does NOT change state
//     (transient stat failure / unresolved session id is not evidence of anything).
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(null), null, "null summary -> no fire");
  assert.equal(d.observe(undefined), null, "undefined summary -> no fire");
  assert.equal(d.observe({}), null, "empty summary (no lastRole) -> no fire");
}

// (f) between-tool-calls: a trailing user/tool_result line means the model owes
//     the next step -> in-flight; first one STARTs, the rest are steady.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(USER_PENDING), "start", "trailing user/tool_result -> in-flight START");
  assert.equal(d.observe(TOOL_USE), null, "then a tool_use -> still in-flight, no fire");
}

// (g) other terminal stop reasons (stop_sequence, max_tokens) also end once.
{
  for (const sr of ["stop_sequence", "max_tokens"]) {
    const d = makeTurnEndDetector();
    assert.equal(d.observe(TOOL_USE), "start", `${sr}: in-flight first -> START`);
    assert.equal(d.observe(ENDED(sr)), "end", `stop_reason ${sr} -> END`);
    assert.equal(d.observe(ENDED(sr)), null, `stop_reason ${sr} again -> no re-fire`);
  }
}

// (h) an unreadable tick in the MIDDLE of a turn does not lose state: a null
//     between an in-flight tail and an ended tail still ends on the ended tail,
//     and does not re-START.
{
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), "start", "in-flight -> START");
  assert.equal(d.observe(null), null, "transient unreadable -> no fire, no state loss");
  assert.equal(d.observe(ENDED("end_turn")), "end", "ended after transient -> END");
}

// (i) an assistant message with an unknown / null stop_reason (mid-stream, not yet
//     a terminal yield) is treated as in-flight -> START once.
{
  const d = makeTurnEndDetector();
  assert.equal(
    d.observe({ lastRole: "assistant", lastStopReason: null, pendingToolUse: false }),
    "start",
    "assistant null stop_reason -> mid-stream in-flight, START",
  );
  assert.equal(
    d.observe({ lastRole: "assistant", lastStopReason: null, pendingToolUse: false }),
    null,
    "still mid-stream -> no re-fire",
  );
}

// (j) RESIDENT under-report repro: a turn that begins WITHOUT a /turn-start hook
//     (channel-woken / scheduled — no UserPromptSubmit) is observed by the
//     detector mid-flight and STARTs working, then ENDs when it yields.
{
  const d = makeTurnEndDetector();
  // first observation of this turn is already mid-tool (the hook never fired).
  assert.equal(d.observe(TOOL_USE), "start", "channel-woken turn observed in-flight -> START (the fix)");
  assert.equal(d.observe(USER_PENDING), null, "still in-flight -> no fire");
  assert.equal(d.observe(ENDED("end_turn")), "end", "yields -> END");
}

// (k) INTERACTIVE-YIELD tools (AskUserQuestion / ExitPlanMode): the assistant's tail
//     is a PENDING tool_use, but the tool BLOCKS the turn awaiting a HUMAN and never
//     auto-resumes via PostToolUse — so the turn has yielded and the agent is IDLE,
//     not working. classify() must report "ended" (despite stop_reason "tool_use") so
//     the Stop-gate posts /turn-end and the detector clears working. Repro: a resident
//     claude stuck at `working` for the ENTIRE human wait (comms-tech-lead, 2h).
{
  const yield_ = (name) => ({ lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true, pendingToolNames: [name] });
  assert.equal(classify(yield_("AskUserQuestion")), "ended", "(k) pending AskUserQuestion -> yielded to human -> ended");
  assert.equal(classify(yield_("ExitPlanMode")), "ended", "(k) pending ExitPlanMode -> yielded for approval -> ended");
  // NO FLICKER REGRESSION: a generic pending tool (real work about to run, or a premature
  // mid-turn Stop that resumes via PostToolUse) stays in-flight — unchanged behavior.
  assert.equal(classify(yield_("Bash")), "in-flight", "(k) pending Bash -> real work -> in-flight (unchanged)");
  // SAFE on a mix: only clear when EVERY pending tool is a yielding one (never strand real work).
  assert.equal(
    classify({ lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true, pendingToolNames: ["AskUserQuestion", "Bash"] }),
    "in-flight",
    "(k) AskUserQuestion batched with Bash -> in-flight (do not clear while real work pending)",
  );
  // Backward-compat: a summary WITHOUT pendingToolNames behaves exactly as before.
  assert.equal(classify({ lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true }), "in-flight",
    "(k) no pendingToolNames field -> in-flight (legacy summary unchanged)");
}

// (l) DETECTOR: a persistent AskUserQuestion tail settles to ENDED once and does NOT
//     keep re-setting working (the 2h-stuck repro: the detector held it in-flight
//     forever). On answering, a new in-flight turn re-arms START.
{
  const ask = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true, pendingToolNames: ["AskUserQuestion"] };
  const d = makeTurnEndDetector();
  assert.equal(d.observe(TOOL_USE), "start", "(l) active turn -> START (working)");
  assert.equal(d.observe(ask), "end", "(l) yields to AskUserQuestion -> END (idle, awaiting human)");
  for (let i = 0; i < 5; i++) assert.equal(d.observe(ask), null, `(l) still awaiting answer tick ${i} -> no re-set working`);
  assert.equal(d.observe(USER_PENDING), "start", "(l) human answered (tool_result) -> new turn START");
}

console.log("turn-end-detector.test.js: all assertions passed");
