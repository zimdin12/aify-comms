#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  enumerateManagedSurvivors,
  reapManagedSurvivors,
  stopRequestReason,
} from "../reap-managed-survivors.js";
// The marker-file read side moved to `runtime-marker-files.js` in v0.5.4; imported from its OWNER.
import { sweepTombstonedMarkers, tombstonedMarkerAgentIds } from "../runtime-marker-files.js";
// The two cmdline readers moved to `proc-probes.js` in v0.5.4 with the rest of the process read
// side. Imported from their OWNER — the reaper does not re-export them.
import { cmdlineDeliveryLoopAgent, cmdlineResidentAgent } from "../proc-probes.js";

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
// 5. live resident process truth overrides a stale managed backend record.
// ---------------------------------------------------------------------------
{
  const procs = [
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 200, ppid: 1, commandLine: "/bin/bash hermes-aify --aify-agent sc-coder --resume native-session" },
  ];
  const found = enumerateManagedSurvivors({
    ownedAgentIds: ["sc-coder"],
    listProcesses: () => procs,
    readMarkers: () => [{ kind: "port", agentId: "sc-coder", value: 9342 }],
  });
  assert.deepEqual(found.deliveryLoops, [], "live resident wrapper protects its delivery loop from managed boot reap");
  assert.deepEqual(found.gatewayHosts, [], "live resident wrapper protects its gateway marker from managed boot reap");
}

// ---------------------------------------------------------------------------
// 6. empty / missing ownedAgentIds → fail-safe, enumerate NOTHING.
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
// 7. reapManagedSurvivors delegates to the right kill primitive per kind and
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
// 8. reap is best-effort: a throwing primitive does not abort the others.
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

// ---------------------------------------------------------------------------
// 9. tombstonedMarkerAgentIds (fix/hermes-leak P4): markers for agents NOT in
//    the live keyset are tombstoned; known agents are kept; unknown keyset is
//    fail-safe (sweeps nothing).
// ---------------------------------------------------------------------------
{
  const groups = [
    { agentId: "sc-coder", files: ["/tmp/aify-hermes-port-sc-coder"] },
    { agentId: "ghost", files: ["/tmp/aify-hermes-port-ghost", "/tmp/aify-hermes-key-ghost"] },
    { agentId: "other-env", files: ["/tmp/aify-hermes-daemon-pid-other-env"] },
  ];
  // sc-coder + other-env are live/known; ghost is tombstoned.
  assert.deepEqual(
    tombstonedMarkerAgentIds(groups, ["sc-coder", "other-env"]).sort(),
    ["ghost"],
    "only the agent absent from the known keyset is tombstoned",
  );
  // Fail-safe: unknown keyset → sweep nothing.
  assert.deepEqual(tombstonedMarkerAgentIds(groups, null), [], "null keyset → fail-safe empty");
  assert.deepEqual(tombstonedMarkerAgentIds(groups, undefined), [], "undefined keyset → fail-safe empty");
  // Empty known set (a real, fetched keyset that happens to be empty) → all swept.
  assert.deepEqual(
    tombstonedMarkerAgentIds(groups, []).sort(),
    ["ghost", "other-env", "sc-coder"],
    "an explicit empty keyset tombstones every marker agent",
  );
}

