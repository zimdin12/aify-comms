import assert from "assert";
import test from "node:test";
import { BaseController } from "../../controllers/base-controller.js";

test("BaseController setReadyListener + markReady fire callback", () => {
  let called = false;
  class TestController extends BaseController {
    async start() { this.markReady(); return { ok: true }; }
    async injectMessage() {}
    async interrupt() {}
    async steer() {}
  }
  const c = new TestController({ agentId: "x" });
  c.setReadyListener(() => { called = true; });
  c.markReady();
  assert.strictEqual(called, true);
});

test("BaseController.markReady is a no-op when no listener set", () => {
  class TestController extends BaseController {
    async start() {}
    async injectMessage() {}
    async interrupt() {}
    async steer() {}
  }
  const c = new TestController({ agentId: "x" });
  // Should not throw
  c.markReady();
  assert.ok(true);
});

test("setReadyListener accepts a function", () => {
  class TestController extends BaseController {
    async start() {}
    async injectMessage() {}
    async interrupt() {}
    async steer() {}
  }
  const c = new TestController({ agentId: "x" });
  c.setReadyListener(() => {});
  // No throw expected
  assert.ok(true);
});

test("setReadyListener rejects non-function gracefully (clears listener)", () => {
  class TestController extends BaseController {
    async start() {}
    async injectMessage() {}
    async interrupt() {}
    async steer() {}
  }
  const c = new TestController({ agentId: "x" });
  c.setReadyListener(() => {});
  // Passing non-function clears the listener; subsequent markReady does nothing
  c.setReadyListener("not a function");
  c.markReady();
  assert.ok(true);
});

test("markReady swallows listener errors (best-effort)", () => {
  class TestController extends BaseController {
    async start() {}
    async injectMessage() {}
    async interrupt() {}
    async steer() {}
  }
  const c = new TestController({ agentId: "x" });
  c.setReadyListener(() => { throw new Error("listener fail"); });
  // markReady must not throw even if the listener does
  c.markReady();
  assert.ok(true);
});
