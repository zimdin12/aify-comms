#!/usr/bin/env node
import assert from "node:assert/strict";
import path from "node:path";
import { terminalChildEnv } from "../terminal-env.js";
import { NEVER_INHERITED } from "../child-env-hygiene.mjs";

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


// ── AIFY_AGENT_ROLE: a spawned tester came up as a coder ─────────────────────────────
//
// The spawn request carries a role. This env builder never passed it, so the inner
// mcp/stdio/server.js child read `process.env.AIFY_AGENT_ROLE` — absent — and fell back to
// "coder" (server.js:238). Its self-register then sent that role, and because re-register is a
// full state refresh, the spawn's real role was overwritten. Spawn a `tester` and get a `coder`,
// with nothing anywhere reporting a problem.
//
// SECOND bug in the same line, and the worse of the two: `...baseEnv` spreads the ENVIRONMENT
// BRIDGE's own environment. If that process has AIFY_AGENT_ROLE set, every managed worker it
// launches INHERITS it. So the role was not merely missing, it was inheritable from an unrelated
// process — exactly why AIFY_AGENT_ID is set explicitly rather than left to inheritance.
{
  const withRole = terminalChildEnv({
    baseEnv: { AIFY_AGENT_ROLE: "manager" },   // the env bridge's own role, which must not leak
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: { role: "tester" },
  });
  assert.equal(withRole.AIFY_AGENT_ROLE, "tester", "the spawn's role must reach the worker");

  const unknownRole = terminalChildEnv({
    baseEnv: { AIFY_AGENT_ROLE: "manager" },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: {},
  });
  assert.equal(
    unknownRole.AIFY_AGENT_ROLE, "",
    "with no known role the variable must be CLEARED, not inherited from the environment bridge — "
    + "an empty value makes the child fall back to its own default, a leaked one makes it lie",
  );

  const fromTerminal = terminalChildEnv({
    baseEnv: {},
    runtime: "hermes",
    terminal: { agentId: "sc-tester", role: "reviewer" },
    agentInfo: {},
  });
  assert.equal(fromTerminal.AIFY_AGENT_ROLE, "reviewer", "the terminal row is a valid role source too");

  const whitespace = terminalChildEnv({
    baseEnv: {},
    runtime: "hermes",
    terminal: { agentId: "x" },
    agentInfo: { role: "  tester  " },
  });
  assert.equal(whitespace.AIFY_AGENT_ROLE, "tester");
}

// ── the bridge's own ancestry reaches nothing it spawns ───────────────────────
//
// AT THE CALL SITE, not on the helper. child-env-hygiene.test.js proves withoutInheritedMarkers
// strips the list; that is a different claim from terminalChildEnv actually calling it, and the
// difference is not academic -- an interrupt-attribution feature shipped this same week that was
// fully unit-tested and queried a table nothing writes, so it could never once have fired.
//
// DERIVED from NEVER_INHERITED rather than listing the names again. A hand-written list here would
// go stale the moment a third marker is added, and it would go stale SILENTLY -- passing, while
// covering less than it appears to.
{
  const hostile = Object.fromEntries(
    Object.keys(NEVER_INHERITED).map((name) => [name, "leaked-from-the-bridge"]),
  );
  const env = terminalChildEnv({
    baseEnv: { ...hostile, PATH: "/usr/bin", HOME: "/home/dev" },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: { role: "tester" },
  });

  for (const name of Object.keys(NEVER_INHERITED)) {
    assert.notEqual(
      env[name], "leaked-from-the-bridge",
      `${name} reached the worker from the bridge's own environment`,
    );
  }

  // The values this function OWNS are still set. Stripping must not turn into losing.
  assert.equal(env.AIFY_AGENT_ID, "sc-tester", "the worker lost its own identity");
  assert.equal(env.AIFY_AGENT_ROLE, "tester", "the worker lost its own role");

  // And an ordinary variable is untouched: a child needs most of what it inherits.
  assert.equal(env.PATH, "/usr/bin");
  assert.equal(env.HOME, "/home/dev");
}

{
  // REMOVED, not blanked. An empty string is a value, and a runtime asking "is this set?" reads
  // it as yes -- which is how a half-cleared marker keeps the original bug while looking fixed.
  const env = terminalChildEnv({
    baseEnv: { CLAUDE_CODE_CHILD_SESSION: "1" },
    runtime: "hermes",
    terminal: { agentId: "sc-tester" },
    agentInfo: {},
  });
  assert.ok(
    !("CLAUDE_CODE_CHILD_SESSION" in env),
    "the transcript-loss marker is still present, as an empty value",
  );
}

console.log("terminal-env.test.js: all assertions passed");
