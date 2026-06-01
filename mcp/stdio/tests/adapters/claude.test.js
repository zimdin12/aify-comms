import assert from "assert";
import test from "node:test";
import os from "node:os";
import path from "node:path";
import { promises as fs } from "node:fs";
import { ClaudeAdapter } from "../../adapters/claude.js";

// claude project-dir encoding mirrors adapters/claude.js encodeClaudeCwd.
function encodeClaudeCwd(cwd) {
  return String(cwd || "").replace(/[^a-zA-Z0-9]/g, "-");
}

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

// Shared-cwd attribution fix (2026-06-01): when this agent's OWN session id
// cannot be resolved, transcriptStat/transcriptMtimeMs must return the
// "unknown / not active" sentinel (null / 0) instead of falling back to the
// NEWEST .jsonl in the shared project dir — otherwise a teammate streaming in
// the same cwd makes THIS idle agent look busy → dashboard wrongly "working".
test("ClaudeAdapter.transcriptStat returns sentinel when sid unresolved (no newest-in-dir fallback)", async () => {
  const a = new ClaudeAdapter();
  // Isolated home + cwd so the encoded project dir is unique to this test.
  const home = await fs.mkdtemp(path.join(os.tmpdir(), "aify-claude-home-"));
  const storeDir = await fs.mkdtemp(path.join(os.tmpdir(), "aify-claude-store-"));
  const cwd = "C:/some/shared/workdir-" + Date.now();
  const projDir = path.join(home, ".claude", "projects", encodeClaudeCwd(cwd));
  await fs.mkdir(projDir, { recursive: true });
  // A teammate's transcript sits in the shared project dir (the "newest").
  await fs.writeFile(
    path.join(projDir, "11111111-2222-3333-4444-555555555555.jsonl"),
    '{"type":"assistant"}\n',
  );
  // Unresolvable sid: agentId has no capture file in storeDir, and the env
  // carries no CLAUDE_SESSION_ID / AIFY_AGENT_ID.
  const opts = {
    env: {},
    homeDir: home,
    cwd,
    agentId: "agent-with-no-captured-session",
    dir: storeDir,
  };
  const stat = await a.transcriptStat(opts);
  assert.strictEqual(stat, null, "unresolved sid must NOT fall back to the newest .jsonl");
  const mtime = await a.transcriptMtimeMs(opts);
  assert.strictEqual(mtime, 0, "unresolved sid → mtime sentinel 0 (not the teammate's mtime)");
});
