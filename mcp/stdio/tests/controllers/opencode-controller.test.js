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

test("OpencodeController steers through OpenCode promptAsync", async () => {
  const calls = [];
  const c = new OpencodeController({ agentId: "x", agentInfo: {}, run: {}, runtimeState: {}, callbacks: {} });
  c._sessionId = "session-1";
  c._cwd = "/work";
  c._open = {
    client: {
      session: {
        promptAsync: async (input) => {
          calls.push(input);
          return {};
        },
      },
    },
  };

  await c._legacyShape().steer("new information");

  assert.deepStrictEqual(calls, [{
    path: { id: "session-1" },
    query: { directory: "/work" },
    body: { parts: [{ type: "text", text: "new information" }] },
  }]);
});
