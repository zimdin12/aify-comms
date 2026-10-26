import assert from "assert";
import test from "node:test";
import { ClaudeAdapter } from "../../adapters/claude.js";

test("ClaudeAdapter identity", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(a.name, "claude-code");
  assert.strictEqual(a.displayName, "Claude Code");
  assert.deepStrictEqual(a.sessionEnvVars, ["CLAUDE_SESSION_ID"]);
});

test("ClaudeAdapter reads CLAUDE_SESSION_ID", () => {
  process.env.CLAUDE_SESSION_ID = "claude-abc-123";
  const a = new ClaudeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "claude-abc-123");
  delete process.env.CLAUDE_SESSION_ID;
});

test("ClaudeAdapter resumeArgs for real handle", () => {
  const a = new ClaudeAdapter();
  assert.deepStrictEqual(a.resumeArgs("claude-abc-123"), ["--resume", "claude-abc-123"]);
});

test("ClaudeAdapter diagnosticEnv exposes CLAUDE_SESSION_ID", () => {
  process.env.CLAUDE_SESSION_ID = "active-id";
  const a = new ClaudeAdapter();
  assert.deepStrictEqual(a.diagnosticEnv(), { CLAUDE_SESSION_ID: "active-id" });
  delete process.env.CLAUDE_SESSION_ID;
});

test("ClaudeAdapter Plan 2 capabilities", () => {
  const a = new ClaudeAdapter();
  assert.strictEqual(a.supportsResident, true);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, true);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});

test("ClaudeAdapter overrides discoverSessionId", () => {
  const a = new ClaudeAdapter();
  const own = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(a), "discoverSessionId");
  assert.ok(own && typeof own.value === "function",
    "ClaudeAdapter must define its own discoverSessionId override");
});

test("ClaudeAdapter.discoverSessionId returns null or non-empty string", async () => {
  const a = new ClaudeAdapter();
  const result = await a.discoverSessionId();
  if (result !== null) {
    assert.ok(typeof result === "string" && result.length > 0);
  }
});
