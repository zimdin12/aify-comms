#!/usr/bin/env node
// Regression: when the configured runtime launcher does not resolve to a real
// executable, runtimeLaunchAvailability must report an actionable diagnostic
// (not just "not on PATH") so the dashboard failure event is debuggable.

import assert from "node:assert/strict";

const originalClaudeCmd = process.env.AIFY_CLAUDE_COMMAND;

try {
  process.env.AIFY_CLAUDE_COMMAND = "this-binary-does-not-exist-aify-test-zzz";
  const mod = await import(`../runtimes.js?cacheBust=${Date.now()}`);
  const result = mod.runtimeLaunchAvailability("claude-code");
  assert.equal(result.available, false, "missing launcher should report unavailable");
  assert.match(
    result.message,
    /not launchable|could not be resolved/i,
    "message should explain the launch is blocked",
  );
  assert.match(
    result.message,
    /Diagnostic:/,
    "message should include diagnostic resolution attempts so users can see what was tried",
  );
  assert.match(
    result.message,
    /bridge PATH/,
    "diagnostic should include the bridge's PATH so users can see what the bridge actually sees",
  );

  // describeExecutableResolution should expose structured attempts for tools
  const info = mod.describeExecutableResolution("this-binary-does-not-exist-aify-test-zzz");
  assert.equal(info.resolved, null);
  assert.ok(Array.isArray(info.attempts) && info.attempts.length > 0, "should record at least one attempt");
} finally {
  if (originalClaudeCmd === undefined) delete process.env.AIFY_CLAUDE_COMMAND;
  else process.env.AIFY_CLAUDE_COMMAND = originalClaudeCmd;
}

console.log("executable-resolution.test.js: all assertions passed");
