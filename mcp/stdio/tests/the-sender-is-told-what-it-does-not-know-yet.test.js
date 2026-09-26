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

test("both send acks carry the note: the direct send and the channel post", () => {
  // The helper above can be green while a call site never calls it, which is how the channel ack
  // shipped without the note in 0.7.0. The acks run only against a live service (IS_REMOTE), so
  // this reads each registration's source, from its name to the next registration.
  const src = readFileSync(new URL("../send-tools.mjs", import.meta.url), "utf8");
  for (const tool of ["comms_send", "comms_channel_send"]) {
    const start = src.indexOf(`    "${tool}",`);
    assert.ok(start > 0, `${tool}'s registration was not found; this reader is stale`);
    const next = src.indexOf("server.tool(", start);
    const body = src.slice(start, next < 0 ? undefined : next);
    assert.match(body, /awaitingReplyNote\(/, `${tool}'s ack must carry the note`);
  }
});
