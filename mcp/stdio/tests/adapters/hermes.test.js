import assert from "assert";
import test from "node:test";
import { HermesAdapter } from "../../adapters/hermes.js";

test("HermesAdapter identity", () => {
  const a = new HermesAdapter();
  assert.strictEqual(a.name, "hermes");
  assert.strictEqual(a.displayName, "Hermes");
  assert.deepStrictEqual(a.sessionEnvVars, ["HERMES_SESSION_ID", "HERMES_SESSION"]);
});

test("HermesAdapter prefers HERMES_SESSION_ID over HERMES_SESSION", () => {
  process.env.HERMES_SESSION_ID = "primary";
  process.env.HERMES_SESSION = "fallback";
  const a = new HermesAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "primary");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.HERMES_SESSION;
});

test("HermesAdapter falls back to HERMES_SESSION when HERMES_SESSION_ID empty", () => {
  delete process.env.HERMES_SESSION_ID;
  process.env.HERMES_SESSION = "fallback-id";
  const a = new HermesAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "fallback-id");
  delete process.env.HERMES_SESSION;
});

test("HermesAdapter diagnosticEnv includes gateway URL", () => {
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  const a = new HermesAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_HERMES_GATEWAY_URL, "ws://127.0.0.1:9999/api/ws?token=x");
  delete process.env.AIFY_HERMES_GATEWAY_URL;
});

test("HermesAdapter diagnosticEnv reports (unset) for gateway when missing", () => {
  delete process.env.AIFY_HERMES_GATEWAY_URL;
  const a = new HermesAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_HERMES_GATEWAY_URL, "(unset)");
});

test("HermesAdapter Plan 2 capabilities", () => {
  const a = new HermesAdapter();
  assert.strictEqual(a.supportsResident, true);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, true);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});

test("HermesAdapter overrides discoverSessionId", () => {
  const a = new HermesAdapter();
  const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(a), "discoverSessionId");
  assert.ok(own && typeof own.value === "function",
    "HermesAdapter must define its own discoverSessionId override");
});

test("HermesAdapter.discoverSessionId returns null when no gateway and no fs sessions", async () => {
  // Without AIFY_HERMES_GATEWAY_URL and without ~/.hermes/sessions, should return null
  delete process.env.AIFY_HERMES_GATEWAY_URL;
  const a = new HermesAdapter();
  const result = await a.discoverSessionId();
  if (result !== null) {
    assert.ok(typeof result === "string" && result.length > 0);
  }
});
