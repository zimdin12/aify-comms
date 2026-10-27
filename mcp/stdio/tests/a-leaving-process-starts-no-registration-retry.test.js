// A refused start-up registration retries every 30 s. Once the process has begun to leave, no new retry starts,
// and a retry that already started is waited for before resident-lost, like the first attempt.
//
// Reviews of 9f6e1a90 and e1dfe132: a registration that lands after the leaving process reported itself gone
// makes the gone process the agent's owner. `resident-duties.mjs` hands the registrar `active` (not yet leaving)
// and `onAttempt` (track this retry). The real registrar against a loopback stand-in that refuses as a live
// predecessor does; the 30 s are mock time.

import assert from "node:assert/strict";
import http from "node:http";
import { mock, test } from "node:test";

const REFUSAL = { status: 409, body: { detail: "agent 'retry-witness' already has a LIVE resident bridge; pass force=true" } };
let posts = 0;
let answer = () => REFUSAL;
const server = http.createServer((req, res) => {
  req.resume();
  req.on("end", async () => {
    const reply = req.method === "POST" && req.url === "/api/v1/agents" ? await answer(++posts) : { status: 200, body: {} };
    res.writeHead(reply.status, { "content-type": "application/json" });
    res.end(JSON.stringify(reply.body));
  });
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
// Read once, at import, by the endpoint and identity modules: set before the registrar is imported.
Object.assign(process.env, {
  AIFY_SERVER_URL: `http://127.0.0.1:${server.address().port}`, CLAUDE_MCP_SERVER_URL: "",
  AIFY_AGENT_ID: "retry-witness", AIFY_RUNTIME: "claude-code", AIFY_SESSION_MODE: "resident",
});
const { makeAutoRegister } = await import("../auto-registration.mjs");
const { ResidentDuties } = await import("../resident-duties.mjs");

/** Wait until the stand-in has seen `n` registration POSTs, failing by name rather than hanging (review of 4e5d8faa). */
async function untilPosts(n, tick) {
  const deadline = Date.now() + 5000;
  while (posts < n) {
    assert.ok(Date.now() < deadline, `registration POST ${n} never reached the stand-in`);
    await tick();
  }
}

async function postsAfterThirtySeconds(active) {
  posts = 0;
  answer = () => REFUSAL;
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    await makeAutoRegister({ ensureDispatchLoop: () => {}, active })();
    assert.equal(posts, 1, "the first registration did not reach the stand-in");
    mock.timers.tick(30_000);
  } finally {
    mock.timers.reset();
  }
  await new Promise((resolve) => setTimeout(resolve, 500));
  return posts;
}

test("a process that has begun to leave starts no retry", async () => {
  assert.equal(await postsAfterThirtySeconds(() => false), 1);
});

test("control: a process still running retries after 30 s", async () => {
  assert.equal(await postsAfterThirtySeconds(() => true), 2);
});

test("a retry already in flight when the process leaves is answered before resident-lost is sent", async () => {
  const order = [];
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  posts = 0;
  answer = async (n) => {
    if (n === 1) return REFUSAL;
    await held;
    order.push("retry answered");
    return { status: 200, body: { ok: true, agentId: "retry-witness" } };
  };
  const duties = new ResidentDuties({
    agentId: "retry-witness", ownerId: "owner-1", env: { AIFY_SESSION_MODE: "resident" }, boundMs: 5000,
    call: async (method, url) => { if (url.endsWith("/resident-lost")) order.push("loss"); return {}; },
    removeBinding: () => {},
  });
  duties.startBeats = () => [];  // the real beats would reach the stand-in's other routes; they are not under test
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    duties.start();
    await untilPosts(1, () => new Promise((resolve) => setImmediate(resolve)));  // setTimeout is mocked here
    await duties.registering;
    mock.timers.tick(30_000);
  } finally {
    mock.timers.reset();
  }
  await untilPosts(2, () => new Promise((resolve) => setTimeout(resolve, 10)));
  const leaving = duties.end();
  await new Promise((resolve) => setTimeout(resolve, 200));
  assert.deepEqual(order, [], "resident-lost was sent while the retry was still in flight");
  release();
  await leaving;
  assert.deepEqual(order, ["retry answered", "loss"]);
});

test.after(() => new Promise((resolve) => server.close(resolve)));
