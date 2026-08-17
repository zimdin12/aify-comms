// Real tests for the required-reply handoff, extracted from server.js in v0.5.4.
//
// This decides what happens to a run that REQUIRED a reply and ended without one, and the two policies
// differ in exactly the way that matters: strict mode records that a reply is still owed, fallback mode
// mirrors the agent's final output as the reply. Mirroring when strict mode is on would fabricate an
// answer out of working/telemetry text and close a run nobody actually answered — which is worse than
// leaving it visibly unanswered.
//
// server.js is imported by no test at all, so none of this had coverage.
//
// A REAL HTTP SERVER on 127.0.0.2 rather than a stubbed `httpCall`: the module reaches the service through
// an imported binding that cannot be monkey-patched. One server for the whole file with a swappable
// handler — a per-test server plus a cache-busted import does NOT bust `aify-service-endpoint.mjs`
// underneath, which resolves its target once at load, and every later test then posts at a closed port.

import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

let ROUTES = {};
const REQUESTS = [];

const SERVER = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    // `httpCall` prefixes /api/v1; routes below are written as the service paths, so strip it once here
    // rather than repeating the prefix in every key. My first version matched the raw URL, so no route
    // ever hit, every call 404d, and the failures read like the module ignoring its own guards.
    const path = req.url.replace(/^\/api\/v1/, "");
    REQUESTS.push({ method: req.method, url: path, body: body ? JSON.parse(body) : null });
    const key = Object.keys(ROUTES).find((k) => path.startsWith(k));
    const reply = key ? ROUTES[key] : { status: 404, payload: { error: "no route" } };
    res.writeHead(reply.status, { "content-type": "application/json" });
    res.end(JSON.stringify(reply.payload ?? {}));
  });
});

const PORT = await new Promise((r) => SERVER.listen(0, "127.0.0.2", () => r(SERVER.address().port)));
process.env.AIFY_SERVER_URL = `http://127.0.0.2:${PORT}`;
// The modules read `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL` — the LEGACY name WINS, and a
// live wrapper environment exports it. Setting only the new name left the fake below unused.
process.env.CLAUDE_MCP_SERVER_URL = `http://127.0.0.2:${PORT}`;
process.env.AIFY_API_KEY = "test-key";
// Paired with the LEGACY name, which the modules read FIRST: a wrapper environment exports it,
// and leaving it set means the module sends the operator's real key instead of this one.
process.env.CLAUDE_MCP_API_KEY = "test-key";
const m = await import("../required-reply-handoff.mjs");

test.after(() => SERVER.close());

function scenario(routes) {
  ROUTES = routes;
  REQUESTS.length = 0;
  // The settings cache memoises for 5s; reset it so each test chooses its own policy.
  m._replyCaptureFallbackCache.fetchedAt = 0;
  return REQUESTS;
}

const RUN = { id: "run-1", from: "manager" };

test("a run with no id or no sender is left alone entirely", async () => {
  const requests = scenario({});
  await m.ensureRequiredReplyHandoff("coder", {});
  await m.ensureRequiredReplyHandoff("coder", { id: "r" });
  await m.ensureRequiredReplyHandoff("coder", { from: "manager" });
  assert.deepEqual(requests, [], "nothing to hand off means nothing to ask the service about");
});

test("a run that does not require a reply is left alone", async () => {
  const requests = scenario({ "/dispatch/runs/": { status: 200, payload: { run: { requireReply: false } } } });
  await m.ensureRequiredReplyHandoff("coder", RUN);
  assert.equal(requests.length, 1, "it reads the run and then stops");
  assert.equal(requests[0].method, "GET");
});

test("a run that ALREADY has a reply is left alone", async () => {
  // The agent answered through comms_send. Mirroring on top would post a duplicate reply.
  const requests = scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true, resultMessageId: "m-9" } } },
  });
  await m.ensureRequiredReplyHandoff("coder", RUN);
  assert.equal(requests.length, 1);
});

