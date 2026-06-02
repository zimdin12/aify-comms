#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  enumerateManagedSurvivors,
  reapManagedSurvivors,
  cmdlineDeliveryLoopAgent,
  cmdlineResidentAgent,
} from "../reap-managed-survivors.js";

// ---------------------------------------------------------------------------
// cmdlineDeliveryLoopAgent: extract the agent id from a delivery-loop cmdline.
// ---------------------------------------------------------------------------
{
  assert.equal(
    cmdlineDeliveryLoopAgent("node /x/mcp/stdio/hermes-managed-host.js run sc-coder"),
    "sc-coder",
  );
  assert.equal(
    cmdlineDeliveryLoopAgent("node hermes-managed-host.js ensure-host sc-coder"),
    null,
    "ensure-host is not a delivery loop",
  );
  assert.equal(cmdlineDeliveryLoopAgent("node server.js"), null);
  assert.equal(cmdlineDeliveryLoopAgent(""), null);
}

// ---------------------------------------------------------------------------
// cmdlineResidentAgent: a resident operator session carries --aify-agent <x>
// and is NOT a managed delivery loop / managed wrapper.
// ---------------------------------------------------------------------------
{
  assert.equal(
    cmdlineResidentAgent("hermes --tui --aify-agent comms-tech-lead"),
    "comms-tech-lead",
  );
  assert.equal(
    cmdlineResidentAgent("claude-aify --aify-agent=sc-coder --auto"),
    "sc-coder",
  );
  assert.equal(cmdlineResidentAgent("node hermes-managed-host.js run sc-coder"), null);
}

// ---------------------------------------------------------------------------
// 1. enumerate excludes resident sessions and other-env agents (plan §2.1).
// ---------------------------------------------------------------------------
{
  const procs = [
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 200, ppid: 1, commandLine: "hermes --tui --resume aify-some-resident --aify-agent comms-tech-lead" },
    { pid: 300, ppid: 1, commandLine: "node hermes-managed-host.js run other-team-agent" },
  ];
  const found = enumerateManagedSurvivors({
    ownedAgentIds: ["sc-coder"],
    listProcesses: () => procs,
    readMarkers: () => [],
  });
  assert.deepEqual(found.deliveryLoops.map((p) => p.pid), [100]);
  // The resident comms-tech-lead session (200) is NOT in any kind.
  assert.ok(!found.deliveryLoops.some((p) => p.pid === 200));
  // other-team-agent (300) is not owned → excluded.
  assert.ok(!found.deliveryLoops.some((p) => p.pid === 300));
}

// ---------------------------------------------------------------------------
// 2. enumerate from markers: gateway hosts (port) + daemons (pid), owned only.
// ---------------------------------------------------------------------------
{
  const markers = [
    { kind: "port", agentId: "sc-coder", value: 9342 },
    { kind: "port", agentId: "other-team-agent", value: 9341 }, // not owned
    { kind: "daemon-pid", agentId: "sc-coder", value: 4242 },
    { kind: "daemon-pid", agentId: "stranger", value: 4343 }, // not owned
  ];
  const found = enumerateManagedSurvivors({
    ownedAgentIds: ["sc-coder"],
    listProcesses: () => [],
    readMarkers: () => markers,
  });
  assert.deepEqual(found.gatewayHosts.map((g) => g.port), [9342]);
  assert.deepEqual(found.gatewayHosts.map((g) => g.agentId), ["sc-coder"]);
  assert.deepEqual(found.daemons.map((d) => d.pid), [4242]);
  assert.deepEqual(found.daemons.map((d) => d.agentId), ["sc-coder"]);
}

// ---------------------------------------------------------------------------
// 3. console PTYs come from the injected terminal_sessions.process_id list,
//    already scoped by the caller to owned/in-root agents.
// ---------------------------------------------------------------------------
{
  const found = enumerateManagedSurvivors({
    ownedAgentIds: ["sc-coder"],
    listProcesses: () => [],
    readMarkers: () => [],
    consolePtyPids: [{ agentId: "sc-coder", pid: 7777 }, { agentId: "stranger", pid: 8888 }],
  });
  assert.deepEqual(found.consolePtys.map((c) => c.pid), [7777]);
}

