#!/usr/bin/env node
import assert from "node:assert/strict";

const { managedClaudeModel, managedClaudePermissionArgs } = await import("../runtimes.js");

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

assert.equal(
  managedClaudeModel({}, {}),
  "opus",
  "managed Claude should default to the latest capable Claude Code alias",
);

assert.equal(
  managedClaudeModel({ model: "sonnet" }, { model: "opus" }),
  "sonnet",
  "per-agent Claude model override should win over runtime config",
);

assert.equal(
  managedClaudeModel({}, { model: "claude-opus-4-1-20250805" }),
  "claude-opus-4-1-20250805",
  "runtime config Claude model should be usable when no agent override exists",
);

console.log("managed-claude-permissions.test.js: all assertions passed");
