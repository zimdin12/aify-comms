#!/usr/bin/env node
import assert from "node:assert/strict";

const { managedClaudePermissionArgs } = await import("../runtimes.js");

assert.deepEqual(
  managedClaudePermissionArgs({}, "managed"),
  ["--dangerously-skip-permissions"],
  "managed Claude runs should be non-interactive by default",
);

assert.deepEqual(
  managedClaudePermissionArgs({}, "resident"),
  [],
  "resident Claude sessions should keep the visible user's permission mode by default",
);

assert.deepEqual(
  managedClaudePermissionArgs({ approvalPolicy: "never" }, "resident"),
  ["--dangerously-skip-permissions"],
  "explicit non-interactive policy should work for resident fallback runs",
);

assert.deepEqual(
  managedClaudePermissionArgs({ skipPermissions: false }, "managed"),
  [],
  "operators can opt out when debugging permission prompts",
);

console.log("managed-claude-permissions.test.js: all assertions passed");
