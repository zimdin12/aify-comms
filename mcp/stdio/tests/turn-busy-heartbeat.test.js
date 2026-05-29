import assert from "assert";
import test from "node:test";
import { startTurnBusyHeartbeat, makeDefaultTurnBusyPoster } from "../turn-busy-heartbeat.js";

test("startTurnBusyHeartbeat POSTs every interval while active", async () => {
  const calls = [];
  let controllerActive = true;
  const stop = startTurnBusyHeartbeat({
    agentId: "agent-x",
    intervalMs: 10,
    isActive: () => controllerActive,
    postFn: async (agentId) => { calls.push(agentId); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 2, `expected >=2 POSTs in 50ms with 10ms interval; got ${calls.length}`);
  assert.strictEqual(calls[0], "agent-x");
});

test("startTurnBusyHeartbeat stops POSTing when isActive returns false", async () => {
  const calls = [];
  let controllerActive = true;
  const stop = startTurnBusyHeartbeat({
    agentId: "agent-y",
    intervalMs: 10,
    isActive: () => controllerActive,
    postFn: async (agentId) => { calls.push(agentId); },
  });
  await new Promise(r => setTimeout(r, 30));
  controllerActive = false;
  const callsAtFlip = calls.length;
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.strictEqual(calls.length, callsAtFlip, `expected no new POSTs after isActive->false; got ${calls.length - callsAtFlip} extra`);
});

test("startTurnBusyHeartbeat is no-op with missing params", () => {
  const stop1 = startTurnBusyHeartbeat({});
  stop1();
  const stop2 = startTurnBusyHeartbeat({ agentId: "x" });
  stop2();
});

test("startTurnBusyHeartbeat swallows postFn errors", async () => {
  const stop = startTurnBusyHeartbeat({
    agentId: "agent-err",
    intervalMs: 10,
    isActive: () => true,
    postFn: async () => { throw new Error("net fail"); },
  });
  await new Promise(r => setTimeout(r, 30));
  stop();
  // No assertion needed - test passes if we didn't crash
  assert.ok(true);
});

test("makeDefaultTurnBusyPoster refreshes BOTH turn-start and bridge heartbeat", async () => {
  const origFetch = globalThis.fetch;
  const hits = [];
  globalThis.fetch = async (url, opts) => {
    hits.push({ url: String(url), body: JSON.parse(opts.body) });
    return { ok: true, status: 200 };
  };
  try {
    const post = makeDefaultTurnBusyPoster("http://svc:8800", "", "bridge-abc");
    await post("agent-z");
  } finally {
    globalThis.fetch = origFetch;
  }
  const turn = hits.find(h => h.url.endsWith("/agents/agent-z/turn-start"));
  const beat = hits.find(h => h.url.endsWith("/agents/agent-z/heartbeat"));
  assert.ok(turn, "expected a /turn-start POST to keep turn_state fresh");
  assert.ok(beat, "expected a /heartbeat POST to refresh bridge_instances.last_seen during long turns");
  assert.strictEqual(beat.body.bridgeId, "bridge-abc", "heartbeat must carry the bridge id so the right bridge lease is refreshed");
  assert.ok(!("turnBusy" in beat.body), "keep-alive heartbeat is liveness-only (no turnBusy field)");
});

test("makeDefaultTurnBusyPoster without bridgeId keeps legacy turn-start-only behavior", async () => {
  const origFetch = globalThis.fetch;
  const hits = [];
  globalThis.fetch = async (url, opts) => {
    hits.push(String(url));
    return { ok: true, status: 200 };
  };
  try {
    const post = makeDefaultTurnBusyPoster("http://svc:8800");
    await post("agent-q");
  } finally {
    globalThis.fetch = origFetch;
  }
  assert.ok(hits.some(u => u.endsWith("/agents/agent-q/turn-start")), "expected /turn-start");
  assert.ok(!hits.some(u => u.endsWith("/agents/agent-q/heartbeat")), "no bridgeId => no heartbeat POST");
});
