#!/usr/bin/env node
import assert from "node:assert/strict";

const { buildManagedClaudeUnlockPowerShell, isClaudeSessionInUseError } = await import("../runtimes.js");

assert.equal(
  isClaudeSessionInUseError("Error: Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use."),
  true,
);
assert.equal(isClaudeSessionInUseError("Session ID is already in use."), true);
assert.equal(isClaudeSessionInUseError("Claude exited with code 1"), false);
assert.equal(isClaudeSessionInUseError("session lock unavailable"), false);

const script = buildManagedClaudeUnlockPowerShell("e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6");
assert.match(script, /Get-CimInstance Win32_Process/);
assert.match(script, /taskkill/);
assert.match(script, /--session-id/);
assert.match(script, /-p\|--print/);
assert.match(script, /e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6/);

console.log("claude-session-in-use.test.js: all assertions passed");
