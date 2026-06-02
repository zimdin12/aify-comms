#!/usr/bin/env node
import assert from "node:assert/strict";
import { runManagedTeardown } from "../reap-managed-survivors.js";

// runManagedTeardown is the seam server.js calls inside shutdownWithStatus
// (after TERMINAL_MANAGER.stopAll) and on the supersede path, ONLY when this is
// an environment bridge. It enumerates the managed-hermes triad survivors for
// the bridge's owned agents and reaps them with the injected primitives.

// ---------------------------------------------------------------------------
// 1. A shutdown teardown on an env bridge enumerates + reaps the owned agents'
//    triad (gateway host by port, delivery loop by pid, daemon, console PTY).
// ---------------------------------------------------------------------------
{
  const calls = { killByPort: [], stopDaemon: [], killTree: [], killByPid: [] };
  const procs = [
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 999, ppid: 1, commandLine: "hermes --tui --aify-agent operator-resident" }, // resident, NOT owned
  ];
  const markers = [
    { kind: "port", agentId: "sc-coder", value: 9342 },
    { kind: "daemon-pid", agentId: "sc-coder", value: 4242 },
    { kind: "port", agentId: "other-env-agent", value: 9341 }, // not owned
  ];

  const result = runManagedTeardown({
    ownedAgentIds: ["sc-coder"],
    cwdRoots: ["C:/Docker/aify-comms"],
    listProcesses: () => procs,
    readMarkers: () => markers,
    consolePtyPids: [{ agentId: "sc-coder", pid: 7777 }],
    killByPort: (p) => { calls.killByPort.push(p); return { killed: true }; },
    stopDaemon: (o) => { calls.stopDaemon.push(o); return { stopped: true }; },
    killTree: (pid) => { calls.killTree.push(pid); return true; },
    killByPid: (pid) => { calls.killByPid.push(pid); return { killed: true }; },
  });

  assert.deepEqual(calls.killByPort, [9342], "owned gateway host killed by port");
  assert.deepEqual(calls.killTree, [100], "owned delivery loop killed by tree");
  assert.equal(calls.stopDaemon.length, 1);
  assert.equal(calls.stopDaemon[0].agentId, "sc-coder");
  assert.deepEqual(calls.killByPid, [7777], "owned console PTY killed by pid");

  // The resident operator session (999) and the other-env gateway (9341) are
  // NEVER touched.
  assert.ok(!calls.killTree.includes(999), "resident operator session never killed");
  assert.ok(!calls.killByPort.includes(9341), "other-env gateway never killed");

  assert.ok(result && result.killed && result.killed.gatewayHosts.length === 1);
}

// ---------------------------------------------------------------------------
// 2. FAIL-SAFE: no owned agents → teardown is a total no-op (kills nothing).
//    This is the guard that prevents a non-env bridge / unknown-scope shutdown
//    from sweeping the host.
// ---------------------------------------------------------------------------
{
  const calls = [];
  const procs = [{ pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" }];
  const markers = [{ kind: "port", agentId: "sc-coder", value: 9342 }];
  const result = runManagedTeardown({
    ownedAgentIds: [],
    listProcesses: () => procs,
    readMarkers: () => markers,
    consolePtyPids: [{ agentId: "sc-coder", pid: 7777 }],
    killByPort: (p) => calls.push(["port", p]),
    stopDaemon: (o) => calls.push(["daemon", o]),
    killTree: (pid) => calls.push(["tree", pid]),
    killByPid: (pid) => calls.push(["pid", pid]),
  });
  assert.deepEqual(calls, [], "no owned agents → kills nothing");
  assert.equal(result.killed.gatewayHosts.length, 0);
  assert.equal(result.killed.deliveryLoops.length, 0);
}

console.log("server-shutdown-teardown.test.js: all assertions passed");
