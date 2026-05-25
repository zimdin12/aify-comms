import assert from "assert";
import test from "node:test";
import { HermesController } from "../../controllers/hermes-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("HermesController extends BaseController", () => {
  const c = new HermesController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "hermes" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "managed",
  });
  assert.ok(c instanceof BaseController);
});

test("HermesController exposes start/injectMessage/interrupt/steer", () => {
  const c = new HermesController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "hermes" },
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
