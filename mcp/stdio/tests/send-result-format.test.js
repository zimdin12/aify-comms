// What comms_send tells an agent after a triggered send, for every answer shape both service versions give
// (0.9.8 stage 1). The formatter must claim only what the answer proves: no empty run list or queued console
// input is ever "started", a stored-unwoken recipient is named as such, and a replay is unknown.
import assert from "node:assert/strict";
import test from "node:test";

import { formatSendRefusal, formatTriggeredSendResult } from "../send-result-format.mjs";

const ctx = { from: "me", to: "peer", type: "request" };
const text = (res) => res.content[0].text;
const run = { targetAgentId: "peer", runId: "run-1", status: "queued" };

test("OLD SERVICE refusal: an error naming who was unavailable", () => {
  const res = formatSendRefusal({ ok: false, error: "Message was not sent because one or more recipients cannot start live work now.", notStarted: [{ targetAgentId: "peer", reason: 'agent status is "offline"', recipientStatus: "offline" }] });
  assert.equal(res.isError, true);
  assert.match(text(res), /Message was not sent/);
  assert.match(text(res), /Unavailable: peer: agent status is "offline" \(offline\)/);
});

test("healthy queued: the run is named and the ack is not a delivery claim", () => {
  const out = text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [run], notStarted: [] }, ctx));
  assert.match(out, /^Sent\. Dispatch: peer \(run-1\)/);
  assert.match(out, /CREATED, not what was delivered/);
  assert.doesNotMatch(out, /not woken/);
});

test("a request's ack carries the don't-predict-the-reply note, stored-unwoken included", () => {
  const note = /The reply arrives as a new message that wakes you/;
  assert.match(text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [run], notStarted: [] }, ctx)), note);
  assert.match(text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [], notStarted: [{ targetAgentId: "peer", reason: "offline" }] }, ctx)), note);
  assert.doesNotMatch(text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [run], notStarted: [] }, { ...ctx, type: "info" })), note, "info owes nothing");
});

test("console-only delivery is queued input, never proof the agent read it", () => {
  const out = text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [], notStarted: [], consoleDeliveries: [{ targetAgentId: "peer", terminalId: "t1" }] }, ctx));
  assert.match(out, /Typed into the console of peer \(queued input, not proof the agent read it\)/);
  assert.doesNotMatch(out, /started/);
});

test("NEW SERVICE stored-only: saved, not woken, with the service's own notice and no 'started'", () => {
  const notice = "Wake failed for peer (agent status is \"offline\"). The message is in peer's inbox; peer can read it when it is online.";
  const out = text(formatTriggeredSendResult({ ok: true, stored: true, messageId: "m1", recipients: ["peer"], dispatchRuns: [], notStarted: [{ targetAgentId: "peer", reason: 'agent status is "offline"' }], notice }, ctx));
  assert.match(out, /^Saved in the inbox, not woken: peer: agent status is "offline"\./);
  assert.ok(out.includes(notice), "the service's notice is passed on");
  assert.doesNotMatch(out, /Dispatch|started/, "an empty run list is not a start");
});

test("mixed recipients: the woken one is dispatched, the other is saved and not woken", () => {
  const out = text(formatTriggeredSendResult({ ok: true, messageId: "m1", recipients: ["peer", "away"], dispatchRuns: [run], notStarted: [{ targetAgentId: "away", reason: "stopped" }] }, ctx));
  assert.match(out, /Sent\. Dispatch: peer \(run-1\)/);
  assert.match(out, /Saved in the inbox, not woken: away: stopped\./);
});

test("an answer with no run, no console input and no unwoken recipient leaves the wake UNKNOWN", () => {
  const out = text(formatTriggeredSendResult({ ok: true, messageId: "m9", recipients: ["peer"], dispatchRuns: [], notStarted: [] }, ctx));
  assert.match(out, /^Stored \(m9\)\. Whether a wake happened is not known from this answer\./);
  assert.doesNotMatch(out, /Dispatch:|not woken|nothing was started/, "an empty list is not proof either way");
});

test("an accepted-looking answer with no message id claims neither storage nor a send", () => {
  const out = text(formatTriggeredSendResult({ ok: true, recipients: ["peer"], dispatchRuns: [], notStarted: [] }, ctx));
  assert.match(out, /^The service's answer was incomplete \(no message id\): whether it was stored or woken is not known\./);
  assert.doesNotMatch(out, /Stored \(|undefined/);
});

test("a replay is unknown, from both the new service and an old one without the new fields", () => {
  for (const r of [
    { ok: true, messageId: "m1", replayed: true, stored: true, wakeOutcome: "unknown", recipients: [], dispatchRuns: [], notStarted: [] },
    { ok: true, messageId: "m1", replayed: true, recipients: [], dispatchRuns: [], notStarted: [] },
  ]) {
    const out = text(formatTriggeredSendResult(r, ctx));
    assert.match(out, /^Already sent: this matched an earlier send of the same message \(m1\)/);
    assert.match(out, /not known from this answer, and nothing was woken again/);
    assert.doesNotMatch(out, /Stored \(|Dispatch|started\./, "a replay's empty lists say nothing about the first send");
  }
});
