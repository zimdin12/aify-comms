import assert from "assert";
import test from "node:test";
import { OpencodeController } from "../../controllers/opencode-controller.js";
import { BaseController } from "../../controllers/base-controller.js";

test("OpencodeController extends BaseController", () => {
  const c = new OpencodeController({ agentId: "x", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.ok(c instanceof BaseController, "OpencodeController must extend BaseController");
});

test("OpencodeController exposes start/injectMessage/interrupt/steer", () => {
  const c = new OpencodeController({ agentId: "x", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  assert.strictEqual(typeof c.start, "function");
  assert.strictEqual(typeof c.injectMessage, "function");
  assert.strictEqual(typeof c.interrupt, "function");
  assert.strictEqual(typeof c.steer, "function");
});

test("OpencodeController preserves opts", () => {
  const opts = { agentId: "x", agentInfo: { runtime: "opencode" }, run: {}, runtimeState: {}, callbacks: {} };
  const c = new OpencodeController(opts);
  assert.deepStrictEqual(c.opts, opts);
});
