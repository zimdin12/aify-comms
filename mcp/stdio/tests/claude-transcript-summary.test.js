#!/usr/bin/env node
// Tests for summarizeTranscriptTail, the structural summary of a claude transcript tail that the Stop gate
// (claude-stop-gate.js) classifies to tell a finished turn from a premature mid-turn Stop:
//   { lastRole, lastStopReason, pendingToolUse, pendingToolNames }
// The gate reads the last 64 KiB of the transcript itself, so the text below can start mid-line.
//
// The fixtures mirror the REAL claude JSONL schema sampled from a live session (2026-06-02): message lines carry
// message.role / message.stop_reason / message.content[].type, and NON-message bookkeeping lines (type
// "last-prompt", "mode", "permission-mode", "attachment", "summary") get appended AFTER the last assistant message
// and MUST be skipped when finding the last real message.
import assert from "node:assert/strict";
import { summarizeTranscriptTail } from "../adapters/claude.js";

const tail = (lines) => lines.map((o) => JSON.stringify(o)).join("\n") + "\n";

function asstMsg(stopReason, contentTypes) {
  return {
    type: "assistant",
    isSidechain: false,
    message: { role: "assistant", stop_reason: stopReason, content: contentTypes.map((t) => ({ type: t })) },
  };
}
function userMsg(contentTypes) {
  return { type: "user", message: { role: "user", content: contentTypes.map((t) => ({ type: t })) } };
}
const lastPrompt = { type: "last-prompt", lastPrompt: "redacted" };
const modeLine = { type: "mode", mode: "default" };
const permLine = { type: "permission-mode", permissionMode: "default" };

// (1) ENDED: last assistant message stop_reason end_turn, trailed by bookkeeping
//     lines (last-prompt/mode/permission-mode) which must be skipped.
{
  const s = summarizeTranscriptTail(tail([userMsg(["text"]), asstMsg("end_turn", ["text"]), lastPrompt, modeLine, permLine]));
  assert.equal(s.lastRole, "assistant", "(1) last message role is assistant");
  assert.equal(s.lastStopReason, "end_turn", "(1) stop_reason end_turn");
  assert.equal(s.pendingToolUse, false, "(1) no pending tool_use");
}

// (2) IN-FLIGHT: last assistant message stop_reason tool_use with a tool_use
//     content block (a long build / a Task sub-agent dispatch).
{
  const s = summarizeTranscriptTail(tail([asstMsg("tool_use", ["thinking"]), asstMsg("tool_use", ["text"]), asstMsg("tool_use", ["tool_use"])]));
  assert.equal(s.lastRole, "assistant", "(2) last role assistant");
  assert.equal(s.lastStopReason, "tool_use", "(2) stop_reason tool_use");
  assert.equal(s.pendingToolUse, true, "(2) pending tool_use true");
}

// (3) IN-FLIGHT: trailing user/tool_result (tool returned, model owes next step).
{
  const s = summarizeTranscriptTail(tail([asstMsg("tool_use", ["tool_use"]), userMsg(["tool_result"])]));
  assert.equal(s.lastRole, "user", "(3) last role user (tool_result)");
  assert.equal(s.pendingToolUse, false, "(3) user line has no pending tool_use of its own");
}

// (4) a tail cut mid-line (the gate reads a fixed byte window): the partial first line is skipped.
{
  const text = tail([asstMsg("tool_use", ["tool_use"]), userMsg(["text"]), asstMsg("end_turn", ["text"]), lastPrompt]);
  const s = summarizeTranscriptTail(text.slice(17));
  assert.equal(s.lastStopReason, "end_turn", "(4) a cut first line does not poison the last real message");
  assert.equal(s.pendingToolUse, false, "(4) no pending tool_use");
}

// (5) only bookkeeping lines (no message line in the window) -> lastRole null, which classifies as unknown.
{
  const s = summarizeTranscriptTail(tail([lastPrompt, modeLine, permLine]));
  assert.equal(s.lastRole, null, "(5) no message line -> lastRole null");
}

// (6) pending tool NAMES: a tail ending in an assistant tool_use block exposes the
//     tool name(s) so the gate can tell an interactive-YIELD tool (AskUserQuestion /
//     ExitPlanMode — blocks awaiting a human) from real work. Mirrors the real shape
//     observed live: thinking + text blocks, then the tool_use block written LAST.
{
  const askMsg = {
    type: "assistant",
    message: {
      role: "assistant",
      stop_reason: "tool_use",
      content: [{ type: "text" }, { type: "tool_use", name: "AskUserQuestion" }],
    },
  };
  const s = summarizeTranscriptTail(tail([userMsg(["text"]), askMsg, lastPrompt]));
  assert.equal(s.lastRole, "assistant", "(6) last role assistant");
  assert.equal(s.pendingToolUse, true, "(6) pending tool_use true");
  assert.deepEqual(s.pendingToolNames, ["AskUserQuestion"], "(6) pending tool name captured for yield detection");
}

console.log("claude-transcript-summary.test.js: all assertions passed");