// ---------------------------------------------------------------------------
// 4. SAFETY: a delivery-loop-looking cmdline that ALSO carries a resident
//    --aify-agent for an agent NOT owned is excluded (never kill a resident).
// ---------------------------------------------------------------------------
{
  const procs = [
    // A managed loop for sc-coder (owned) — keep.
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    // A resident operator session that merely contains the word run — drop.
    { pid: 500, ppid: 1, commandLine: "hermes --tui --aify-agent operator-resident" },
  ];
  const found = enumerateManagedSurvivors({
    ownedAgentIds: ["sc-coder"],
    listProcesses: () => procs,
    readMarkers: () => [],
  });
  assert.deepEqual(found.deliveryLoops.map((p) => p.pid), [100]);
}

// ---------------------------------------------------------------------------
// 5. empty / missing ownedAgentIds → fail-safe, enumerate NOTHING.
// ---------------------------------------------------------------------------
{
  const procs = [{ pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" }];
  const markers = [{ kind: "port", agentId: "sc-coder", value: 9342 }];
  const found = enumerateManagedSurvivors({
    ownedAgentIds: [],
    listProcesses: () => procs,
    readMarkers: () => markers,
    consolePtyPids: [{ agentId: "sc-coder", pid: 7777 }],
  });
  assert.deepEqual(found.deliveryLoops, []);
  assert.deepEqual(found.gatewayHosts, []);
  assert.deepEqual(found.daemons, []);
  assert.deepEqual(found.consolePtys, []);
}

// ---------------------------------------------------------------------------
// 6. reapManagedSurvivors delegates to the right kill primitive per kind and
//    logs what it kills (no silent drops).
// ---------------------------------------------------------------------------
{
  const calls = { killByPort: [], stopDaemon: [], killTree: [], killByPid: [] };
  const found = {
    gatewayHosts: [{ agentId: "sc-coder", port: 9342 }],
    deliveryLoops: [{ agentId: "sc-coder", pid: 100 }],
    daemons: [{ agentId: "sc-coder", pid: 4242, port: 9342 }],
    consolePtys: [{ agentId: "sc-coder", pid: 7777 }],
  };
  const result = reapManagedSurvivors(found, {
    killByPort: (p) => { calls.killByPort.push(p); return { killed: true }; },
    stopDaemon: (opts) => { calls.stopDaemon.push(opts); return { stopped: true }; },
    killTree: (pid) => { calls.killTree.push(pid); return true; },
    killByPid: (pid) => { calls.killByPid.push(pid); return { killed: true }; },
  });
  assert.deepEqual(calls.killByPort, [9342], "gateway host killed by port");
  assert.deepEqual(calls.killTree, [100], "delivery loop killed by process tree");
  assert.equal(calls.stopDaemon.length, 1, "daemon stopped via stopDaemon");
  assert.equal(calls.stopDaemon[0].agentId, "sc-coder");
  assert.deepEqual(calls.killByPid, [7777], "console PTY killed by pid");
  // Result reports each kill, no silent drops.
  assert.ok(result.killed.gatewayHosts.length === 1);
  assert.ok(result.killed.deliveryLoops.length === 1);
  assert.ok(result.killed.daemons.length === 1);
  assert.ok(result.killed.consolePtys.length === 1);
}

// ---------------------------------------------------------------------------
// 7. reap is best-effort: a throwing primitive does not abort the others.
// ---------------------------------------------------------------------------
{
  const killedPids = [];
  const found = {
    gatewayHosts: [{ agentId: "a", port: 1 }],
    deliveryLoops: [{ agentId: "a", pid: 100 }],
    daemons: [],
    consolePtys: [{ agentId: "a", pid: 7777 }],
  };
  const result = reapManagedSurvivors(found, {
    killByPort: () => { throw new Error("boom"); },
    stopDaemon: () => ({ stopped: false }),
    killTree: (pid) => { killedPids.push(pid); return true; },
    killByPid: (pid) => { killedPids.push(pid); return { killed: true }; },
  });
  assert.deepEqual(killedPids.sort((a, b) => a - b), [100, 7777], "loop + pty still killed despite gateway throw");
  assert.ok(Array.isArray(result.errors) && result.errors.length >= 1, "errors recorded, not swallowed");
}

console.log("reap-managed-survivors.test.js: all assertions passed");
