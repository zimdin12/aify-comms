import assert from "assert";
import test from "node:test";
import { RuntimeAdapter } from "../../adapters/base.js";

// A test double that fills in the abstract members so we can exercise the base
// class's shared logic (normalizeSessionHandle / normalizeModelOverride /
// getCurrentSessionId default impl / diagnosticEnv default impl).
class TestAdapter extends RuntimeAdapter {
  get name() { return "test-runtime"; }
  get sessionEnvVars() { return ["TEST_SESSION_ID", "TEST_SESSION_ALT"]; }
}

test("getCurrentSessionId returns null when all env vars unset", () => {
  delete process.env.TEST_SESSION_ID;
  delete process.env.TEST_SESSION_ALT;
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), null);
});

test("getCurrentSessionId returns first non-empty env var value", () => {
  process.env.TEST_SESSION_ID = "abc-123";
  delete process.env.TEST_SESSION_ALT;
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "abc-123");
  delete process.env.TEST_SESSION_ID;
});

test("getCurrentSessionId falls back to second env var when first is empty", () => {
  process.env.TEST_SESSION_ID = "";
  process.env.TEST_SESSION_ALT = "fallback-id";
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "fallback-id");
  delete process.env.TEST_SESSION_ID;
  delete process.env.TEST_SESSION_ALT;
});

test("getCurrentSessionId rejects placeholder handle values", () => {
  process.env.TEST_SESSION_ID = "unknown";
  const a = new TestAdapter();
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "default";
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "none";
  assert.strictEqual(a.getCurrentSessionId(), null);
  process.env.TEST_SESSION_ID = "null";
  assert.strictEqual(a.getCurrentSessionId(), null);
  delete process.env.TEST_SESSION_ID;
});

test("normalizeSessionHandle trims whitespace", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeSessionHandle("  real-handle  "), "real-handle");
});

test("normalizeSessionHandle returns empty for placeholder", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeSessionHandle("unknown"), "");
  assert.strictEqual(a.normalizeSessionHandle("Default"), "");
  assert.strictEqual(a.normalizeSessionHandle(""), "");
  assert.strictEqual(a.normalizeSessionHandle(null), "");
  assert.strictEqual(a.normalizeSessionHandle(undefined), "");
});

test("resumeArgs returns [--resume, handle] for real handle", () => {
  const a = new TestAdapter();
  assert.deepStrictEqual(a.resumeArgs("real-handle"), ["--resume", "real-handle"]);
});

test("resumeArgs returns [] for empty or placeholder handle", () => {
  const a = new TestAdapter();
  assert.deepStrictEqual(a.resumeArgs(""), []);
  assert.deepStrictEqual(a.resumeArgs("unknown"), []);
  assert.deepStrictEqual(a.resumeArgs(null), []);
});

test("normalizeModelOverride strips placeholders", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeModelOverride("unknown"), "");
  assert.strictEqual(a.normalizeModelOverride("default"), "");
  assert.strictEqual(a.normalizeModelOverride("auto"), "");
  assert.strictEqual(a.normalizeModelOverride(""), "");
});

test("normalizeModelOverride preserves real model names", () => {
  const a = new TestAdapter();
  assert.strictEqual(a.normalizeModelOverride("gpt-5.5"), "gpt-5.5");
  assert.strictEqual(a.normalizeModelOverride("claude-sonnet-4-6"), "claude-sonnet-4-6");
});

test("diagnosticEnv reports session env vars with their values or (unset)", () => {
  delete process.env.TEST_SESSION_ID;
  process.env.TEST_SESSION_ALT = "captured";
  const a = new TestAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.TEST_SESSION_ID, "(unset)");
  assert.strictEqual(env.TEST_SESSION_ALT, "captured");
  delete process.env.TEST_SESSION_ALT;
});

test("abstract base throws when name/sessionEnvVars not overridden", () => {
  class Bad extends RuntimeAdapter {}
  const b = new Bad();
  assert.throws(() => b.name, /abstract/);
  assert.throws(() => b.sessionEnvVars, /abstract/);
});

test("Plan 2 capability getters throw 'not yet implemented'", () => {
  const a = new TestAdapter();
  assert.throws(() => a.supportsResident, /not yet implemented/);
  assert.throws(() => a.supportsManaged, /not yet implemented/);
  assert.throws(() => a.supportsSteering, /not yet implemented/);
  assert.throws(() => a.supportsInterrupt, /not yet implemented/);
  assert.throws(() => a.supportsMultiClient, /not yet implemented/);
  assert.throws(() => a.preferredDeliveryMode, /not yet implemented/);
});

test("Plan 3 console + delivery surface enforced on base", () => {
  const a = new TestAdapter();
  // wrapperName + consoleCommand remain server-side responsibilities; JS base
  // throws so accidental callers fail loudly.
  assert.throws(() => a.wrapperName, /not yet implemented/);
  assert.throws(() => a.consoleCommand({ agentId: "x", handle: "", interactive: true }), /not yet implemented/);
  // controllerFor is abstract on the base — TestAdapter doesn't override it.
  assert.throws(() => a.controllerFor({}), /abstract/);
  // injectMessage/interrupt/steer delegate through controllerFor — if
  // controllerFor itself throws (abstract here), the delegate rejects with
  // that same error.
  assert.rejects(() => a.injectMessage({ text: "hi" }), /abstract/);
  assert.rejects(() => a.interrupt({ reason: "x" }), /abstract/);
  assert.rejects(() => a.steer({ text: "x" }), /abstract/);
});
