// Real tests for the message/chat transport, extracted from app.js in v0.5.4.
//
// Three things here are load-bearing and were untested:
//   * the CHANNEL contract differs from the DM one. A bare {from, body} 422'd, and `subject`/`inReplyTo`
//     are not part of it — sending them back would reintroduce the rejection the current shape fixes.
//   * `/channels` only computes per-channel unread_count when `agentId` is supplied. Without it every
//     unread badge in the rail sat permanently at 0, which looks like "no unread" rather than a bug.
//   * a send that never settles leaves the composer disabled with no error, so it is raced against an
//     AbortController rather than left to the browser default.
//
// A REAL LOOPBACK SERVER on 127.0.0.2: these functions call `api()`, an imported binding, and the point of
// the extraction is that the module runs in Node.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { setApiBase } from "./api-client.mjs";
import { state } from "./state.mjs";
import {
  chatLoadChannels, chatLoadConversation, chatSendMessage, sendMessageWithTimeout, sendRunFollowup,
} from "./message-transport.mjs";

let HANDLER = (_req, res) => { res.writeHead(200); res.end("{}"); };
const SEEN = [];
const SOCKETS = new Set();
const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => { SEEN.push({ url: req.url, method: req.method, body }); HANDLER(req, res); });
});
SERVER.on("connection", (s) => { SOCKETS.add(s); s.on("close", () => SOCKETS.delete(s)); });
const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
setApiBase(`http://127.0.0.2:${PORT}/api/v1`);

// Destroy sockets as well as closing: one test deliberately leaves a request unanswered, and close()
// alone waits on it.
test.after(() => { for (const s of SOCKETS) s.destroy(); SERVER.close(); });

function respond(payload, status = 200) {
  SEEN.length = 0;
  HANDLER = (_req, res) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(payload)); };
}

const sent = () => JSON.parse(SEEN[0].body);

// --- the channel contract -------------------------------------------------

test("a channel send carries from_agent AND channel — the bare {from, body} shape 422'd", async () => {
  respond({ ok: true });
  await chatSendMessage({ isChannel: true, target: "dev", identity: "me", body: "hello" });
  assert.equal(SEEN[0].url, "/api/v1/channels/dev/send");
  assert.equal(sent().from_agent, "me");
  assert.equal(sent().channel, "dev", "the channel is named in the BODY as well as the path");
  assert.equal(sent().body, "hello");
});

test("subject and inReplyTo are NOT sent to a channel — they are not in its contract", async () => {
  // The DM path derives a subject from the body. Doing that here would put a field on the request that
  // the channel model rejects.
  respond({ ok: true });
  await chatSendMessage({
    isChannel: true, target: "dev", identity: "me", body: "hello",
    subject: "a subject", inReplyTo: "msg-1", expectsReply: true,
  });
  const payload = sent();
  assert.ok(!("subject" in payload), "subject must not reach the channel endpoint");
  assert.ok(!("inReplyTo" in payload), "inReplyTo must not reach the channel endpoint");
  assert.ok(!("requireReply" in payload), "the DM-only reply flag must not either");
});

test("a channel name is URL-encoded into the path", async () => {
  respond({ ok: true });
  await chatSendMessage({ isChannel: true, target: "team/ops", identity: "me", body: "x" });
  assert.equal(SEEN[0].url, "/api/v1/channels/team%2Fops/send");
});

test("'normal' priority is OMITTED rather than sent", async () => {
  // Conditional spread: the service has its own default, and sending the default back makes every
  // message look explicitly prioritised.
  respond({ ok: true });
  await chatSendMessage({ isChannel: true, target: "dev", identity: "me", body: "x", priority: "normal" });
  assert.ok(!("priority" in sent()), "the default must not be transmitted");

  respond({ ok: true });
  await chatSendMessage({ isChannel: true, target: "dev", identity: "me", body: "x", priority: "high" });
  assert.equal(sent().priority, "high", "a non-default priority IS transmitted");
});

