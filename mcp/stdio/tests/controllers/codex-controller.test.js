import assert from "assert";
import test from "node:test";
import { CodexController } from "../../controllers/codex-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("CodexController extends BaseController", () => {
  const c = new CodexController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "codex" },
    run: {},
    runtimeState: {},
    callbacks: {},
    executionMode: "managed",
  });
  assert.ok(c instanceof BaseController);
});

test("CodexController exposes start/injectMessage/interrupt/steer", () => {
  const c = new CodexController({
    agentId: "x",
    agentInfo: { agent_id: "x", runtime: "codex" },
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
