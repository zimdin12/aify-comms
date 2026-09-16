// A session id the service REFUSED is offered again on the next tick; any other answer is final.
//
// EXTERNAL REVIEW, 2026-09-16. DECISIONS.md said an agent whose id was parked because another live agent held
// it "waits out the lease" and then takes it. It could not: the service answers a refusal with HTTP 200 and
// `state: "session-collision"`, the default poster resolved on any 200, and the heartbeat then recorded the id
// as sent and never offered it again. Measured by the reviewer with the real module against a fake service:
// 1 PATCH in about 15 ticks. So a refused agent stayed without its conversation until a Confirm, a relaunch or
// a change of session id -- and a dashboard Restart in that window started a FRESH conversation.
//
// `session-changed` is deliberately NOT retried: that id is parked for an operator's Confirm, and offering it
// again changes nothing but the noise.

import assert from "node:assert/strict";
import test from "node:test";

import { makeDefaultHandlePoster, startSessionHandleHeartbeat } from "../session-handle-heartbeat.js";

const adapter = (id) => ({ getCurrentSessionId: () => id });

async function postsFor(answer, ticks = 6) {
  const calls = [];
  const stop = startSessionHandleHeartbeat({
    adapter: adapter("11111111-2222-4333-8444-555555555555"),
    agentId: "agent-b",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push(handle); return answer; },
  });
  await new Promise((resolve) => setTimeout(resolve, ticks * 10 + 15));
  stop();
  return calls.length;
}

test("a refused id is offered again on the next tick, so it is taken once the owner's lease runs out", async () => {
  assert.ok(await postsFor({ ok: true, state: "session-collision", collisionWith: "agent-a" }) >= 3,
    "a refusal was recorded as delivered and never offered again");
});

test("CONTROLS: an adopted id, one parked for Confirm, and an answer with no body are each sent once", async () => {
  assert.equal(await postsFor({ ok: true, state: "adopted" }), 1, "an adopted id was sent again");
  assert.equal(await postsFor({ ok: true, state: "session-changed" }), 1, "an id waiting for Confirm was re-sent");
  assert.equal(await postsFor(undefined), 1, "a poster that answers nothing is not a refusal");
});

test("the default poster hands the service's answer back, and an empty 200 is not a refusal", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ ok: true, state: "session-collision", collisionWith: "agent-a" }) });
    assert.equal((await makeDefaultHandlePoster("http://svc")("agent-b", "h"))?.state, "session-collision");
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => "" });
    assert.equal(await makeDefaultHandlePoster("http://svc")("agent-b", "h"), null, "an empty body read as an answer");
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => "not json" });
    assert.equal(await makeDefaultHandlePoster("http://svc")("agent-b", "h"), null, "an unreadable body read as an answer");
  } finally {
    globalThis.fetch = original;
  }
});
