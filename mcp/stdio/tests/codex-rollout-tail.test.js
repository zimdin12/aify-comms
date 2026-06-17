#!/usr/bin/env node
// Tests for summarizeCodexRolloutTail (WS-4b, 2026-06-17). Codex rollout JSONL lines
// are { type, payload }; the parser walks from the END and returns the structural
// summary the generic turn detector consumes ({ lastRole, lastStopReason,
// pendingToolUse }). Fixtures mirror the REAL codex rollout schema sampled from a live
// session (2026): event_msg/task_complete ends a turn; response_item/message + tool
// lines and event_msg/task_started are in-flight; token_count is bookkeeping.
import assert from "node:assert/strict";
import { summarizeCodexRolloutTail } from "../adapters/codex.js";

const L = (o) => JSON.stringify(o);

// Helper: classify the way turn-end-detector does, for clarity in assertions.
function isEnded(s) {
  return s.lastRole === "assistant" && !s.pendingToolUse &&
    ["end_turn", "stop_sequence", "max_tokens"].includes(s.lastStopReason);
}

// 1. task_complete as the last meaningful line -> ENDED.
{
  const text = [
    L({ type: "response_item", payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: "done" }] } }),
    L({ type: "event_msg", payload: { type: "token_count", info: {} } }),
    L({ type: "event_msg", payload: { type: "task_complete", turn_id: "t1", last_agent_message: "done" } }),
  ].join("\n");
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), true, "task_complete last must classify as ended");
}

// 2. A new user message AFTER the last task_complete -> IN-FLIGHT (new turn began).
{
  const text = [
    L({ type: "event_msg", payload: { type: "task_complete", turn_id: "t1" } }),
    L({ type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: "next" }] } }),
  ].join("\n");
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), false, "a user message after task_complete must be in-flight");
  assert.equal(s.lastRole, "user");
}

// 3. A trailing function_call (tool in flight) -> IN-FLIGHT.
{
  const text = [
    L({ type: "event_msg", payload: { type: "task_complete", turn_id: "t0" } }),
    L({ type: "event_msg", payload: { type: "task_started", turn_id: "t1" } }),
    L({ type: "response_item", payload: { type: "function_call", name: "shell", arguments: "{}" } }),
  ].join("\n");
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), false, "a trailing function_call must be in-flight");
  assert.equal(s.pendingToolUse, true);
}

// 4. token_count bookkeeping after task_complete is skipped -> still ENDED.
{
  const text = [
    L({ type: "event_msg", payload: { type: "task_complete", turn_id: "t1" } }),
    L({ type: "event_msg", payload: { type: "token_count", info: {} } }),
  ].join("\n");
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), true, "token_count after task_complete must be skipped (still ended)");
}

// 5. task_started with nothing after it -> IN-FLIGHT.
{
  const text = L({ type: "event_msg", payload: { type: "task_started", turn_id: "t1" } });
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), false, "task_started must be in-flight");
  assert.equal(s.lastRole, "user");
}

// 6. Empty / no meaningful line -> UNKNOWN (lastRole null), so the detector won't flip.
{
  assert.equal(summarizeCodexRolloutTail("").lastRole, null, "empty -> unknown");
  assert.equal(summarizeCodexRolloutTail("not json\n{partial").lastRole, null, "garbage -> unknown");
  assert.equal(summarizeCodexRolloutTail(L({ type: "event_msg", payload: { type: "token_count" } })).lastRole, null,
    "only-bookkeeping tail -> unknown");
}

// 7. A truncated first line (byte-window cut) is skipped; the next valid line decides.
{
  const text = '{"type":"response_item","payload":{"type":"mess' + "\n" +
    L({ type: "event_msg", payload: { type: "task_complete", turn_id: "t1" } });
  const s = summarizeCodexRolloutTail(text);
  assert.equal(isEnded(s), true, "partial leading line is ignored; task_complete still ends");
}

console.log("ok - codex-rollout-tail: 7 groups passed");
