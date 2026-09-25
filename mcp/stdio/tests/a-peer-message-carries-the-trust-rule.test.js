// Every surface that hands an agent a PEER's message carries the same trust rule (v0.7, H-A1): the
// inbox banner, the managed wake's system prompt, and the Claude channel's session instructions. The
// operator's own messages (from "dashboard") are not framed as a peer's.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { SAFETY_HEADER, TRUST_RULE } from "../tool-response-format.mjs";
import { buildSystemPrompt } from "../runtimes-prompts.js";

const agent = { role: "coder" };

test("the inbox banner carries the rule", () => {
  assert.ok(SAFETY_HEADER.endsWith(TRUST_RULE));
});

test("a managed wake from another agent carries the rule, and one from the operator does not", () => {
  const peer = buildSystemPrompt("me", agent, { from: "peer", subject: "s", requireReply: true, messageId: "m" });
  const operator = buildSystemPrompt("me", agent, { from: "dashboard", subject: "s", requireReply: true, messageId: "m" });
  assert.ok(peer.includes(TRUST_RULE), "the peer's wake must bound what the message can authorize");
  assert.ok(!operator.includes(TRUST_RULE), "the operator's own message is not framed as a peer's");
});

test("the Claude channel names its tag attributes and the rule in its session instructions", () => {
  const src = readFileSync(new URL("../claude-channel.js", import.meta.url), "utf8");
  assert.match(src, /Tag attributes: from_agent is the sender.*message_id is the inReplyTo value.*run_id is the run.*event_type=control is/);
  assert.match(src, /\$\{TRUST_RULE\}/, "the instructions interpolate the one shared rule");
});