// --- the DM contract ------------------------------------------------------

test("an explicit type wins; otherwise expectsReply picks request vs info", async () => {
  // The heuristic is back-compat for composers that predate the type selector. An explicit choice must
  // not be silently overridden by it.
  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: "x", expectsReply: true });
  assert.equal(sent().type, "request");

  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: "x", expectsReply: false });
  assert.equal(sent().type, "info");

  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: "x", expectsReply: false, type: "alert" });
  assert.equal(sent().type, "alert", "an explicit type must win over the heuristic");
});

test("a subject is derived from the body when none is given, and capped at 80 chars", async () => {
  respond({ ok: true });
  const long = "x".repeat(200);
  await chatSendMessage({ target: "a1", identity: "me", body: long });
  assert.equal(sent().subject.length, 80, "an unbounded subject would carry the whole message");

  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: long, subject: "   chosen   " });
  assert.equal(sent().subject, "chosen", "an explicit subject wins and is trimmed");

  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: "short", subject: "   " });
  assert.equal(sent().subject, "short", "a whitespace-only subject is not a subject");
});

test("requireReply and queueIfBusy are always present as booleans, never undefined", async () => {
  // `!!expectsReply`. These are flags the service branches on; sending `undefined` is not the same as
  // sending false once the payload is serialised — the key vanishes.
  respond({ ok: true });
  await chatSendMessage({ target: "a1", identity: "me", body: "x" });
  const payload = sent();
  assert.equal(payload.requireReply, false);
  assert.equal(payload.queueIfBusy, false);
  assert.equal(payload.trigger, true, "a DM from the composer always triggers");
});

// --- the loaders ----------------------------------------------------------

test("loading channels passes the viewer id — without it every unread badge is 0", async () => {
  // Not cosmetic: /channels only computes per-channel unread_count when agentId is supplied, and a
  // permanently-0 badge reads as "nothing unread" rather than as a missing parameter.
  respond({ channels: [{ name: "dev", unread_count: 3 }] });
  state.chat = { identity: "me@host", channels: [], channelMessages: {} };
  await chatLoadChannels();
  assert.match(SEEN[0].url, /agentId=me%40host/, "the identity must be URL-encoded into the query");
  assert.deepEqual(state.chat.channels, [{ name: "dev", unread_count: 3 }]);
});

test("a failed channel load KEEPS the previous list", async () => {
  respond({ detail: "boom" }, 500);
  state.chat = { identity: "me", channels: [{ name: "prior" }], channelMessages: {} };
  await chatLoadChannels();
  assert.deepEqual(state.chat.channels, [{ name: "prior" }], "a transient failure must not empty the rail");
});

test("loading a CONVERSATION does not swallow errors, unlike the channel list", async () => {
  // A deliberate asymmetry, pinned because it looks like an oversight. The list is background refresh —
  // failing quietly is right. Opening a conversation is a direct action, and silently showing an empty
  // thread would be indistinguishable from a conversation with no messages.
  respond({ detail: "nope" }, 500);
  state.chat = { identity: "me", channels: [], channelMessages: {} };
  await assert.rejects(() => chatLoadConversation("dev"));
});

test("a conversation accepts either payload shape and defaults to empty", async () => {
  state.chat = { identity: "me", channels: [], channelMessages: {} };

  respond({ messages: [{ id: "m1" }] });
  await chatLoadConversation("dev");
  assert.deepEqual(state.chat.channelMessages.dev, [{ id: "m1" }]);

  respond({ channel: { messages: [{ id: "m2" }] } });
  await chatLoadConversation("dev");
  assert.deepEqual(state.chat.channelMessages.dev, [{ id: "m2" }], "the nested shape is also accepted");

  respond({});
  await chatLoadConversation("dev");
  assert.deepEqual(state.chat.channelMessages.dev, [], "neither shape yields an empty thread, not undefined");
});

