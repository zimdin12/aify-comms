#!/usr/bin/env node
// Pure tests for classify, the Stop gate's structural judgement of a claude transcript tail (claude-stop-gate.js
// suppresses a premature mid-turn Stop iff this says "in-flight"). The edge-triggered detector built on it went
// with the bridge turn posts (0.9.9 S2b, deleted 0.9.10).
//
// It reads a structural summary { lastRole, lastStopReason, pendingToolUse, pendingToolNames } and decides:
//   ENDED  iff the last assistant message YIELDED to the user -- stop_reason in
//          {end_turn, stop_sequence, max_tokens} with NO pending tool_use, or a pending tool that waits on a human.
//   IN-FLIGHT otherwise: stop_reason "tool_use" (long build / pending tool / a Task sub-agent dispatch), a
//          trailing user/tool_result feeding the next step, or an assistant mid-stream (null stop_reason).
//   UNKNOWN  null/unreadable tail or no last role.
import assert from "node:assert/strict";
import { classify } from "../turn-end-detector.js";

// Terminal (yielded-to-user) stop reasons that mean the turn ENDED.
const ENDED = (stopReason) => ({ lastRole: "assistant", lastStopReason: stopReason, pendingToolUse: false });
// A long build / pending tool / sub-agent dispatch: last assistant awaiting a tool.
const TOOL_USE = { lastRole: "assistant", lastStopReason: "tool_use", pendingToolUse: true };
// A trailing user line (prompt or tool_result) -- the model owes the next step.
const USER_PENDING = { lastRole: "user", lastStopReason: null, pendingToolUse: false };

// (a) every terminal stop reason ends the turn.
for (const sr of ["end_turn", "stop_sequence", "max_tokens"]) assert.equal(classify(ENDED(sr)), "ended", `(a) ${sr} -> ended`);

// (b) work still owed: a pending tool (a long build, a sub-agent dispatch), a trailing user/tool_result, or an
//     assistant mid-stream with no stop reason yet.
assert.equal(classify(TOOL_USE), "in-flight", "(b) pending tool_use -> in-flight");
assert.equal(classify(USER_PENDING), "in-flight", "(b) trailing user/tool_result -> in-flight");
assert.equal(classify({ lastRole: "assistant", lastStopReason: null, pendingToolUse: false }), "in-flight",
  "(b) assistant null stop_reason -> mid-stream, in-flight");

// (c) nothing readable is no evidence either way.
for (const summary of [null, undefined, {}]) assert.equal(classify(summary), "unknown", `(c) ${JSON.stringify(summary)} -> unknown`);

// (k) INTERACTIVE-YIELD tools (AskUserQuestion / ExitPlanMode): the assistant's tail
//     is a PENDING tool_use, but the tool BLOCKS the turn awaiting a HUMAN and never
//     auto-resumes via PostToolUse — so the turn has yielded and the agent is IDLE,
//     not working. classify() must report "ended" (despite stop_reason "tool_use") so
//     the Stop-gate posts /turn-end. Repro: a resident
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

console.log("turn-end-detector.test.js: all assertions passed");
