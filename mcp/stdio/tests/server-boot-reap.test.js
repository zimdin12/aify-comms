#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  reapOrphanedManagedSurvivors,
  orphanedOwnedAgentIds,
  bridgeOwnerIsLive,
} from "../reap-managed-survivors.js";

// On env-bridge boot we reap managed-triad survivors whose owning bridge is NOT
// fresh in bridge_instances, and SKIP any owned by a currently-live different
// bridge. The "is the owning bridge live?" determination is injected as a list
// of { agentId, owningBridgeId, ownerLive } records the bridge derives from the
// server. ownerLive reflects whether the OWNING ENVIRONMENT BRIDGE is alive —
// NOT the agent's derived status (a SIGKILL survivor's detached delivery loop
// keeps the agent online/working, so status would falsely mark a dead owner as
// live and the orphan would never be reaped). The bridge derives ownerLive
// host-side from GET /environments (each env's current bridgeId + online/offline
// status) cross-checked against the agent's runtimeState.bridgeInstanceId.

// ---------------------------------------------------------------------------
// bridgeOwnerIsLive: the OWNING-BRIDGE freshness predicate that feeds ownerLive.
// An owning bridge is live iff it is the CURRENT bridgeId of an ONLINE
// environment. The agent's derived status must NOT enter this decision.
// ---------------------------------------------------------------------------
{
  // Crash/SIGKILL survivor: stored owningBridgeId points at the DEAD predecessor
  // bridge. The env's row now carries the NEW bridge id (online) — the old id
  // matches no online env → owner is NOT live even though the survivor's
  // detached loop keeps the agent's status online.
  const environments = [
    { id: "win:host:default", bridgeId: "bridge-NEW", status: "online" },
  ];
  assert.equal(
    bridgeOwnerIsLive("bridge-OLD", { environments, selfBridgeId: "bridge-NEW" }),
    false,
    "dead predecessor bridge id matches no online env → owner NOT live (would be reaped)",
  );

  // A genuinely live DIFFERENT bridge: it IS the current bridgeId of an online
  // env → owner live → must be skipped.
  const multiEnv = [
    { id: "win:host:default", bridgeId: "bridge-NEW", status: "online" },
    { id: "linux:peer:default", bridgeId: "bridge-OTHER", status: "online" },
  ];
  assert.equal(
    bridgeOwnerIsLive("bridge-OTHER", { environments: multiEnv, selfBridgeId: "bridge-NEW" }),
    true,
    "owning bridge is the current bridge of an online env → owner LIVE (skip)",
  );

  // Same id but the env is OFFLINE (stale last_seen): owner not live.
  assert.equal(
    bridgeOwnerIsLive("bridge-OLD", {
      environments: [{ id: "win:host:default", bridgeId: "bridge-OLD", status: "offline" }],
      selfBridgeId: "bridge-NEW",
    }),
    false,
    "owning bridge is the current bridge of an OFFLINE env → owner NOT live (reap)",
  );

  // The freshly-booted bridge's OWN id is always treated as live (defensive;
  // orphanedOwnedAgentIds also skips self).
  assert.equal(
    bridgeOwnerIsLive("bridge-NEW", { environments: [], selfBridgeId: "bridge-NEW" }),
    true,
    "self bridge id → live",
  );

  // Empty owning bridge id (never synced) → not live → eligible for reap.
  assert.equal(
    bridgeOwnerIsLive("", { environments, selfBridgeId: "bridge-NEW" }),
    false,
    "no owning bridge id → not live",
  );
}

// ---------------------------------------------------------------------------
// orphanedOwnedAgentIds: select agents to reap — those WITHOUT a live owner,
// and never an agent owned by THIS freshly-booted bridge id.
// ---------------------------------------------------------------------------
{
  const records = [
    { agentId: "dead-owner", owningBridgeId: "bridge-OLD", ownerLive: false },
    { agentId: "live-other", owningBridgeId: "bridge-OTHER", ownerLive: true },
    { agentId: "no-owner", owningBridgeId: "", ownerLive: false },
    { agentId: "us", owningBridgeId: "bridge-SELF", ownerLive: true },
  ];
  // Default (runtime) semantics: never reap our own id.
  const reap = orphanedOwnedAgentIds(records, { selfBridgeId: "bridge-SELF" });
  assert.deepEqual(reap.sort(), ["dead-owner", "no-owner"], "reap dead/ownerless; skip live-other + self");

  // Boot semantics (treatSelfAsOrphan): a survivor whose agent record reads SELF
  // is a predecessor's orphan (the sync-before-sweep race / SIGKILL stale-online
  // env row rebinds it to self) and MUST be reaped — while a genuinely-live
  // DIFFERENT bridge's agent is STILL skipped.
  const bootReap = orphanedOwnedAgentIds(records, {
    selfBridgeId: "bridge-SELF",
    treatSelfAsOrphan: true,
  });
  assert.deepEqual(
    bootReap.sort(),
    ["dead-owner", "no-owner", "us"],
    "boot: reap dead/ownerless + self-bound predecessor; STILL skip a live different bridge",
  );
}

