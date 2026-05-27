import assert from "assert";
import test from "node:test";
import { ClaudeController } from "../../controllers/claude-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("ClaudeController extends BaseController", () => {
  const c = new ClaudeController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "claude-code" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "channel",
  });
  assert.ok(c instanceof BaseController);
});

test("ClaudeController exposes start/injectMessage/interrupt/steer", () => {
  const c = new ClaudeController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "claude-code" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "channel",
  });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});
