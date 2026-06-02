#!/usr/bin/env node

import assert from "node:assert/strict";
import { test } from "node:test";

test("Claude channel dispatch content starts with a native aify-comms receipt marker", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "Hello",
    body: "Payload",
    priority: "normal",
    messageId: "msg-1",
  });

  assert.match(text, /^aify-comms message received\n/);
  assert.match(text, /\[NORMAL\] sender → claude-test: Hello/);
  assert.doesNotMatch(text, /^\+-+\+$/m, "Claude should keep its own channel shape, not Hermes box styling");
});

test("Claude channel require_reply dispatch instructs a same-turn reply (no deferred reply)", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "Please review",
    body: "Payload",
    priority: "normal",
    messageId: "msg-1",
    requireReply: true,
  });

  // The wake text MUST tell the agent to send its inReplyTo reply in THIS turn,
  // before ending — a managed session is not re-woken to finish a deferred reply
  // (root-caused 2026-06-02: split read+reply across two turns stranded the reply).
  assert.match(text, /this turn/i, "must direct a same-turn reply");
  assert.match(text, /not be re-woken|won't be re-woken|will not be re-woken/i, "must warn the session is not re-woken to finish a deferred reply");
  assert.match(text, /inReplyTo="msg-1"/, "must reference the inReplyTo handle");
});

test("Claude channel non-require_reply dispatch does NOT force a same-turn reply directive", async () => {
  const { dispatchContent } = await import("../claude-channel.js");
  const text = dispatchContent("claude-test", {
    from: "sender",
    subject: "FYI",
    body: "Payload",
    priority: "normal",
    messageId: "msg-2",
    requireReply: false,
  });
  assert.doesNotMatch(text, /not be re-woken|won't be re-woken|will not be re-woken/i,
    "delivery-only dispatch should not carry the same-turn-reply warning");
});