// ---------------------------------------------------------------------------
// 1. boot sweep reaps survivors of a dead owner, SKIPS a live different bridge.
// ---------------------------------------------------------------------------
{
  const calls = { killByPort: [], killTree: [], stopDaemon: [] };
  const ownership = [
    { agentId: "sc-coder", owningBridgeId: "bridge-OLD", ownerLive: false },     // dead owner → reap
    { agentId: "graph-tl", owningBridgeId: "bridge-LIVE", ownerLive: true },     // live other → skip
  ];
  const procs = [
    { pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 200, ppid: 1, commandLine: "node hermes-managed-host.js run graph-tl" }, // owned by live bridge → must NOT die
  ];
  const markers = [
    { kind: "port", agentId: "sc-coder", value: 9342 },
    { kind: "port", agentId: "graph-tl", value: 9341 },
    { kind: "daemon-pid", agentId: "sc-coder", value: 4242 },
  ];

  const result = reapOrphanedManagedSurvivors({
    selfBridgeId: "bridge-NEW",
    cwdRoots: ["C:/Docker/aify-comms"],
    fetchOwnership: () => ownership,
    listProcesses: () => procs,
    readMarkers: () => markers,
    killByPort: (p) => { calls.killByPort.push(p); return { killed: true }; },
    stopDaemon: (o) => { calls.stopDaemon.push(o); return { stopped: true }; },
    killTree: (pid) => { calls.killTree.push(pid); return true; },
  });

  assert.deepEqual(calls.killByPort, [9342], "only the dead-owner gateway killed (9341 belongs to live bridge)");
  assert.deepEqual(calls.killTree, [100], "only the dead-owner loop killed (200 belongs to live bridge)");
  assert.equal(calls.stopDaemon.length, 1);
  assert.equal(calls.stopDaemon[0].agentId, "sc-coder");
  // graph-tl (owned by a live different bridge) is NEVER touched.
  assert.ok(!calls.killByPort.includes(9341));
  assert.ok(!calls.killTree.includes(200));
  assert.ok(result && result.killed);
}

