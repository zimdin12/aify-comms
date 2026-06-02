#!/usr/bin/env node
// fix/hermes-leak P2 (bridge side): a Dashboard STOP/REMOVE of a MANAGED HERMES
// agent must tear down the WHOLE triad (gateway host, delivery loop, daemon) —
// not just the console PTY. The bridge's terminal-control stop handler detects a
// managed-hermes stop control and runs the agent-scoped single-agent teardown
// (runManagedTeardown with ownedAgentIds:[agentId]) so the detached survivors are
// reaped. Agent-scoped throughout: never another agent's / a resident's procs.

import assert from "node:assert/strict";
import {
  stopControlTriadAgentId,
  runManagedTeardown,
  REAP_TRIAD_BODY_SENTINEL,
} from "../reap-managed-survivors.js";

// ---------------------------------------------------------------------------
// 1. stopControlTriadAgentId: a managed-hermes stop control → the agent id.
// ---------------------------------------------------------------------------
{
  // Managed hermes stop control → tear down this agent's triad.
  assert.equal(
    stopControlTriadAgentId({ action: "stop", runtime: "hermes", agentId: "sc-coder", sessionMode: "managed" }),
    "sc-coder",
    "managed hermes stop control returns the agent id",
  );
  // hermes-agent alias normalizes to hermes too.
  assert.equal(
    stopControlTriadAgentId({ action: "stop", runtime: "hermes-agent", agentId: "sc-coder", sessionMode: "managed" }),
    "sc-coder",
  );
  // REMOVE path: sessionMode is gone (agent deleted), but the body sentinel
  // carries the triad-reap intent forward → still reaped.
  assert.equal(
    stopControlTriadAgentId({
      action: "stop",
      runtime: "hermes",
      agentId: "sc-coder",
      sessionMode: "",
      body: `${REAP_TRIAD_BODY_SENTINEL} Agent stopped from dashboard.`,
    }),
    "sc-coder",
    "REMOVE body sentinel triggers the triad teardown even with no sessionMode",
  );
  // The sentinel alone never overrides the runtime guard — a non-hermes runtime
  // with the sentinel is still NOT a hermes triad teardown.
  assert.equal(
    stopControlTriadAgentId({
      action: "stop",
      runtime: "claude-code",
      agentId: "sc-claude",
      body: `${REAP_TRIAD_BODY_SENTINEL} x`,
    }),
    null,
    "sentinel never bypasses the hermes-runtime guard",
  );
}

// ---------------------------------------------------------------------------
// 2. NEVER a triad teardown for non-hermes, non-stop, resident, or no-agent.
// ---------------------------------------------------------------------------
{
  // Not a hermes runtime → no triad (claude has its own reaper).
  assert.equal(
    stopControlTriadAgentId({ action: "stop", runtime: "claude-code", agentId: "sc-claude", sessionMode: "managed" }),
    null,
    "claude stop is not a hermes triad teardown",
  );
  // Not a stop action → no triad (input/resize/start never reap).
  assert.equal(
    stopControlTriadAgentId({ action: "input", runtime: "hermes", agentId: "sc-coder", sessionMode: "managed" }),
    null,
  );
  // RESIDENT hermes → NEVER reaped (operator's own session).
  assert.equal(
    stopControlTriadAgentId({ action: "stop", runtime: "hermes", agentId: "operator", sessionMode: "resident" }),
    null,
    "resident hermes stop must NOT trigger a triad teardown",
  );
  // No agent id → nothing to scope to → no triad (fail-safe).
  assert.equal(
    stopControlTriadAgentId({ action: "stop", runtime: "hermes", agentId: "", sessionMode: "managed" }),
    null,
  );
  // Empty/missing control → null.
  assert.equal(stopControlTriadAgentId(null), null);
  assert.equal(stopControlTriadAgentId({}), null);
}

// ---------------------------------------------------------------------------
// 3. End-to-end seam: the agent id from stopControlTriadAgentId scopes
//    runManagedTeardown to EXACTLY that agent — another agent's gateway/loop is
//    never touched.
// ---------------------------------------------------------------------------
{
  const agentId = stopControlTriadAgentId({
    action: "stop",
    runtime: "hermes",
    agentId: "sc-coder",
    sessionMode: "managed",
  });
  assert.equal(agentId, "sc-coder");

  const calls = { killByPort: [], stopDaemon: [], killTree: [] };
  const procs = [
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 200, ppid: 1, commandLine: "node hermes-managed-host.js run other-agent" }, // NOT scoped
  ];
  const markers = [
    { kind: "port", agentId: "sc-coder", value: 9342 },
    { kind: "daemon-pid", agentId: "sc-coder", value: 4242 },
    { kind: "port", agentId: "other-agent", value: 9999 }, // NOT scoped
  ];

  const result = runManagedTeardown({
    ownedAgentIds: [agentId],
    cwdRoots: ["C:/Docker/aify-comms"],
    listProcesses: () => procs,
    readMarkers: () => markers,
    consolePtyPids: [],
    killByPort: (p) => { calls.killByPort.push(p); },
    stopDaemon: (o) => { calls.stopDaemon.push(o); return { stopped: true }; },
    killTree: (pid) => { calls.killTree.push(pid); return true; },
  });

  assert.deepEqual(calls.killByPort, [9342], "only sc-coder's gateway port killed");
  assert.deepEqual(calls.killTree, [100], "only sc-coder's delivery loop killed");
  assert.equal(calls.stopDaemon.length, 1);
  assert.equal(calls.stopDaemon[0].agentId, "sc-coder");
  // other-agent's port (9999) + loop (200) were NEVER touched.
  assert.ok(!calls.killByPort.includes(9999), "other agent's gateway untouched");
  assert.ok(!calls.killTree.includes(200), "other agent's loop untouched");
  assert.equal(result.killed.gatewayHosts.length, 1);
}

console.log("hermes-stop-triad-teardown.test.js: all assertions passed");
