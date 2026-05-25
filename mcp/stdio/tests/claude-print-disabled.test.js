#!/usr/bin/env node
import assert from "node:assert/strict";

const { launchRuntimeRun } = await import("../runtimes.js");

const controller = launchRuntimeRun({
  agentId: "claude-managed",
  agentInfo: {
    agentId: "claude-managed",
    runtime: "claude-code",
    sessionMode: "managed",
    cwd: process.cwd(),
    capabilities: ["managed-run"],
    runtimeConfig: {},
  },
  run: {
    id: "run-test",
    executionMode: "managed",
    subject: "work",
    body: "do work",
  },
  runtimeState: {},
  callbacks: {},
});

await assert.rejects(
  controller.promise,
  /Claude Code managed Messenger no longer uses claude -p/,
);

// Capabilities now derive from ClaudeAdapter (Plan 2 capability matrix):
// supportsInterrupt=true, supportsSteering=true. The controller's start()
// still rejects the dispatch promise (claude -p delivery is disabled in
// favor of claude-aify wrapper + channel bridge), but the adapter-reported
// surface capabilities are unchanged.
assert.equal(controller.capabilities.interrupt, true);
assert.equal(controller.capabilities.steer, true);

console.log("claude-print-disabled.test.js: all assertions passed");
