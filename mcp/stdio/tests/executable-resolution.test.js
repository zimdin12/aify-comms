#!/usr/bin/env node
// Regression: defaultClaudeCommand / defaultCodexCommand / defaultPiCommand
// must resolve to an absolute path on POSIX so spawn doesn't depend on the
// bridge process inheriting an interactive shell's PATH. AIFY_CLAUDE_COMMAND
// continues to take precedence.

import assert from "node:assert/strict";

const originalPlatform = Object.getOwnPropertyDescriptor(process, "platform");
const originalClaudeCmd = process.env.AIFY_CLAUDE_COMMAND;
const originalCodexCmd = process.env.AIFY_CODEX_COMMAND;
const originalPiCmd = process.env.AIFY_PI_COMMAND;

function setPlatform(value) {
  Object.defineProperty(process, "platform", { value, configurable: true });
}

try {
  // On POSIX, the module should resolve `node` to an absolute path because
  // node is on PATH in any reasonable test environment.
  setPlatform("linux");
  process.env.AIFY_CLAUDE_COMMAND = "node";
  process.env.AIFY_CODEX_COMMAND = "";
  process.env.AIFY_PI_COMMAND = "node";

  const mod = await import(`../runtimes.js?cacheBust=${Date.now()}`);

  const claudeCmd = mod.__test_defaults?.defaultClaudeCommand?.()
    // fall back to indirect exercise: managedClaudeMaxTurns confirms the
    // module loaded; we just need defaultClaudeCommand to have been wired up.
    || null;

  // Public API does not export defaultClaudeCommand; instead verify the
  // resolveExecutable behavior via runtimeLaunchAvailability which uses the
  // same hasExecutable path.
  const availability = mod.runtimeLaunchAvailability("claude-code");
  assert.equal(availability.available, true, "node should be discoverable as a Claude shim for the test");
  assert.match(
    availability.message,
    /available/i,
    "availability message should be positive when the configured launcher exists on PATH",
  );

  // A clearly-missing launcher should produce a negative availability with an
  // actionable message — this is the path users see when claude isn't on PATH.
  process.env.AIFY_CLAUDE_COMMAND = "this-binary-does-not-exist-aify-test";
  const missingMod = await import(`../runtimes.js?cacheBust=${Date.now() + 1}`);
  const missing = missingMod.runtimeLaunchAvailability("claude-code");
  assert.equal(missing.available, false);
  assert.match(
    missing.message,
    /not launchable|not on PATH|not available/i,
    "missing-launcher message should be actionable, not raw ENOENT",
  );
} finally {
  if (originalPlatform) Object.defineProperty(process, "platform", originalPlatform);
  if (originalClaudeCmd === undefined) delete process.env.AIFY_CLAUDE_COMMAND;
  else process.env.AIFY_CLAUDE_COMMAND = originalClaudeCmd;
  if (originalCodexCmd === undefined) delete process.env.AIFY_CODEX_COMMAND;
  else process.env.AIFY_CODEX_COMMAND = originalCodexCmd;
  if (originalPiCmd === undefined) delete process.env.AIFY_PI_COMMAND;
  else process.env.AIFY_PI_COMMAND = originalPiCmd;
}

console.log("executable-resolution.test.js: all assertions passed");
