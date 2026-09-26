// A claude turn the operator interrupted with Esc reads as ENDED, not in flight (v0.7.4).
//
// Esc fires no hook. Claude records the interrupt in the transcript as a user message whose whole text
// is "[Request interrupted by user]" (or "... for tool use"), and nothing will answer it. The detector
// read a trailing user message as "the assistant is about to answer", kept re-posting turn-start, and the
// agent read `working` while it sat idle, for up to 30 minutes. Counted on this host's transcripts,
// 2026-09-26: 28 of the first form and 3 of the second, all shaped like the specimens below.
import assert from "node:assert/strict";
import test from "node:test";
import { isInterruptMarker, summarizeTranscriptTail } from "../adapters/claude.js";
import { classify } from "../turn-end-detector.js";

// Verbatim shapes from real transcripts (claude 2.1.229 and 2.1.159), trimmed of unrelated fields.
const ASSISTANT_MID_TOOL = JSON.stringify({ type: "assistant", message: { role: "assistant", stop_reason: "tool_use", content: [{ type: "tool_use", name: "Bash", id: "t1", input: {} }] } });
const INTERRUPTED = JSON.stringify({ type: "user", message: { role: "user", content: [{ type: "text", text: "[Request interrupted by user]" }] }, interruptedMessageId: "msg_011CeAVWEWc4BQUnBuZP8Q9f" });
const INTERRUPTED_TOOL = JSON.stringify({ type: "user", message: { role: "user", content: [{ type: "text", text: "[Request interrupted by user for tool use]" }] } });
const LAST_PROMPT = JSON.stringify({ type: "last-prompt", lastPrompt: "Now you should have some browser mcp tools." });

test("a tail ending in the interrupt marker is ENDED, even after a pending tool call", () => {
  for (const marker of [INTERRUPTED, INTERRUPTED_TOOL]) {
    const summary = summarizeTranscriptTail([ASSISTANT_MID_TOOL, marker, LAST_PROMPT].join("\n"));
    assert.equal(classify(summary), "ended", marker);
  }
});

test("CONTROL: the same pending tool call without the marker is still in flight", () => {
  assert.equal(classify(summarizeTranscriptTail(ASSISTANT_MID_TOOL)), "in-flight");
});

test("CONTROL: a real prompt that merely quotes the marker is a prompt, so in flight", () => {
  const quoted = JSON.stringify({ type: "user", message: { role: "user", content: [{ type: "text", text: "why did you stop? [Request interrupted by user]" }] } });
  assert.equal(isInterruptMarker(JSON.parse(quoted).message), false);
  assert.equal(classify(summarizeTranscriptTail(quoted)), "in-flight");
});

test("the marker is recognised in both content shapes and nothing else", () => {
  assert.equal(isInterruptMarker({ role: "user", content: "[Request interrupted by user]" }), true);
  assert.equal(isInterruptMarker(JSON.parse(INTERRUPTED_TOOL).message), true);
  assert.equal(isInterruptMarker({ role: "user", content: [{ type: "tool_result", content: "[Request interrupted by user]" }] }), false);
  assert.equal(isInterruptMarker(null), false);
});
