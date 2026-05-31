import { test } from "node:test";
import assert from "node:assert/strict";
import { startLivenessHeartbeat } from "../liveness-heartbeat.js";

test("beats immediately and then on the interval; stop() halts beats", async () => {
  const calls = [];
  const stop = startLivenessHeartbeat({
    intervalMs: 20,
    beat: async () => { calls.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, 75)); // ~ first + 3 interval beats
  stop();
  const after = calls.length;
  await new Promise((r) => setTimeout(r, 60));
  assert.ok(after >= 3, `expected >=3 beats, got ${after}`);
  assert.equal(calls.length, after, "no beats after stop()");
});

test("a throwing beat never crashes the timer", async () => {
  let n = 0;
  const stop = startLivenessHeartbeat({
    intervalMs: 15,
    beat: async () => { n += 1; throw new Error("boom"); },
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.ok(n >= 2, "kept beating despite throws");
});