// ---------------------------------------------------------------------------
// 2. nothing orphaned → no-op (all owners live, or owned by self).
// ---------------------------------------------------------------------------
{
  const calls = [];
  const result = reapOrphanedManagedSurvivors({
    selfBridgeId: "bridge-SELF",
    fetchOwnership: () => [
      { agentId: "a", owningBridgeId: "bridge-LIVE", ownerLive: true },
      { agentId: "b", owningBridgeId: "bridge-SELF", ownerLive: true },
    ],
    listProcesses: () => [{ pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run a" }],
    readMarkers: () => [{ kind: "port", agentId: "a", value: 9342 }],
    killByPort: (p) => calls.push(["port", p]),
    stopDaemon: (o) => calls.push(["daemon", o]),
    killTree: (pid) => calls.push(["tree", pid]),
  });
  assert.deepEqual(calls, [], "no orphans → kills nothing");
  assert.equal(result.killed.gatewayHosts.length, 0);
}

// ---------------------------------------------------------------------------
// 3. FAIL-SAFE: a throwing fetchOwnership → reap NOTHING (no scope = no kill).
// ---------------------------------------------------------------------------
{
  const calls = [];
  const result = reapOrphanedManagedSurvivors({
    selfBridgeId: "bridge-SELF",
    fetchOwnership: () => { throw new Error("server unreachable"); },
    listProcesses: () => [{ pid: 100, ppid: 1, commandLine: "node hermes-managed-host.js run a" }],
    readMarkers: () => [{ kind: "port", agentId: "a", value: 9342 }],
    killByPort: (p) => calls.push(["port", p]),
    stopDaemon: (o) => calls.push(["daemon", o]),
    killTree: (pid) => calls.push(["tree", pid]),
  });
  assert.deepEqual(calls, [], "ownership unknown → fail-safe, kills nothing");
  assert.ok(result && result.skipped === "ownership-unavailable");
}

// ---------------------------------------------------------------------------
// 4. WS2 REGRESSION: the sync-before-sweep race. After restart the env
//    heartbeat's syncManagedEnvironmentAgents() re-binds the survivor agents'
//    runtimeState.bridgeInstanceId to the NEW (self) bridge BEFORE the boot
//    sweep reads ownership — so the agent records read SELF and ownerLive=true.
//    Without treatSelfAsOrphan the sweep skips them (the bug: survivors live on).
//    With treatSelfAsOrphan the boot sweep reaps the self-bound predecessor
//    survivors, and STILL never touches a co-located live OTHER bridge's agent.
// ---------------------------------------------------------------------------
{
  const calls = { killByPort: [], killTree: [], stopDaemon: [] };
  // Both managed agents now read self as owner (rebound by the racing sync);
  // ownerLive=true because self is live. A second, genuinely-live different
  // bridge owns "peer-agent".
  const ownership = [
    { agentId: "sc-architect", owningBridgeId: "bridge-NEW", ownerLive: true }, // rebound to self → must STILL be reaped
    { agentId: "sc-coder", owningBridgeId: "bridge-NEW", ownerLive: true },     // rebound to self → must STILL be reaped
    { agentId: "peer-agent", owningBridgeId: "bridge-OTHER", ownerLive: true }, // live different bridge → must NOT die
  ];
  const procs = [
    { pid: 301, ppid: 1, commandLine: "node hermes-managed-host.js run sc-architect" },
    { pid: 302, ppid: 1, commandLine: "node hermes-managed-host.js run sc-coder" },
    { pid: 303, ppid: 1, commandLine: "node hermes-managed-host.js run peer-agent" },
    // A resident operator session for an owned agent must never be reaped (it is
    // not a delivery loop; it carries --aify-agent and is excluded outright).
    { pid: 999, ppid: 1, commandLine: "claude --aify-agent sc-architect" },
  ];
  const markers = [
    { kind: "port", agentId: "sc-architect", value: 9342 },
    { kind: "port", agentId: "sc-coder", value: 9343 },
    { kind: "port", agentId: "peer-agent", value: 9341 },
    { kind: "daemon-pid", agentId: "sc-architect", value: 4242 },
  ];

  // Without the fix (default): both survivors are skipped — reproduces the bug.
  const buggy = reapOrphanedManagedSurvivors({
    selfBridgeId: "bridge-NEW",
    cwdRoots: ["C:/Docker/aify-comms"],
    fetchOwnership: () => ownership,
    listProcesses: () => procs,
    readMarkers: () => markers,
    killByPort: () => ({ killed: true }),
    stopDaemon: () => ({ stopped: true }),
    killTree: () => true,
  });
  assert.equal(buggy.killed.deliveryLoops.length, 0, "default semantics reproduce the WS2 miss (self-bound survivors skipped)");

  // With the fix (boot path): self-bound predecessor survivors ARE reaped; the
  // live OTHER bridge's agent is NOT.
  const result = reapOrphanedManagedSurvivors({
    selfBridgeId: "bridge-NEW",
    cwdRoots: ["C:/Docker/aify-comms"],
    treatSelfAsOrphan: true,
    fetchOwnership: () => ownership,
    listProcesses: () => procs,
    readMarkers: () => markers,
    killByPort: (p) => { calls.killByPort.push(p); return { killed: true }; },
    stopDaemon: (o) => { calls.stopDaemon.push(o); return { stopped: true }; },
    killTree: (pid) => { calls.killTree.push(pid); return true; },
  });

  assert.deepEqual(calls.killTree, [302], "boot reaps only the self-bound predecessor without a live resident wrapper");
  assert.ok(!calls.killTree.includes(303), "live OTHER bridge's loop NEVER reaped");
  assert.ok(!calls.killTree.includes(999), "resident operator session NEVER reaped");
  assert.deepEqual(calls.killByPort, [9343], "resident process truth protects its gateway; live-other gateway is also untouched");
  assert.ok(!calls.killByPort.includes(9341), "live OTHER bridge's gateway NEVER reaped");
  assert.equal(calls.stopDaemon.length, 0, "resident process truth protects its daemon marker too");
}

console.log("server-boot-reap.test.js: all assertions passed");
