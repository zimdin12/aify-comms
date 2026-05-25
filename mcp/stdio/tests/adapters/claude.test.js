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
