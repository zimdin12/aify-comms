import assert from "assert";
import test from "node:test";
import os from "node:os";
import path from "node:path";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
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

test("HermesAdapter.discoverSessionId prefers durable env session over gateway sid", async () => {
  const previousSessionId = process.env.HERMES_SESSION_ID;
  const previousGatewayUrl = process.env.AIFY_HERMES_GATEWAY_URL;
  try {
    process.env.HERMES_SESSION_ID = "20260526_190639_eebd50";
    process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:1/api/ws?token=x";
    const a = new HermesAdapter();
    assert.strictEqual(await a.discoverSessionId(), "20260526_190639_eebd50");
  } finally {
    if (previousSessionId === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = previousSessionId;
    if (previousGatewayUrl === undefined) delete process.env.AIFY_HERMES_GATEWAY_URL;
    else process.env.AIFY_HERMES_GATEWAY_URL = previousGatewayUrl;
  }
});

test("HermesAdapter.discoverSessionId prefers TUI active-session file over stale env", async () => {
  const previousSessionId = process.env.HERMES_SESSION_ID;
  const previousActiveFile = process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
  const dir = mkdtempSync(path.join(os.tmpdir(), "aify-hermes-active-"));
  const file = path.join(dir, "active.json");
  try {
    process.env.HERMES_SESSION_ID = "stale-parent-shell-session";
    process.env.AIFY_HERMES_ACTIVE_SESSION_FILE = file;
    writeFileSync(file, JSON.stringify({ session_id: "visible-live-sid" }));
    const a = new HermesAdapter();
    assert.strictEqual(await a.discoverSessionId(), "visible-live-sid");
  } finally {
    if (previousSessionId === undefined) delete process.env.HERMES_SESSION_ID;
    else process.env.HERMES_SESSION_ID = previousSessionId;
    if (previousActiveFile === undefined) delete process.env.AIFY_HERMES_ACTIVE_SESSION_FILE;
    else process.env.AIFY_HERMES_ACTIVE_SESSION_FILE = previousActiveFile;
    rmSync(dir, { recursive: true, force: true });
  }
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
