import assert from "assert";
import test from "node:test";
import { startSessionHandleHeartbeat } from "../session-handle-heartbeat.js";

function makeMockAdapter(returnValues) {
  let i = 0;
  return {
    getCurrentSessionId: () => {
      const v = returnValues[Math.min(i, returnValues.length - 1)];
      i += 1;
      return v;
    },
  };
}

test("startSessionHandleHeartbeat POSTs when handle changes", async () => {
  const calls = [];
  const adapter = makeMockAdapter([null, "new-handle"]);
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-x",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 1, "expected at least one POST");
  assert.strictEqual(calls[0].agentId, "agent-x");
  assert.strictEqual(calls[0].handle, "new-handle");
});

test("startSessionHandleHeartbeat does not POST when handle unchanged", async () => {
  const calls = [];
  const adapter = makeMockAdapter(["same-handle", "same-handle", "same-handle"]);
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-y",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise((r) => setTimeout(r, 50));
  stop();
  assert.strictEqual(calls.length, 1, "expected exactly one POST (first appearance)");
});

test("startSessionHandleHeartbeat is a no-op without adapter or agentId", () => {
  const stop1 = startSessionHandleHeartbeat({ adapter: null, agentId: "x", intervalMs: 10, postFn: async () => {} });
  const stop2 = startSessionHandleHeartbeat({ adapter: {}, agentId: "", intervalMs: 10, postFn: async () => {} });
  // Both stop() calls must succeed even though they're no-ops
  stop1();
  stop2();
});

test("startSessionHandleHeartbeat swallows post errors", async () => {
  const adapter = makeMockAdapter(["h1"]);
  let stopCalled = false;
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-z",
    intervalMs: 10,
    postFn: async () => { throw new Error("network fail"); },
  });
  await new Promise((r) => setTimeout(r, 30));
  stopCalled = true;
  stop();
  assert.ok(stopCalled, "process did not crash on post failure");
});

test("Heartbeat falls back to adapter.discoverSessionId when env empty", async () => {
  const { startSessionHandleHeartbeat } = await import("../session-handle-heartbeat.js");
  const calls = [];
  const adapter = {
    getCurrentSessionId: () => null, // env empty
    discoverSessionId: async () => "discovered-handle-xyz",
  };
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-discovery",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 1, "heartbeat should fall through to discoverSessionId");
  assert.strictEqual(calls[0].handle, "discovered-handle-xyz");
});

test("Heartbeat prefers getCurrentSessionId over discoverSessionId", async () => {
  const { startSessionHandleHeartbeat } = await import("../session-handle-heartbeat.js");
  const calls = [];
  let discoverCalled = false;
  const adapter = {
    getCurrentSessionId: () => "env-handle",
    discoverSessionId: async () => { discoverCalled = true; return "discovered-other"; },
  };
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-env",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.ok(calls.length >= 1);
  assert.strictEqual(calls[0].handle, "env-handle", "env-read should win when non-null");
  assert.strictEqual(discoverCalled, false, "discoverSessionId should not be called when env has a value");
});

test("Heartbeat handles discoverSessionId returning null gracefully", async () => {
  const { startSessionHandleHeartbeat } = await import("../session-handle-heartbeat.js");
  const calls = [];
  const adapter = {
    getCurrentSessionId: () => null,
    discoverSessionId: async () => null,
  };
  const stop = startSessionHandleHeartbeat({
    adapter,
    agentId: "agent-empty",
    intervalMs: 10,
    postFn: async (agentId, handle) => { calls.push({ agentId, handle }); },
  });
  await new Promise(r => setTimeout(r, 50));
  stop();
  assert.strictEqual(calls.length, 0, "should not POST when both sources return null");
});
