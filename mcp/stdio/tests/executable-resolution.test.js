#!/usr/bin/env node
// Regression: when the configured runtime launcher does not resolve to a real
// executable, runtimeLaunchAvailability must report an actionable diagnostic
// (not just "not on PATH") so the dashboard failure event is debuggable.

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const originalClaudeCmd = process.env.AIFY_CLAUDE_COMMAND;
const originalPath = process.env.PATH;

try {
  delete process.env.AIFY_CLAUDE_COMMAND;
  process.env.PATH = "";
  let mod = await import(`../runtimes.js?cacheBust=${Date.now()}-default-wrapper`);
  let result = mod.runtimeLaunchAvailability("claude-code");
  assert.equal(result.available, false, "missing default Claude wrapper should report unavailable");
  assert.match(
    result.message,
    /claude-aify/,
    "Claude terminal capability should require the claude-aify wrapper, not raw claude",
  );

  process.env.AIFY_CLAUDE_COMMAND = "this-binary-does-not-exist-aify-test-zzz";
  process.env.PATH = originalPath || "";
  mod = await import(`../runtimes.js?cacheBust=${Date.now()}-configured-missing`);
  result = mod.runtimeLaunchAvailability("claude-code");
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

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aify-claude-wrapper-"));
  const staleWrapper = path.join(tmp, "claude-aify");
  fs.writeFileSync(
    staleWrapper,
    "#!/bin/sh\nclaude --dangerously-load-development-channels --channels server:aify-comms-channel \"$@\"\n",
  );
  fs.chmodSync(staleWrapper, 0o755);
  process.env.AIFY_CLAUDE_COMMAND = staleWrapper;
  mod = await import(`../runtimes.js?cacheBust=${Date.now()}-stale-wrapper`);
  result = mod.runtimeLaunchAvailability("claude-code");
  assert.equal(result.available, false, "stale Claude wrapper should not advertise terminal support");
  assert.match(
    result.message,
    /stale Claude channel flag ordering|rerun install\.sh/i,
    "stale wrapper diagnostic should tell the operator to reinstall the wrapper",
  );

  // describeExecutableResolution should expose structured attempts for tools
  const info = mod.describeExecutableResolution("this-binary-does-not-exist-aify-test-zzz");
  assert.equal(info.resolved, null);
  assert.ok(Array.isArray(info.attempts) && info.attempts.length > 0, "should record at least one attempt");

  const controller = mod.launchRuntimeRun({
    agentId: "claude-worker",
    agentInfo: {
      agentId: "claude-worker",
      role: "coder",
      runtime: "claude-code",
      sessionMode: "managed",
      cwd: process.cwd(),
    },
    run: {
      from: "dashboard",
      subject: "missing launcher smoke",
      body: "hello",
      executionMode: "managed",
    },
    runtimeState: {},
    callbacks: {
      onEvent: () => {},
      onRuntimeState: () => {},
      onRefs: () => {},
    },
  });
  assert.equal(typeof controller.promise?.then, "function", "launchRuntimeRun should return a controller even when startup fails");
  await assert.rejects(
    controller.promise,
    /no longer uses claude -p|claude-aify/i,
    "managed Claude native dispatch should stay disabled and direct operators to the claude-aify channel path",
  );
} finally {
  if (originalClaudeCmd === undefined) delete process.env.AIFY_CLAUDE_COMMAND;
  else process.env.AIFY_CLAUDE_COMMAND = originalClaudeCmd;
  if (originalPath === undefined) delete process.env.PATH;
  else process.env.PATH = originalPath;
}

console.log("executable-resolution.test.js: all assertions passed");
