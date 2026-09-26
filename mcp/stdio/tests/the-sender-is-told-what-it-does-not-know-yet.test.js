// A request's send ack says what the sender knows until the reply arrives (v0.7, H-A2), and only
// when a reply is owed. A message to yourself is your own next turn, so it gets no such note.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

test("requireReply=false on a request means no reply is tracked, so none is promised", () => {
  // v0.7.2 (external review, item 6): the service creates the run with require_reply=false
  // (reply_expectation.py `_dispatch_requires_reply`), and the ack still said the reply would arrive.
  for (const type of ["request", "review", "error"]) {
    assert.equal(awaitingReplyNote({ from: "me", to: "peer", type, requireReply: false }), "", type);
  }
});

test("the direct send's ack carries the note, and the channel post's does not", () => {
  // The helper above can be green while a call site never calls it. The acks run only against a live
  // service (IS_REMOTE), so this reads each registration's source, from its name to the next one.
  // A channel run is created with require_reply=False (service/routers/channel_send.py), so no reply is
  // tracked and the channel ack promised one that nothing owes (v0.7.2, external review item 6).
  const src = readFileSync(new URL("../send-tools.mjs", import.meta.url), "utf8");
  const registration = (tool) => {
    const start = src.indexOf(`    "${tool}",`);
    assert.ok(start > 0, `${tool}'s registration was not found; this reader is stale`);
    const next = src.indexOf("server.tool(", start);
    return src.slice(start, next < 0 ? undefined : next);
  };
  assert.match(registration("comms_send"), /awaitingReplyNote\(/, "comms_send's ack must carry the note");
  assert.doesNotMatch(registration("comms_channel_send"), /awaitingReplyNote\(/, "the channel ack promises a reply no channel run tracks");
});