// ---------------------------------------------------------------------------
// 10. sweepTombstonedMarkers (fix/hermes-leak P4): deletes ALL marker files for
//    tombstoned agents, NEVER a known agent's; fail-safe on unknown keyset.
// ---------------------------------------------------------------------------
{
  const groups = [
    { agentId: "sc-coder", files: ["/tmp/aify-hermes-port-sc-coder", "/tmp/aify-hermes-key-sc-coder"] },
    { agentId: "ghost", files: ["/tmp/aify-hermes-port-ghost", "/tmp/aify-hermes-daemon-pid-ghost", "/tmp/aify-hermes-key-ghost"] },
  ];
  const removed = [];
  const res = sweepTombstonedMarkers({
    knownAgentIds: ["sc-coder"],
    listMarkerFiles: () => groups,
    rm: (p) => removed.push(p),
    log: () => {},
  });
  assert.deepEqual(
    removed.sort(),
    ["/tmp/aify-hermes-daemon-pid-ghost", "/tmp/aify-hermes-key-ghost", "/tmp/aify-hermes-port-ghost"],
    "all three ghost markers deleted; sc-coder's untouched",
  );
  assert.equal(res.swept.length, 1, "one tombstoned agent swept");
  assert.equal(res.swept[0].agentId, "ghost");

  // Fail-safe: unknown keyset deletes NOTHING.
  const removed2 = [];
  const res2 = sweepTombstonedMarkers({
    knownAgentIds: null,
    listMarkerFiles: () => groups,
    rm: (p) => removed2.push(p),
    log: () => {},
  });
  assert.deepEqual(removed2, [], "null keyset → delete nothing (fail-safe)");
  assert.equal(res2.skipped, "known-agents-unavailable");

  // A throwing rm is recorded, not fatal.
  const res3 = sweepTombstonedMarkers({
    knownAgentIds: [],
    listMarkerFiles: () => [{ agentId: "ghost", files: ["/tmp/aify-hermes-port-ghost"] }],
    rm: () => { throw new Error("EPERM"); },
    log: () => {},
  });
  assert.ok(res3.errors.length >= 1, "rm failure recorded in errors");
}

// ---------------------------------------------------------------------------
// stopRequestReason: the teardown reason names who actually asked.
//
// It was the literal string "dashboard stop/remove", hardcoded at the call site whatever the control
// said. MEASURED on the operator's live database: of 13 stop controls ever recorded, ZERO came from
// the dashboard and ALL 13 were requested by the agent being stopped. The one log line an operator
// reads about a dying worker named the wrong actor every time -- and an operator who had not touched
// the dashboard reasonably concluded that something else had.
//
// `requestedBy` was available the whole way: the service serialises it onto every control, and
// `environment-control-loop.mjs` already reads it.
// ---------------------------------------------------------------------------
{
  // The case that caused the confusion. "stopped on request" and "the agent asked to be stopped"
  // send an operator to completely different places.
  assert.equal(
    stopRequestReason({ requestedBy: "graph-senior-dev", agentId: "graph-senior-dev" }),
    "stop requested by the agent itself (graph-senior-dev)",
  );

  assert.equal(
    stopRequestReason({ requestedBy: "dashboard", agentId: "sc-coder" }),
    "stop requested by dashboard",
  );
  assert.equal(
    stopRequestReason({ requestedBy: "sc-manager", agentId: "sc-coder" }),
    "stop requested by sc-manager",
  );

  // A control with no requester SAYS so rather than inventing one. The old string was a confident
  // answer about an actor nobody recorded; an honest gap is readable, a wrong name is not.
  for (const control of [{}, { agentId: "x" }, { requestedBy: "", agentId: "x" }, { requestedBy: "   " }]) {
    assert.equal(
      stopRequestReason(control),
      "stop control, requester not recorded",
      `expected the no-requester answer for ${JSON.stringify(control)}`,
    );
  }

  // It runs inside the control loop; a throw here would fail the stop it was explaining.
  for (const control of [null, undefined, 0, "stop", []]) {
    assert.equal(typeof stopRequestReason(control), "string");
  }

  // THE REGRESSION THAT MATTERS. Reintroducing a hardcoded default would pass every assertion above
  // that supplies a requester, so the absence is asserted directly.
  assert.ok(
    !stopRequestReason({ requestedBy: "mc-senior-dev", agentId: "mc-senior-dev" }).includes("dashboard"),
    "the reason still mentions the dashboard for a self-requested stop",
  );
}

console.log("reap-managed-survivors.test.js: all assertions passed");
