import assert from "assert";
import test from "node:test";
import { PiController } from "../../controllers/pi-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("PiController extends BaseController", () => {
  const c = new PiController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "pi" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "managed",
  });
  assert.ok(c instanceof BaseController);
});

test("PiController exposes start/injectMessage/interrupt/steer", () => {
  const c = new PiController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "pi" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "managed",
  });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});

test("PiController preserves opts", () => {
  const opts = {
    agentId: "x",
    agentInfo: { runtime: "pi" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "managed",
  };
  const c = new PiController(opts);
  assert.deepStrictEqual(c.opts, opts);
});