// --- the timeout ----------------------------------------------------------

test("a send that never answers is ABORTED rather than hanging the composer", async () => {
  // THE REASON THE PRIMITIVE EXISTS. Without the AbortController this promise never settles and the
  // composer stays disabled with nothing shown to the operator.
  SEEN.length = 0;
  HANDLER = () => { /* deliberately never responds */ };
  await assert.rejects(
    () => sendMessageWithTimeout({ from_agent: "me", to: "a1", body: "x" }, 100),
    (error) => {
      assert.match(String(error), /abort/i, "it must fail as an abort, not as a generic network error");
      return true;
    },
  );
});

test("the timeout is CLEARED on success, so a slow-but-fine send is not cancelled later", async () => {
  // `finally { clearTimeout(timer) }`. A leaked timer would also keep the page's event loop busy.
  respond({ ok: true });
  const result = await sendMessageWithTimeout({ from_agent: "me", to: "a1", body: "x" }, 5000);
  assert.deepEqual(result, { ok: true });
});

// --- run follow-ups -------------------------------------------------------
//
// `sendRunFollowup` is the Runs view's two buttons, Retry and Queue-after. The v0.6 Phase 3 census
// found nothing called it. It is worth testing because it is a message BUILDER whose every field is a
// promise to the receiving agent: `queueIfBusy` decides whether the follow-up interrupts a working
// agent or waits, `requireReply` opens a tracked contract, and `inReplyTo` is what threads the answer
// back to the original message rather than starting an orphan thread.

const followupRun = (over = {}) => ({
  id: "run-7",
  agentId: "coder",
  subject: "build the thing",
  body: "original brief",
  messageId: "msg-42",
  ...over,
});

test("a follow-up with no resolvable target sends nothing at all", async () => {
  respond({ ok: true });
  await sendRunFollowup({ id: "run-orphan" });
  assert.equal(SEEN.length, 0, "a run with no agent must not produce a message addressed to nobody");
});

test("a queue-after follow-up waits for the agent rather than interrupting it", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun());
  const payload = sent();
  assert.equal(payload.to, "coder");
  assert.equal(payload.queueIfBusy, true, "queue-after must not steer into a run already in flight");
  assert.equal(payload.trigger, true, "it still has to wake an idle agent");
  assert.equal(payload.requireReply, true, "a follow-up opens a tracked contract");
  assert.match(payload.subject, /^Queue after run-7$/);
});

test("a retry names itself a retry, so the thread does not read as a second request", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun(), { retry: true });
  assert.equal(sent().subject, "Retry: build the thing");
});

test("a retry of a run with no subject falls back to the run id", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun({ subject: "" }), { retry: true });
  assert.equal(sent().subject, "Retry: run-7");
});

test("an explicit body wins over the run's own text", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun(), { body: "do it differently this time" });
  assert.equal(sent().body, "do it differently this time");
});

test("without a body the follow-up carries the run's own brief, not an empty message", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun());
  assert.equal(sent().body, "original brief");
});

test("a run with neither body nor summary still says something", async () => {
  // The last resort matters: an empty body reaches the agent as a wake with no instruction, which is
  // indistinguishable from a bug on the receiving end.
  respond({ ok: true });
  await sendRunFollowup(followupRun({ body: "", summary: "", subject: "" }));
  assert.equal(sent().body, "Follow-up for run-7");
});

test("the follow-up threads onto the original message", async () => {
  respond({ ok: true });
  await sendRunFollowup(followupRun());
  assert.equal(sent().inReplyTo, "msg-42", "without this the answer starts an orphan thread");
});

test("the snake_case message id is accepted too", async () => {
  // Runs arrive from two shapes depending on the endpoint; reading only one silently drops threading.
  respond({ ok: true });
  await sendRunFollowup(followupRun({ messageId: undefined, message_id: "msg-99" }));
  assert.equal(sent().inReplyTo, "msg-99");
});
