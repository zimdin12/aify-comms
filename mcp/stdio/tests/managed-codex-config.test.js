#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const originalHome = process.env.HOME;
const originalCodexHome = process.env.CODEX_HOME;
const tempHome = fs.mkdtempSync(path.join(os.tmpdir(), "aify-managed-codex-home-"));
process.env.HOME = tempHome;
delete process.env.CODEX_HOME;

const { managedCodexConfigText, managedCodexEffort, prepareManagedCodexHome } = await import("../runtimes.js");

const text = managedCodexConfigText({
  workspace: "/mnt/c/Users/Administrator/sand_castle",
  serverUrl: "http://localhost:8800",
  model: "gpt-5.5",
  effort: "high",
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

const defaultText = managedCodexConfigText({
  workspace: "/mnt/c/Users/Administrator/sand_castle",
  serverUrl: "http://localhost:8800",
});
assert.match(defaultText, /model = "gpt-5\.5"/);
assert.match(defaultText, /model_reasoning_effort = "high"/);
assert.equal(managedCodexEffort({ effort: "max" }), "xhigh");
assert.equal(managedCodexEffort({ effort: "medium" }), "medium");

const managedHome = prepareManagedCodexHome({
  agentId: "sc/coder:one",
  workspace: "/mnt/c/Users/Administrator/sand_castle",
  serverUrl: "http://localhost:8800",
});

assert.equal(managedHome, path.join(tempHome, ".local", "state", "aify-comms", "managed-codex-home-sc_coder_one"));
assert.ok(fs.existsSync(path.join(managedHome, "config.toml")));
assert.ok(fs.existsSync(path.join(managedHome, "skills", "aify-comms", "SKILL.md")));
assert.ok(fs.existsSync(path.join(managedHome, "skills", "aify-comms-debug", "SKILL.md")));

const otherManagedHome = prepareManagedCodexHome({
  agentId: "sc-coder-two",
  workspace: "/mnt/c/Users/Administrator/sand_castle",
  serverUrl: "http://localhost:8800",
  model: "gpt-other",
  effort: "xhigh",
});

assert.equal(otherManagedHome, path.join(tempHome, ".local", "state", "aify-comms", "managed-codex-home-sc-coder-two"));
assert.notEqual(otherManagedHome, managedHome);
assert.match(fs.readFileSync(path.join(otherManagedHome, "config.toml"), "utf8"), /model = "gpt-other"/);

if (originalHome === undefined) delete process.env.HOME;
else process.env.HOME = originalHome;
if (originalCodexHome === undefined) delete process.env.CODEX_HOME;
else process.env.CODEX_HOME = originalCodexHome;
fs.rmSync(tempHome, { recursive: true, force: true });

console.log("managed-codex-config.test.js: all assertions passed");
