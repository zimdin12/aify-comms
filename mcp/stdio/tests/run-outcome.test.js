// A throw in a successful run's bookkeeping must not report the run as failed (v0.7 scan B11).
import assert from "node:assert/strict";
import test from "node:test";
import { followRunOutcome } from "../run-outcome.mjs";

function recorder() {
  const calls = [];
  return {
    calls,
    handlers: {
      succeed: async (result) => { calls.push(["succeed", result]); throw new Error("runtime-state PATCH refused"); },
      fail: async (error) => { calls.push(["fail", error.message]); },
      always: () => { calls.push(["always"]); },
      log: (...args) => { calls.push(["log", args.join(" ")]); },
    },
  };
}

test("a throw after a successful run is logged, and the failure branch never runs", async () => {
  const { calls, handlers } = recorder();
  await followRunOutcome(Promise.resolve({ status: "completed" }), handlers);
  assert.deepEqual(calls.map(([name]) => name), ["succeed", "log", "always"]);
  assert.match(calls[1][1], /runtime-state PATCH refused/);
});

test("control: a run that failed still takes the failure branch", async () => {
  const { calls, handlers } = recorder();
  await followRunOutcome(Promise.reject(new Error("model crashed")), handlers);
  assert.deepEqual(calls.map(([name]) => name), ["fail", "always"]);
  assert.equal(calls[0][1], "model crashed");
});
