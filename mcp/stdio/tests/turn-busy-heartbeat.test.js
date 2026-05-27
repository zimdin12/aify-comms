import assert from "assert";
import test from "node:test";
import { startTurnBusyHeartbeat } from "../turn-busy-heartbeat.js";

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
