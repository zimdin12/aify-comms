// A harness-injected user message is NOT a pending turn.
//
// Live incident (2026-07-14): the operator ran `/rename General Manager`. Claude appended a
// user-role entry whose ENTIRE content was
//     <system-reminder>The user named this session "General Manager"...</system-reminder>
// and the agent was stuck `working` from that second on — permanently.
//
// Why permanently: the tail summarizer saw `lastRole: "user"` and the classifier read that as
// "the human spoke, the assistant is about to answer" = IN-FLIGHT. So KEEP-FRESH re-posted
// /turn-start every 45s, and no assistant reply was ever coming to end it. KEEP-CLEARED could
// not help either — it only fires when the transcript proves ENDED, and this "proved" in-flight.
// A slash command is client-side; there is no turn.
//
// The summarizer already skips bookkeeping lines (NON_MESSAGE_TYPES). This is the same class of
// thing — it just arrives wearing a user role, so it slipped through.

import assert from "node:assert/strict";
import test from "node:test";

import { isBookkeepingUserMessage, summarizeTranscriptTail } from "../adapters/claude.js";
import { classify } from "../turn-end-detector.js";

const line = (o) => JSON.stringify(o);
const assistantDone = line({
  type: "assistant",
  message: { role: "assistant", stop_reason: "end_turn", content: [{ type: "text", text: "done" }] },
});
const userSays = (text) => line({ type: "user", message: { role: "user", content: text } });

// Verbatim from the live transcript that stuck general-manager.
const RENAME_INJECTION =
  '<system-reminder>\nThe user named this session "General Manager". This may indicate the session\'s focus or intent.\n</system-reminder>';

test("REGRESSION: /rename must not strand the agent at `working` forever", () => {
  const tail = summarizeTranscriptTail([assistantDone, userSays(RENAME_INJECTION)].join("\n"));
  assert.equal(tail.lastRole, "assistant", "the injection must be skipped, not treated as a prompt");
  assert.equal(classify(tail), "ended", "an idle session must classify as ENDED so it can be cleared");
});

test("a client-side slash command's wrapper is bookkeeping too", () => {
  const cmd = "<command-name>/help</command-name>\n<local-command-stdout>usage: ...</local-command-stdout>";
  const tail = summarizeTranscriptTail([assistantDone, userSays(cmd)].join("\n"));
  assert.equal(classify(tail), "ended");
});

test("a REAL prompt is still in-flight — even when it carries a system-reminder", () => {
  // This is the case that must not regress: almost every real user message has reminders
  // appended to it. Only a message with NO prompt text of its own is bookkeeping.
  const real = `${RENAME_INJECTION}\nplease fix the failing test`;
  const tail = summarizeTranscriptTail([assistantDone, userSays(real)].join("\n"));
  assert.equal(tail.lastRole, "user", "a genuine prompt must still register as a pending turn");
  assert.equal(classify(tail), "in-flight");
});

test("a tool_result user message is real turn content, never bookkeeping", () => {
  const toolResult = line({
    type: "user",
    message: { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "ok" }] },
  });
  const tail = summarizeTranscriptTail([assistantDone, toolResult].join("\n"));
  assert.equal(tail.lastRole, "user", "mid-turn tool traffic must keep the agent working");
  assert.equal(classify(tail), "in-flight");
});

test("isBookkeepingUserMessage: block-array content is handled too", () => {
  assert.equal(isBookkeepingUserMessage({ content: [{ type: "text", text: RENAME_INJECTION }] }), true);
  assert.equal(isBookkeepingUserMessage({ content: [{ type: "text", text: "do the thing" }] }), false);
  assert.equal(isBookkeepingUserMessage({ content: "" }), true);
  assert.equal(isBookkeepingUserMessage(null), false);
});
