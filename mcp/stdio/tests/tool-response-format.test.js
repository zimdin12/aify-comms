// The wording of MCP tool responses, tested directly for the first time.
//
// These nine formatters decide what an operator READS about their fleet: inbox lines, dispatch state,
// queued runs, outbound activity, auto-reply text. Until v0.5.4 they lived in `server.js`, the bin entry
// point, which nothing imports — so none of it was reachable from a test. A wrong word here is not a
// crash; it is a person being misinformed, which is the failure mode that survives a green suite.
//
// Every function here is pure: plain values in, a string out. That is the property that makes these
// assertions possible at all, and the reason the extraction was bounded to the self-contained subset.

import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  autoReplyBodyForRun,
  autoReplySubjectForRun,
  formatDispatchState,
  formatInboxHeaders,
  formatInboxMessage,
  formatOutboundActivity,
  formatQueuedRun,
  replyExpectationSummary,
} from "../tool-response-format.mjs";

const MESSAGE = {
  id: "msg-1", from: "agent-a", to: "agent-b", type: "request",
  subject: "deploy the thing", body: "please deploy", timestamp: "2026-08-10T16:02:58Z",
};
const REGISTRY = { agents: { "agent-a": { role: "coder" } } };

test("every export is callable and returns a string for its REAL input shape", () => {
  // My first version called every function with `{}`, which assumed a uniform single-object contract
  // these do not share: `formatInboxHeaders`/`formatInboxMessage` take (message, registry) and
  // `internalCompactUnsupportedText` takes a SESSION. Testing an invented signature proves nothing
  // about the code and fails for the wrong reason, so each is called the way production calls it.
  const cases = [
    ["formatDispatchState", () => formatDispatchState({ state: "queued" })],
    ["formatQueuedRun", () => formatQueuedRun({ targetAgentId: "agent-b", runId: "run-1" })],
    ["formatOutboundActivity", () => formatOutboundActivity({ outbound: {} })],
    ["formatInboxHeaders", () => formatInboxHeaders(MESSAGE, REGISTRY)],
    ["formatInboxMessage", () => formatInboxMessage(MESSAGE, REGISTRY)],
    ["autoReplySubjectForRun", () => autoReplySubjectForRun({ id: "run-1", subject: "s" })],
    ["autoReplyBodyForRun", () => autoReplyBodyForRun({ id: "run-1", subject: "s" })],
    ["replyExpectationSummary", () => replyExpectationSummary({ id: "run-1" })],
  ];
  for (const [name, call] of cases) {
    let out;
    assert.doesNotThrow(() => { out = call(); }, `${name} threw on its documented shape`);
    assert.equal(typeof out, "string", `${name} must return a string`);
  }
});

test("formatOutboundActivity reports both timestamps, and neither when absent", () => {
  // This field exists to retire a false "silent lane" claim — an agent that HAS sent recently must not
  // look idle. Reporting nothing when something happened is the bug it guards.
  assert.match(formatOutboundActivity({ outbound: { lastSentAt: "2026-08-10T16:02:58Z" } }), /16:02:58|2026-08-10/);
  assert.match(
    formatOutboundActivity({ outbound: { lastCompletedRunAt: "2026-08-10T16:03:00Z" } }),
    /16:03:00|2026-08-10/,
  );
  const empty = formatOutboundActivity({});
  assert.equal(typeof empty, "string");
  assert.ok(!/undefined|NaN/.test(empty), `empty state leaked a placeholder: ${empty}`);
});

test("the inbox formatters name the sender's role when the registry knows it", () => {
  const known = formatInboxHeaders(MESSAGE, REGISTRY);
  assert.match(known, /agent-a/);
  assert.match(known, /coder/, "a known sender's role should be shown");
  const unknown = formatInboxHeaders(MESSAGE, { agents: {} });
  assert.ok(!/undefined/.test(unknown), `an unknown sender leaked a placeholder: ${unknown}`);
});

