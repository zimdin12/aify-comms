// The prompts a dispatched agent actually receives.
//
// FIRST PAYMENT ON THE UNTESTED BACKLOG recorded in `every-module-is-imported-by-a-test.test.js`. This
// module builds the system and user prompts for every managed run, and nothing exercised it — 109 lines
// deciding, among other things, WHETHER AN AGENT REPLIES AT ALL. Chosen first because it is pure: no
// spawn, no network, no filesystem, so the tests are real calls rather than a harness.
//
// THE REPLY CONTRACT IS THE SUBJECT. Three states, and the difference between them is operator-visible:
//   * dashboard sender  — a human is waiting; always reply, and reply to "dashboard" not the raw id
//   * requireReply false — read context; replying anyway is the over-communication the operator has
//                          complained about, so the prompt must actively discourage an ack
//   * default           — a required handoff; ending silently strands the run
// A regression here does not crash anything. It changes how a fleet of agents behaves, quietly.

import assert from "node:assert/strict";
import test from "node:test";

import { buildSystemPrompt, buildUserPrompt } from "../runtimes-prompts.js";

const AGENT = { role: "coder" };
const run = (extra = {}) => ({ from: "manager-bot", type: "request", subject: "please review", body: "the diff", ...extra });

test("the reply target is the SENDER for an agent, and 'dashboard' for the operator", () => {
  // `to="dashboard"` is not cosmetic: the dashboard is not a registered agent id, and replying to the raw
  // sender string would not thread into the operator's chat.
  const toAgent = buildSystemPrompt("coder-1", AGENT, run());
  assert.match(toAgent, /to="manager-bot"/);

  const toDash = buildSystemPrompt("coder-1", AGENT, run({ from: "dashboard" }));
  assert.match(toDash, /to="dashboard"/);
  assert.match(toDash, /human\/operator/, "the agent must be told a person is waiting");
});

test("a messageId becomes inReplyTo, which is what closes the run", () => {
  // Without it the reply is a new message and the originating run never closes — the stranded-run failure
  // this project has hit before.
  const withId = buildSystemPrompt("coder-1", AGENT, run({ messageId: "msg-42" }));
  assert.match(withId, /inReplyTo="msg-42"/);
  assert.match(withId, /MessageId: msg-42/);

  const withoutId = buildSystemPrompt("coder-1", AGENT, run());
  assert.ok(!withoutId.includes("inReplyTo="), "no id means no inReplyTo rather than an empty one");
  assert.ok(!withoutId.includes("MessageId:"), "and no dangling MessageId line");
});

test("inReplyTo falls back to the run's own inReplyTo when messageId is absent", () => {
  const p = buildSystemPrompt("coder-1", AGENT, run({ inReplyTo: "parent-7" }));
  assert.match(p, /inReplyTo="parent-7"/);
});

test("requireReply=false tells the agent NOT to send a courtesy acknowledgement", () => {
  // The over-communication case. The prompt has to do more than omit the requirement — it must actively
  // say an ack-only reply is unwanted, or agents reply to everything.
  const optional = buildUserPrompt(run({ requireReply: false }));
  assert.match(optional, /Do not send an acknowledgement-only reply/i);
  assert.match(optional, /Do not send a courtesy ack/i);
  assert.ok(!/Do not end silently/.test(optional), "the required-handoff wording must not also appear");
});

test("the DEFAULT is a required handoff — silence strands the run", () => {
  const required = buildUserPrompt(run());
  assert.match(required, /Required handoff/);
  assert.match(required, /Do not end silently/);
  assert.ok(!/courtesy ack/i.test(required), "the optional wording must not leak into the required case");
});

test("requireReply=false is IGNORED for the dashboard — a human is still waiting", () => {
  // `!isDashboardSender && requireReply === false`. My first version only checked the reply RULE, and a
  // mutant that dropped the dashboard guard survived it: `replyRule` tests isDashboardSender first, so the
  // rule line is unchanged. The guard's effect shows up in the OTHER replyOptional-driven lines, which have
  // no dashboard branch of their own — so those are what must be asserted.
  const p = buildUserPrompt(run({ from: "dashboard", requireReply: false }));
  assert.match(p, /Reply to the dashboard user/);
  assert.ok(!/keep it as read context/i.test(p), "the operator's message is never read-only context");
  assert.match(p, /Reply delivery: send your answer as a comms_send tool call \(rule below\)/,
    "the REQUIRED delivery wording, not the conditional one");
  assert.match(p, /Do not end silently/, "and the required-handoff closing rule");
  assert.ok(!/courtesy ack/i.test(p), "never the ack-suppression wording for a waiting human");

  const sys = buildSystemPrompt("coder-1", AGENT, run({ from: "dashboard", requireReply: false }));
  assert.match(sys, /human\/operator/);
  assert.ok(!/does not owe an acknowledgement/i.test(sys),
    "the system prompt must not tell the agent a dashboard message is optional");
});

