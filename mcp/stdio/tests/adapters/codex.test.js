import assert from "assert";
import test from "node:test";
import { CodexAdapter } from "../../adapters/codex.js";

test("CodexAdapter identity", () => {
  const a = new CodexAdapter();
  assert.strictEqual(a.name, "codex");
  assert.strictEqual(a.displayName, "Codex");
  assert.deepStrictEqual(a.sessionEnvVars, ["CODEX_THREAD_ID"]);
});

test("CodexAdapter reads CODEX_THREAD_ID", () => {
  process.env.CODEX_THREAD_ID = "019d-thread";
  const a = new CodexAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "019d-thread");
  delete process.env.CODEX_THREAD_ID;
});

test("CodexAdapter diagnosticEnv includes app-server URL", () => {
  process.env.CODEX_THREAD_ID = "thread-x";
  process.env.AIFY_CODEX_APP_SERVER_URL = "ws://127.0.0.1:1234";
  const a = new CodexAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.CODEX_THREAD_ID, "thread-x");
  assert.strictEqual(env.AIFY_CODEX_APP_SERVER_URL, "ws://127.0.0.1:1234");
  delete process.env.CODEX_THREAD_ID;
  delete process.env.AIFY_CODEX_APP_SERVER_URL;
});

test("CodexAdapter diagnosticEnv reports (unset) when app-server missing", () => {
  delete process.env.CODEX_THREAD_ID;
  delete process.env.AIFY_CODEX_APP_SERVER_URL;
  const a = new CodexAdapter();
  const env = a.diagnosticEnv();
  assert.strictEqual(env.AIFY_CODEX_APP_SERVER_URL, "(unset)");
});
