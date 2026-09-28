// The session-handle heartbeat of a claude started from an agent's shell (review of 7f638a65).
//
// Registration already used the session Claude Code gave the bridge (computeInitialSessionHandle), but
// the heartbeat started on its own and reported what discovery found: the AIFY_AGENT_ID-keyed capture
// store, which names the PARENT's session. The reviewer's probe had the registration say `nested-session`
// and the first heartbeat post `parent-session`. The service now ignores a heartbeat from a bridge that
// holds no registration (bridge_report_gate.py, tested end to end in
// test_a_nested_claude_does_not_stop_its_parent_agent.py); these pin the bridge's half: it reports the
// session it registered, names itself, and offers an ignored report again.

import { test } from "node:test";
import assert from "node:assert/strict";

import { startSessionHandleHeartbeat, makeDefaultHandlePoster } from "../session-handle-heartbeat.js";
import { ClaudeAdapter } from "../adapters/claude.js";

/** Wait for `count` posts, never for a fixed time. */
async function untilPosted(posted, count, deadlineMs = 3000) {
  const deadline = Date.now() + deadlineMs;
  while (posted.length < count && Date.now() < deadline) await new Promise((r) => setTimeout(r, 5));
  assert.ok(posted.length >= count, `saw ${posted.length} of ${count} posts`);
}

function claudeAdapter(env, discovered) {
  const adapter = new ClaudeAdapter();
  const own = adapter.sessionWhenStartedFromAClaudeShell.bind(adapter);
  adapter.sessionWhenStartedFromAClaudeShell = () => own(env);
  adapter.discoverSessionId = async () => discovered;
  adapter.getCurrentSessionId = () => discovered;
  return adapter;
}

test("a claude started from a Claude Code shell reports the session its bridge was given", async () => {
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "parent-agent",
    adapter: claudeAdapter({ CLAUDE_PID: "4242", CLAUDE_CODE_SESSION_ID: "nested-session" }, "parent-session"),
    postFn: async (_agent, handle) => { posted.push(handle); return {}; },
    intervalMs: 20,
  });
  await untilPosted(posted, 1);
  stop();
  assert.deepEqual(posted, ["nested-session"]);
});

test("CONTROL: any other claude still reports what discovery finds", async () => {
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "parent-agent",
    adapter: claudeAdapter({ CLAUDE_CODE_SESSION_ID: "own-session" }, "discovered-session"),
    postFn: async (_agent, handle) => { posted.push(handle); return {}; },
    intervalMs: 20,
  });
  await untilPosted(posted, 1);
  stop();
  assert.deepEqual(posted, ["discovered-session"]);
});

test("an ignored report is offered again, so a bridge whose registration lands later is heard", async () => {
  const posted = [];
  const stop = startSessionHandleHeartbeat({
    agentId: "parent-agent",
    adapter: claudeAdapter({}, "session-a"),
    postFn: async (_agent, handle) => { posted.push(handle); return { state: "bridge-not-registered" }; },
    intervalMs: 20,
  });
  await untilPosted(posted, 3);
  stop();
  assert.ok(posted.every((handle) => handle === "session-a"));
});

test("CONTROL: an accepted report is not repeated", async () => {
  const posted = [];
  let ticks = 0;
  const adapter = claudeAdapter({}, "session-a");
  const discover = adapter.discoverSessionId;
  adapter.discoverSessionId = async () => { ticks += 1; return discover(); };
  const stop = startSessionHandleHeartbeat({
    agentId: "parent-agent",
    adapter,
    postFn: async (_agent, handle) => { posted.push(handle); return { ok: true }; },
    intervalMs: 20,
  });
  const deadline = Date.now() + 3000;
  while (ticks < 4 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 5));
  stop();
  assert.ok(ticks >= 4, `only ${ticks} ticks`);
  assert.deepEqual(posted, ["session-a"]);
});

test("the poster names its bridge, which the service requires of a heartbeat", async () => {
  const bodies = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  try {
    await makeDefaultHandlePoster("http://127.0.0.2:1", "", "bridge-7")("parent-agent", "session-a");
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.deepEqual(bodies, [{ sessionHandle: "session-a", requestedBy: "bridge-heartbeat", bridgeId: "bridge-7" }]);
});
