import assert from "assert";
import test from "node:test";
import { BaseController, DelegatedManagedController } from "../../controllers/base-controller.js";
import { declaringModules } from "../bridge-sources.mjs";

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

test("DelegatedManagedController resolves at once as delegated, with no-op controls, and marks ready", async () => {
  // The controller for managed-via-wrapper dispatch: the wrapper's child bridge owns the real work.
  let ready = 0;
  const c = new DelegatedManagedController({ agentId: "x" });
  c.setReadyListener(() => { ready += 1; });
  const handle = c.start();
  assert.strictEqual(ready, 1);
  assert.deepStrictEqual(handle.capabilities, { interrupt: false, steer: false });
  assert.strictEqual((await handle.promise).status, "delegated");
  await handle.interrupt();
  await handle.steer();
});

test("DelegatedManagedController has one declaration, and the census can see a class", () => {
  // It was declared twice, byte-identical, in the codex and hermes controllers. The census found that
  // copy only after it learned to see classes, so this also keeps that ability honest.
  assert.deepStrictEqual(declaringModules("DelegatedManagedController"),
    [{ file: "controllers/base-controller.js", kind: "class" }]);
  assert.deepStrictEqual(declaringModules("NoSuchClassAnywhere"), []);
});
