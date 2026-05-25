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
