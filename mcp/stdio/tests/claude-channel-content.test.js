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
