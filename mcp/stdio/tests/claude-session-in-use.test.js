#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const {
  buildManagedClaudeUnlockPowerShell,
  claudeSessionTranscriptExists,
  claudeSessionTranscriptPath,
  isClaudeSessionInUseError,
} = await import("../runtimes.js");

assert.equal(
  isClaudeSessionInUseError("Error: Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use."),
  true,
);
assert.equal(isClaudeSessionInUseError("Session ID is already in use."), true);
assert.equal(isClaudeSessionInUseError("Claude exited with code 1"), false);
assert.equal(isClaudeSessionInUseError("session lock unavailable"), false);

const script = buildManagedClaudeUnlockPowerShell("e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6", [1234, 5678]);
assert.match(script, /Get-CimInstance Win32_Process/);
assert.match(script, /taskkill/);
assert.match(script, /--session-id/);
assert.match(script, /-p\|--print/);
assert.match(script, /--resume/);
assert.match(script, /claude-aify/);
assert.match(script, /e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6/);
assert.match(script, /\$markerPids = @\(1234,5678\)/);
assert.match(script, /Test-AifyHeadlessClaude/);
assert.match(script, /ParentProcessId/);
assert.match(script, /marker/);

const sessionId = "11111111-2222-4333-8444-555555555555";
const originalHomeForTranscript = process.env.HOME;
const cleanHome = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-session-clean-"));
process.env.HOME = cleanHome;
const cwd = "C:\\Users\\dev\\sand_castle";
const transcriptPath = claudeSessionTranscriptPath(sessionId, cwd);
assert.match(transcriptPath.replace(/\\/g, "/"), /\.claude\/projects\/C--Users-dev-sand-castle\/11111111-2222-4333-8444-555555555555\.jsonl$/);
assert.equal(claudeSessionTranscriptExists(sessionId, cwd), false);
if (originalHomeForTranscript === undefined) delete process.env.HOME;
else process.env.HOME = originalHomeForTranscript;
fs.rmSync(cleanHome, { recursive: true, force: true });

const tempHome = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-session-"));
const originalHome = process.env.HOME;
process.env.HOME = tempHome;
try {
  const testTranscript = claudeSessionTranscriptPath(sessionId, cwd);
  fs.mkdirSync(path.dirname(testTranscript), { recursive: true });
  fs.writeFileSync(testTranscript, "{}\n");
  assert.equal(claudeSessionTranscriptExists(sessionId, cwd), true);
} finally {
  if (originalHome === undefined) delete process.env.HOME;
  else process.env.HOME = originalHome;
  fs.rmSync(tempHome, { recursive: true, force: true });
}

console.log("claude-session-in-use.test.js: all assertions passed");