test("STRICT mode records that the reply is owed and does NOT fabricate one", async () => {
  const requests = scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
    "/settings": { status: 200, payload: { settings: { managed_reply_capture_fallback: false } } },
  });
  await m.ensureRequiredReplyHandoff("coder", RUN, "completed", "some working output");

  const posted = requests.filter((r) => r.method === "POST");
  assert.deepEqual(posted, [], "strict mode must not send a message on the agent's behalf");

  const patch = requests.find((r) => r.method === "PATCH");
  assert.ok(patch, "it must still record WHY the run is unanswered");
  assert.equal(patch.body.eventType, "handoff");
  assert.match(patch.body.appendEvent, /Reply still owed to manager/,
    "the event names who is waiting, or the run is just quietly stuck");
  assert.ok(!patch.body.appendEvent.includes("some working output"),
    "…and must not leak the working text as if it were the answer");
});

test("FALLBACK mode mirrors the final output as the reply", async () => {
  const requests = scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
    "/settings": { status: 200, payload: { settings: { managed_reply_capture_fallback: true } } },
    "/messages": { status: 200, payload: { id: "m-1" } },
  });
  await m.ensureRequiredReplyHandoff("coder", RUN, "completed", "the actual answer");

  const posted = requests.find((r) => r.method === "POST" && r.url.includes("/messages"));
  assert.ok(posted, "fallback mode must close the run with a reply");
  assert.equal(posted.body.from_agent, "coder");
  assert.equal(posted.body.to, "manager", "the reply goes back to whoever asked");

  // THE IDEMPOTENCY NONCE (Task #240), previously asserted as source text in
  // message-idempotency-retry.test.js. It is DETERMINISTIC and run-keyed, so a transient socket error can
  // be retried without double-sending: the server collapses the retry on the nonce. A random nonce here
  // would post the same reply twice on any retry.
  assert.equal(posted.body.clientNonce, "handoff-run-1-completed");
});

test("the handoff nonce changes with the terminal status, but not between retries", async () => {
  const seen = [];
  for (const status of ["completed", "failed", "completed"]) {
    const requests = scenario({
      "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
      "/settings": { status: 200, payload: { settings: { managed_reply_capture_fallback: true } } },
      "/messages": { status: 200, payload: { id: "m-1" } },
    });
    await m.ensureRequiredReplyHandoff("coder", RUN, status, "out");
    seen.push(requests.find((r) => r.method === "POST").body.clientNonce);
  }
  assert.equal(seen[0], seen[2], "the same run and status must mint the same nonce — that is what dedups");
  assert.notEqual(seen[0], seen[1], "a different outcome is a different message, not a duplicate");
});

test("the settings read is CACHED for 5 seconds", async () => {
  const requests = scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
    "/settings": { status: 200, payload: { settings: { managed_reply_capture_fallback: false } } },
  });
  await m.ensureRequiredReplyHandoff("coder", RUN);
  await m.ensureRequiredReplyHandoff("coder", RUN);
  const settingsReads = requests.filter((r) => r.url.startsWith("/settings")).length;
  assert.equal(settingsReads, 1, "the policy must not cost a settings round trip per turn end");
});

test("a settings fetch that FAILS serves the stale value instead of flipping the policy", async () => {
  // This runs at turn end. A transient settings outage deciding whether replies get fabricated would be a
  // policy change by accident.
  scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
    "/settings": { status: 200, payload: { settings: { managed_reply_capture_fallback: false } } },
  });
  await m.ensureRequiredReplyHandoff("coder", RUN);
  assert.equal(m._replyCaptureFallbackCache.value, false, "strict was cached");

  const requests = scenario({
    "/dispatch/runs/": { status: 200, payload: { run: { requireReply: true } } },
    "/settings": { status: 500, payload: { error: "down" } },
  });
  m._replyCaptureFallbackCache.fetchedAt = 0;          // force a re-read that will fail
  m._replyCaptureFallbackCache.value = false;          // …with strict as the last known good
  await m.ensureRequiredReplyHandoff("coder", RUN);
  assert.deepEqual(requests.filter((r) => r.method === "POST" && r.url.includes("/messages")), [],
    "a failed settings read must keep the last known policy, not fall back to mirroring");
});
