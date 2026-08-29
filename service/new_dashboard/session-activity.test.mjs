// Real tests for the per-session activity feed.
//
// The merge rules here decide whether an operator looking at one agent sees what it has actually been
// doing. Each was invisible to the source-regex tests app.js is limited to: a run belongs to the feed if
// the agent is its TARGET **or** its sender, messages match on any of four sender/recipient spellings, and
// the two streams interleave by time rather than concatenating.
//
// SEALING. `state` is a shared singleton, so the fields these read are rebuilt per test; `document` does
// not exist in Node and is installed only while rendering.

import assert from "node:assert/strict";
import test from "node:test";

import { state } from "./state.mjs";
import { messagesForSession, renderSessionActivity, runFrom } from "./session-activity.mjs";

const session = (agentId) => ({ id: "s1", agentId });

function render({ runs = [], messages = [] } = {}, agentId = "coder") {
  state.runs = runs;
  state.messages = messages;
  const host = { innerHTML: "" };
  const had = "document" in globalThis;
  globalThis.document = { getElementById: (id) => (id === "session-activity" ? host : null) };
  try {
    renderSessionActivity(session(agentId));
    return host.innerHTML;
  } finally {
    if (!had) delete globalThis.document;
  }
}

test("runFrom reads all three sender spellings the server has shipped", () => {
  assert.equal(runFrom({ from: "a" }), "a");
  assert.equal(runFrom({ fromAgent: "b" }), "b");
  assert.equal(runFrom({ from_agent: "c" }), "c");
  assert.equal(runFrom({}), "", "an absent sender is an empty string, never undefined — it is compared to an id");
});

test("a run belongs to the feed whether the agent SENT it or is its target", () => {
  // Dropping either half hides work the operator is looking straight at. The OR is the whole rule.
  const html = render({
    runs: [
      { id: "r-target", subject: "to coder", targetAgentId: "coder", updatedAt: "2026-08-14T10:00:00Z" },
      { id: "r-sent", subject: "from coder", from: "coder", updatedAt: "2026-08-14T11:00:00Z" },
      { id: "r-other", subject: "unrelated", targetAgentId: "tester", from: "tester" },
    ],
  });
  assert.ok(html.includes("r-target"), "a run targeting the agent must appear");
  assert.ok(html.includes("r-sent"), "a run the agent sent must appear too");
  assert.ok(!html.includes("r-other"), "another agent's run must not");
});

test("messagesForSession matches on sender, recipient, and both target spellings", () => {
  state.messages = [
    { id: "m1", from: "coder" },
    { id: "m2", to: "coder" },
    { id: "m3", targetAgentId: "coder" },
    { id: "m4", target_agent_id: "coder" },
    { id: "m5", from: "tester", to: "manager" },
  ];
  assert.deepEqual(messagesForSession(session("coder")).map((m) => m.id), ["m1", "m2", "m3", "m4"]);
});

test("messagesForSession returns nothing for a session with no agent", () => {
  state.messages = [{ id: "m1", from: "coder" }];
  assert.deepEqual(messagesForSession({ id: "s1" }), [],
    "an unbound session must not inherit another agent's mail");
});

test("runs and messages INTERLEAVE by time, newest first", () => {
  const html = render({
    runs: [{ id: "r1", subject: "older run", targetAgentId: "coder", updatedAt: "2026-08-14T10:00:00Z" }],
    messages: [{ id: "m1", from: "coder", subject: "newer message", timestamp: "2026-08-14T12:00:00Z" }],
  });
  assert.ok(html.indexOf("newer message") < html.indexOf("older run"),
    "the two streams are merged by timestamp, not concatenated by kind");
});

test("an unparseable timestamp sorts as 0 rather than poisoning the order", () => {
  // `Number.isFinite` guard: a single NaN in a comparator leaves the whole ordering undefined, so one
  // malformed row would scramble the feed rather than sink to the bottom of it.
  const html = render({
    runs: [
      { id: "r-bad", subject: "no timestamp", targetAgentId: "coder", updatedAt: "not a date" },
      { id: "r-good", subject: "dated", targetAgentId: "coder", updatedAt: "2026-08-14T10:00:00Z" },
    ],
  });
  assert.ok(html.indexOf("dated") < html.indexOf("no timestamp"), "the dated row sorts above the undated one");
});

test("the feed is capped at 60 rows", () => {
  const runs = Array.from({ length: 80 }, (_, i) =>
    ({ id: `r${i}`, subject: `run ${i}`, targetAgentId: "coder",
       updatedAt: `2026-08-14T10:${String(i % 60).padStart(2, "0")}:00Z` }));
  const html = render({ runs });
  assert.equal((html.match(/class="activity-row"/g) || []).length, 60);
});

test("an unread message is marked differently from a read one", () => {
  const unread = render({ messages: [{ id: "m1", from: "coder", body: "hi", read: false }] });
  const read = render({ messages: [{ id: "m2", from: "coder", body: "hi", read: true }] });
  assert.notEqual(unread, read, "read state must be visible — it is why the operator opens this panel");
  assert.ok(unread.includes("unread"));
});

test("an empty feed explains itself instead of rendering blank", () => {
  const html = render({});
  assert.ok(html.includes("No activity yet"));
  assert.ok(!html.includes("activity-row"));
});

test("a run falls back to its id when it has no subject", () => {
  const html = render({ runs: [{ id: "r-nameless", targetAgentId: "coder" }] });
  assert.ok(html.includes("r-nameless"), "a subjectless run must still be identifiable");
});