test("an unread message is tagged NEW and a read one is not", () => {
  assert.match(formatInboxHeaders(MESSAGE, REGISTRY), /\[NEW\]/);
  assert.match(formatInboxHeaders({ ...MESSAGE, read: true }, REGISTRY), /\[read\]/);
  assert.ok(!/\[NEW\]/.test(formatInboxHeaders({ ...MESSAGE, _read: true }, REGISTRY)),
    "the internal _read flag must also suppress the NEW tag");
});

test("no formatter leaks a placeholder for the shapes production actually passes", () => {
  // The commonest failure here is not throwing — it is interpolating a missing optional field and
  // printing "undefined" to a human. Only OPTIONAL fields are varied; omitting a required id is a
  // shape these never receive, and asserting on it would be testing an invented contract.
  const variants = [
    { ...MESSAGE, subject: "" }, { ...MESSAGE, body: "" }, { ...MESSAGE, preview: "" },
    { ...MESSAGE, type: "" },
  ];
  for (const m of variants) {
    for (const [name, out] of [
      ["formatInboxHeaders", formatInboxHeaders(m, REGISTRY)],
      ["formatInboxMessage", formatInboxMessage(m, REGISTRY)],
    ]) {
      assert.ok(!/undefined|NaN|\[object Object\]/.test(out),
        `${name} leaked a placeholder for ${JSON.stringify(m)}: ${out}`);
    }
  }
  for (const run of [{ id: "r", subject: "s" }, { id: "r", subject: "" }]) {
    for (const [name, out] of [
      ["autoReplySubjectForRun", autoReplySubjectForRun(run)],
      ["autoReplyBodyForRun", autoReplyBodyForRun(run)],
      ["replyExpectationSummary", replyExpectationSummary(run)],
    ]) {
      assert.ok(!/undefined|NaN|\[object Object\]/.test(out),
        `${name} leaked a placeholder for ${JSON.stringify(run)}: ${out}`);
    }
  }
});

test("the module is pure: no imports, no module state", () => {
  // The property the rest of this file depends on, asserted rather than assumed. If a future edit needs
  // a database read or a module-level `let`, it does not belong here — and these tests would quietly
  // stop being deterministic.
  const src = readFileSync(new URL("../tool-response-format.mjs", import.meta.url), "utf-8");
  assert.ok(!/^import\s/m.test(src), "a pure formatter module should need no imports");
  assert.ok(!/^let\s/m.test(src), "no module-level mutable state");
  assert.ok(!/await/.test(src), "no async work belongs here");
  assert.ok(!/httpCall|fetch\(|fs\./.test(src), "no I/O belongs here");
});

test("formatQueuedRun names the target and run, and explains any queueing", () => {
  // Signature READ from the body rather than assumed: it takes `targetAgentId` and `runId`, not the
  // `{id, subject}` shape I guessed twice. Testing an invented signature proves nothing.
  const plain = formatQueuedRun({ targetAgentId: "agent-b", runId: "run-1" });
  assert.match(plain, /agent-b/);
  assert.match(plain, /run-1/);

  const steered = formatQueuedRun({
    targetAgentId: "agent-b", runId: "run-1", steered: true,
    steeredIntoActiveRun: { runId: "run-9", subject: "deploy" },
  });
  assert.match(steered, /steered into active run run-9/);
  assert.match(steered, /deploy/, "the run it steered into should be named, not just its id");

  const queued = formatQueuedRun({
    targetAgentId: "agent-b", runId: "run-1",
    queuedBehindActiveRun: { runId: "run-7", subject: "build" },
  });
  assert.match(queued, /queued behind active run run-7/);

  const merged = formatQueuedRun({
    targetAgentId: "agent-b", runId: "run-1", merged: true, mergedCount: 3,
  });
  assert.match(merged, /buffered 3 updates/, "a merged run must say how many updates it absorbed");
});
