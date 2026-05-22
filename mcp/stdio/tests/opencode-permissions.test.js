#!/usr/bin/env node
import assert from "node:assert/strict";

const { opencodePermissionConfig } = await import("../runtimes.js");

assert.deepEqual(
  opencodePermissionConfig({}, "managed"),
  { bash: "allow", edit: "allow", webfetch: "allow" },
  "managed OpenCode runs should default to non-interactive permissions",
);

assert.equal(
  opencodePermissionConfig({}, "resident"),
  undefined,
  "resident OpenCode sessions should keep the visible user's permission mode by default",
);

assert.deepEqual(
  opencodePermissionConfig({ approvalPolicy: "ask" }, "managed"),
  { bash: "ask", edit: "ask", webfetch: "ask" },
  "explicit ask policy should override managed default",
);

assert.deepEqual(
  opencodePermissionConfig({ permission: { bash: "deny", edit: "allow", webfetch: "ask" } }, "managed"),
  { bash: "deny", edit: "allow", webfetch: "ask" },
  "explicit permission object should win over derived policy",
);

console.log("opencode-permissions.test.js: all assertions passed");
