#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { tmpDir } from "./_tmpdir.js";

const originalHome = process.env.HOME;
const originalCodexHome = process.env.CODEX_HOME;
const tempHome = tmpDir("aify-managed-codex-home-");
process.env.HOME = tempHome;
delete process.env.CODEX_HOME;

const { managedCodexConfigText, managedCodexEffort, prepareManagedCodexHome } = await import("../runtimes.js");

const text = managedCodexConfigText({
  workspace: "/mnt/c/Users/dev/sand_castle",
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
// Plan 6 follow-up (2026-05-26): AIFY_MANAGED_DISPATCH is NO LONGER hard-set
// to "1" — the wrapper PTY's terminal-env.js sets it to "0" for wrapper-backed
// managed codex (the inner MCP MUST register + claim channel runs), and the
// legacy native-managed-codex path doesn't use this config file at all
// (createCodexController talks to the codex app-server directly).
assert.doesNotMatch(text, /AIFY_MANAGED_DISPATCH = "1"/);
// Plan 6 follow-up (2026-05-26): the managed-codex config must include
// `env_vars` so the inner aify-comms MCP child inherits AIFY_AGENT_ID
// and the other wrapper-spawn vars from the parent codex process.
// Without this, codex's per-child env REPLACES the inherited environment
// and the inner MCP registers without an agent id — managed-via-wrapper
// dispatch sits queued forever.
assert.match(text, /env_vars = \[[^\]]*"AIFY_AGENT_ID"[^\]]*\]/);
assert.match(text, /env_vars = \[[^\]]*"AIFY_MANAGED_VIA_WRAPPER"[^\]]*\]/);
assert.match(text, /env_vars = \[[^\]]*"AIFY_SESSION_MODE"[^\]]*\]/);
assert.match(text, /\[projects\."\/mnt\/c\/Users\/dev\/sand_castle"\]/);
assert.doesNotMatch(text, /openmemory/);
assert.doesNotMatch(text, /host\.docker\.internal/);
assert.doesNotMatch(text, /8765/);

const defaultText = managedCodexConfigText({
  workspace: "/mnt/c/Users/dev/sand_castle",
  serverUrl: "http://localhost:8800",
});
assert.doesNotMatch(defaultText, /^model = /m);
assert.match(defaultText, /model_reasoning_effort = "high"/);
assert.equal(managedCodexEffort({ effort: "medium" }), "medium");

const managedHome = prepareManagedCodexHome({
  workspace: "/mnt/c/Users/dev/sand_castle",
  serverUrl: "http://localhost:8800",
});

assert.equal(managedHome, path.join(tempHome, ".local", "state", "aify-comms", "managed-codex-home"));
assert.ok(fs.existsSync(path.join(managedHome, "config.toml")));
assert.ok(fs.existsSync(path.join(managedHome, "skills", "aify-comms", "SKILL.md")));
assert.ok(fs.existsSync(path.join(managedHome, "skills", "aify-comms-debug", "SKILL.md")));

if (originalHome === undefined) delete process.env.HOME;
else process.env.HOME = originalHome;
if (originalCodexHome === undefined) delete process.env.CODEX_HOME;
else process.env.CODEX_HOME = originalCodexHome;
fs.rmSync(tempHome, { recursive: true, force: true });

console.log("managed-codex-config.test.js: all assertions passed");
