// A request's send ack says what the sender knows until the reply arrives (v0.7, H-A2), and only
// when a reply is owed. A message to yourself is your own next turn, so it gets no such note.
import assert from "node:assert/strict";
import test from "node:test";
import { awaitingReplyNote } from "../tool-response-format.mjs";

test("a request to another agent tells the sender not to report, predict or redo the work", () => {
  for (const type of ["request", "review", "error"]) {
    assert.match(awaitingReplyNote({ from: "me", to: "peer", type }), /do not report, predict or redo that work/, type);
  }
  assert.match(awaitingReplyNote({ from: "me", to: "peer", type: "info", requireReply: true }), /wakes you/);
});

test("no note when nothing is owed, or when the message is to yourself", () => {
  assert.equal(awaitingReplyNote({ from: "me", to: "peer", type: "info" }), "");
  assert.equal(awaitingReplyNote({ from: "me", to: "peer", type: "response" }), "");
  assert.equal(awaitingReplyNote({ from: "me", to: "me", type: "request" }), "");
});