test("only an explicit false makes the reply optional — in BOTH prompts", () => {
  // `requireReply === false`, not falsy. An absent field is the common case and must stay required.
  //
  // Both builders carry their own copy of this expression, and my first version only exercised the user
  // prompt — a mutant loosening the SYSTEM prompt to `!run?.requireReply` survived. Two copies of one rule
  // need two assertions, or one of them is unguarded.
  for (const value of [undefined, null, 0, ""]) {
    const label = JSON.stringify(value);
    assert.match(buildUserPrompt(run({ requireReply: value })), /Required handoff/,
      `user prompt: requireReply=${label} must still require a reply`);
    const sys = buildSystemPrompt("coder-1", AGENT, run({ requireReply: value }));
    assert.match(sys, /Before you finish, send the reply/,
      `system prompt: requireReply=${label} must still require a reply`);
    assert.ok(!/does not owe an acknowledgement/i.test(sys),
      `system prompt: requireReply=${label} must not read as optional`);
  }
});

test("a channel subject adds channel discipline; a direct one does not", () => {
  // `/^#[-A-Za-z0-9_.]+:/`. Broad acks in a channel are what create acknowledgement loops across a team.
  const channel = buildUserPrompt(run({ subject: "#dev-team: ship it" }));
  assert.match(channel, /Channel discipline/);

  const direct = buildUserPrompt(run({ subject: "ship it" }));
  assert.ok(!/Channel discipline/.test(direct));

  // A '#' that is not a channel prefix must not trigger it.
  const hashed = buildUserPrompt(run({ subject: "#4 is broken" }));
  assert.ok(!/Channel discipline/.test(hashed), "'#4 is broken' is a subject, not a channel");
});

test("standing instructions appear only when the agent has them", () => {
  const withInstr = buildSystemPrompt("coder-1", { role: "coder", instructions: "always run the suite" }, run());
  assert.match(withInstr, /Standing instructions: always run the suite/);

  const without = buildSystemPrompt("coder-1", AGENT, run());
  assert.ok(!without.includes("Standing instructions"), "no empty label when there are none");
});

test("empty sections are dropped rather than left as blank lines", () => {
  // `.filter(Boolean)`. Without it the prompt accumulates blank lines for every inapplicable rule, which
  // is wasted context on every single run.
  const p = buildSystemPrompt("coder-1", AGENT, run());
  assert.ok(!/\n\n\n/.test(p), "no runs of blank lines");
  assert.match(p, /^\[AIFY MESSAGE\]/);
  assert.match(p, /\[\/AIFY MESSAGE\]$/);
});

test("conversation context is included oldest-first and capped at eight messages", () => {
  // Unbounded history would grow the prompt without limit on a long-running pair of agents.
  const messages = Array.from({ length: 12 }, (_, i) => ({ from: "manager-bot", type: "info", body: `m${i}` }));
  const p = buildUserPrompt(run({ conversationContext: messages }));
  assert.match(p, /\[RECENT DIRECT CONVERSATION\]/);
  assert.ok(!p.includes("m3"), "the oldest beyond the cap are dropped");
  assert.ok(p.includes("m11"), "the newest is kept");
  assert.ok(p.indexOf("m4") < p.indexOf("m11"), "oldest first");
});

test("a long context body is truncated with an ellipsis, not sent whole", () => {
  const p = buildUserPrompt(run({ conversationContext: [{ from: "m", body: "x".repeat(2000) }] }));
  assert.match(p, /x{700}\.\.\./, "capped at 700 characters");
  assert.ok(!p.includes("x".repeat(800)), "the full body must not survive");
});

test("no conversation context yields no section at all", () => {
  for (const value of [undefined, [], null, "not an array"]) {
    const p = buildUserPrompt(run({ conversationContext: value }));
    assert.ok(!p.includes("[RECENT DIRECT CONVERSATION]"),
      `${JSON.stringify(value)} must produce no context block`);
  }
});

test("a context entry missing its fields still renders a readable line", () => {
  // These come off the wire. A missing `from` rendering as "undefined" would be shown to the agent.
  const p = buildUserPrompt(run({ conversationContext: [{}] }));
  assert.match(p, /- unknown \(info\)/, "absent sender and type get readable defaults");
  assert.ok(!p.includes("undefined"), "no raw undefined may reach the prompt");
});

test("the body is carried verbatim, including markdown and newlines", () => {
  const body = "line one\n\n```js\nconst a = 1;\n```";
  const p = buildUserPrompt(run({ body }));
  assert.ok(p.includes(body), "the message body must not be reformatted");
});
