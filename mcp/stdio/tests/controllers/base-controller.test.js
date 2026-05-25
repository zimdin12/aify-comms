import assert from "assert";
import test from "node:test";
import { BaseController } from "../../controllers/base-controller.js";

test("BaseController abstract methods throw on direct instantiation", async () => {
  const c = new BaseController({ agentId: "x" });
  await assert.rejects(() => c.start({}), /abstract/);
  await assert.rejects(() => c.injectMessage({}), /abstract/);
  await assert.rejects(() => c.interrupt({}), /abstract/);
  await assert.rejects(() => c.steer({}), /abstract/);
});

test("BaseController preserves opts on instance", () => {
  const c = new BaseController({ agentId: "x", runtime: "test" });
  assert.deepStrictEqual(c.opts, { agentId: "x", runtime: "test" });
});

test("BaseController terminalSink defaults to null", () => {
  const c = new BaseController({ agentId: "x" });
  assert.strictEqual(c.terminalSink, null);
});

test("BaseController subclass can override start", async () => {
  class TestController extends BaseController {
    async start(ctx) { return { ok: true, ctx }; }
  }
  const c = new TestController({ agentId: "x" });
  const result = await c.start({ runId: "r" });
  assert.deepStrictEqual(result, { ok: true, ctx: { runId: "r" } });
});
