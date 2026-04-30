#!/usr/bin/env node
import assert from "node:assert/strict";

const { managedCodexConfigText } = await import("../runtimes.js");

const text = managedCodexConfigText({
  workspace: "/mnt/c/Users/Administrator/sand_castle",
  serverUrl: "http://localhost:8800",
  model: "gpt-5.4",
  effort: "medium",
});

assert.match(text, /\[mcp_servers\.aify-comms\]/);
assert.match(text, /enabled = true/);
assert.match(text, /startup_timeout_sec = 10/);
assert.match(text, /tool_timeout_sec = 25/);
assert.match(text, /disabled_tools = \["comms_listen"\]/);
assert.match(text, /AIFY_SERVER_URL = "http:\/\/localhost:8800"/);
assert.match(text, /AIFY_MANAGED_DISPATCH = "1"/);
assert.match(text, /\[projects\."\/mnt\/c\/Users\/Administrator\/sand_castle"\]/);
assert.doesNotMatch(text, /openmemory/);
assert.doesNotMatch(text, /host\.docker\.internal/);
assert.doesNotMatch(text, /8765/);

console.log("managed-codex-config.test.js: all assertions passed");
