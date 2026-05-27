#!/usr/bin/env node
import assert from "node:assert/strict";
import path from "node:path";
import { terminalChildEnv } from "../terminal-env.js";

const codexHome = path.join("C:", "Users", "Admin", ".local", "state", "aify-comms", "managed-codex-home");
const env = terminalChildEnv({
  baseEnv: {
    AIFY_SERVER_URL: "http://localhost:8800",
    AIFY_ENVIRONMENT_BRIDGE: "1",
    AIFY_MANAGED_DISPATCH: "1",
    CODEX_HOME: "C:/Users/Admin/.codex",
    CLAUDE_SESSION_ID: "old-claude",
  },
  runtime: "codex",
  sessionHandle: "thread-123",
  workspace: "C:/repo",
  terminal: { agentId: "coder" },
  managedViaWrapper: true,
  prepareCodexHome: ({ workspace }) => {
    assert.equal(workspace, "C:/repo");
    return codexHome;
  },
});

assert.equal(env.AIFY_ENVIRONMENT_BRIDGE, "0");
assert.equal(env.AIFY_MANAGED_DISPATCH, "0");
assert.equal(env.AIFY_AGENT_ID, "coder");
assert.equal(env.AIFY_COMMS_AGENT_ID, "coder");
assert.equal(env.AIFY_RUNTIME, "codex");
assert.equal(env.AIFY_SESSION_HANDLE, "thread-123");
assert.equal(env.CODEX_THREAD_ID, "thread-123");
assert.equal(env.CODEX_HOME, codexHome);
assert.equal(env.CLAUDE_SESSION_ID, "old-claude");

const policyCodexHome = path.join("C:", "Users", "Admin", ".local", "state", "aify-comms", "managed-codex-policy-home");
const policyEnv = terminalChildEnv({
  baseEnv: {
    AIFY_SERVER_URL: "http://localhost:8800",
    CODEX_HOME: "C:/Users/Admin/.codex",
  },
  runtime: "codex",
  sessionHandle: "thread-456",
  workspace: "C:/repo",
  terminal: { agentId: "policy-coder" },
  agentInfo: {
    model: "gpt-test",
    runtimeConfig: { effort: "xhigh" },
  },
  managedViaWrapper: true,
  prepareCodexHome: ({ workspace, model, effort }) => {
    assert.equal(workspace, "C:/repo");
    assert.equal(model, "gpt-test");
    assert.equal(effort, "xhigh");
    return policyCodexHome;
  },
});

assert.equal(policyEnv.CODEX_HOME, policyCodexHome);
assert.equal(policyEnv.AIFY_MANAGED_MODEL, "gpt-test");
assert.equal(policyEnv.AIFY_MANAGED_EFFORT, "xhigh");

const claudeEnv = terminalChildEnv({
  baseEnv: { AIFY_ENVIRONMENT_BRIDGE: "1", AIFY_MANAGED_DISPATCH: "1" },
  runtime: "claude-code",
  sessionHandle: "claude-session",
  workspace: "/repo",
  terminal: { agentId: "sc-manager" },
  agentInfo: { model: "opus", runtimeConfig: { effort: "medium" } },
  managedViaWrapper: true,
});

assert.equal(claudeEnv.AIFY_ENVIRONMENT_BRIDGE, "0");
assert.equal(claudeEnv.AIFY_MANAGED_DISPATCH, "0");
assert.equal(claudeEnv.CLAUDE_SESSION_ID, "claude-session");
assert.equal(claudeEnv.CODEX_HOME, undefined);
assert.equal(claudeEnv.AIFY_MANAGED_MODEL, "opus");
assert.equal(claudeEnv.AIFY_MANAGED_EFFORT, "medium");

const hermesEnv = terminalChildEnv({
  baseEnv: { HERMES_SESSION_ID: "old-hermes" },
  runtime: "hermes",
  sessionHandle: "hermes-session",
  workspace: "/repo",
  terminal: { agentId: "hermes-coder" },
  managedViaWrapper: true,
});

assert.equal(hermesEnv.AIFY_AGENT_ID, "hermes-coder");
assert.equal(hermesEnv.AIFY_RUNTIME, "hermes");
assert.equal(hermesEnv.AIFY_SESSION_HANDLE, "hermes-session");
assert.equal(hermesEnv.HERMES_SESSION_ID, "hermes-session");

const piEnv = terminalChildEnv({
  baseEnv: { PI_SESSION_ID: "old-pi", OMP_SESSION_ID: "old-omp", AIFY_PI_SESSION_ID: "old-aify-pi" },
  runtime: "pi",
  sessionHandle: "pi-session",
  workspace: "/repo",
  terminal: { agentId: "pi-coder" },
});

assert.equal(piEnv.AIFY_RUNTIME, "pi");
assert.equal(piEnv.AIFY_SESSION_HANDLE, "pi-session");
assert.equal(piEnv.PI_SESSION_ID, "pi-session");
assert.equal(piEnv.OMP_SESSION_ID, "pi-session");
assert.equal(piEnv.AIFY_PI_SESSION_ID, "pi-session");

// Spawn-context declaration: every wrapper PTY spawned by aify-comms must
// inherit AIFY_SESSION_MODE=managed so the inner mcp/stdio/server.js
// registers the agent as managed (not resident). Operator-launched
// wrappers don't have this env set and auto-detect via TTY presence.
assert.equal(env.AIFY_SESSION_MODE, "managed", "bridge-spawned wrapper env must declare AIFY_SESSION_MODE=managed");
assert.equal(piEnv.AIFY_SESSION_MODE, "managed", "bridge-spawned pi wrapper env must declare AIFY_SESSION_MODE=managed");

// Wrapper-backed PTYs declare AIFY_MANAGED_VIA_WRAPPER=1 so the inner
// bridge's dispatch-claim loop adds channel/resident claim modes. Native
// managed runtimes such as Pi must not set it.
assert.equal(env.AIFY_MANAGED_VIA_WRAPPER, "1", "bridge-spawned codex wrapper env must declare AIFY_MANAGED_VIA_WRAPPER=1");
assert.equal(claudeEnv.AIFY_MANAGED_VIA_WRAPPER, "1", "bridge-spawned claude wrapper env must declare AIFY_MANAGED_VIA_WRAPPER=1");
assert.equal(hermesEnv.AIFY_MANAGED_VIA_WRAPPER, "1", "bridge-spawned hermes wrapper env must declare AIFY_MANAGED_VIA_WRAPPER=1");
assert.equal(piEnv.AIFY_MANAGED_VIA_WRAPPER, "0", "bridge-spawned pi env must stay native managed, not wrapper-backed");

console.log("terminal-env.test.js: all assertions passed");
