import assert from "assert";
import test from "node:test";
import { RuntimeAdapter } from "../../adapters/base.js";

class _TestAdapter extends RuntimeAdapter {
  get name() { return "test-runtime"; }
  get sessionEnvVars() { return ["TEST_SESSION_ID"]; }
}

test("RuntimeAdapter.discoverSessionId default returns null", async () => {
  const a = new _TestAdapter();
  assert.strictEqual(await a.discoverSessionId(), null);
});

test("discoverSessionId is async", () => {
  const a = new _TestAdapter();
  const result = a.discoverSessionId();
  assert.ok(typeof result.then === "function", "discoverSessionId must return a Promise");
});
