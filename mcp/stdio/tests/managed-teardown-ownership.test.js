#!/usr/bin/env node
import assert from "node:assert/strict";
import {
  bootstrapManagedEnvironmentBridge,
  localAgentNeedsDispatchHosting,
  managedAgentNeedsDispatchHosting,
  reconcileManagedStateWithSnapshot,
  resolveFreshManagedTeardownTargets,
} from "../managed-teardown-ownership.js";
import { orphanedOwnedAgentIds } from "../reap-managed-survivors.js";

{
  assert.equal(
    localAgentNeedsDispatchHosting({ agentId: "channel-agent", channelsEnabled: true }),
    false,
    "the MCP tool bridge must not duplicate its runtime channel sidecar's dispatch loop",
  );
  assert.equal(
    localAgentNeedsDispatchHosting({ agentId: "plain-agent", channelsEnabled: false }),
    true,
  );

  assert.equal(
    managedAgentNeedsDispatchHosting({ sessionMode: "managed", status: "available" }),
    false,
    "a cold-startable agent without a live worker is owned by the spawn loop, not the 3s dispatch loop",
  );
  assert.equal(
    managedAgentNeedsDispatchHosting({ sessionMode: "managed", status: "stopped" }),
    false,
  );
  assert.equal(
    managedAgentNeedsDispatchHosting({ sessionMode: "managed", status: "working" }),
    true,
    "a live managed worker remains hosted for dispatch",
  );
  assert.equal(
    managedAgentNeedsDispatchHosting({
      sessionMode: "managed",
      status: "online",
      runtimeConfig: { channelEnabled: true },
    }),
    true,
    "the environment controller remains the productive delivery fallback for a live channel runtime",
  );
  assert.equal(
    managedAgentNeedsDispatchHosting({ sessionMode: "managed", status: "blocked" }),
    true,
    "a live worker awaiting input remains hosted",
  );
  assert.equal(
    managedAgentNeedsDispatchHosting({ sessionMode: "resident", status: "online" }),
    false,
    "resident workers are never adopted by the environment bridge",
  );
}

{
  const state = new Map([
    ["resident-now", { info: { sessionMode: "managed" } }],
    ["still-managed", { info: { sessionMode: "managed" } }],
  ]);
  const removed = reconcileManagedStateWithSnapshot(state, {
    "resident-now": { sessionMode: "resident" },
    "still-managed": { sessionMode: "managed" },
  });

  assert.deepEqual(removed, ["resident-now"]);
  assert.equal(state.has("resident-now"), false);
  assert.equal(state.has("still-managed"), true);
}

{
  const state = new Map([
    ["spawn-race", { info: { sessionMode: "managed" } }],
  ]);
  const removed = reconcileManagedStateWithSnapshot(state, {});

  assert.deepEqual(removed, []);
  assert.equal(state.has("spawn-race"), true, "an agent absent from an older snapshot must not be pruned");
}

{
  const resolved = await resolveFreshManagedTeardownTargets({
    selfBridgeId: "bridge-self",
    fetchOwnership: async () => [
      { agentId: "ours", owningBridgeId: "bridge-self" },
      { agentId: "theirs", owningBridgeId: "bridge-other" },
      { agentId: "ours", owningBridgeId: "bridge-self" },
    ],
  });

  assert.deepEqual(resolved, {
    agentIds: ["ours"],
    source: "fresh-ownership",
  });
}

{
  const resolved = await resolveFreshManagedTeardownTargets({
    selfBridgeId: "bridge-self",
    fetchOwnership: async () => {
      throw new Error("service offline");
    },
  });

  assert.deepEqual(resolved.agentIds, []);
  assert.equal(resolved.skipped, "ownership-unavailable");
  assert.match(resolved.error.message, /service offline/);
}

{
  const events = [];
  let environmentBridgeId = "bridge-old";
  let owningBridgeId = "bridge-old";
  let reaped = [];

  const result = await bootstrapManagedEnvironmentBridge({
    registerEnvironment: async () => {
      events.push("register");
      environmentBridgeId = "bridge-new";
      return true;
    },
    sweepSurvivors: async () => {
      events.push("sweep-survivors");
      reaped = orphanedOwnedAgentIds([
        {
          agentId: "managed-hermes",
          owningBridgeId,
          ownerLive: owningBridgeId === environmentBridgeId,
        },
      ], { selfBridgeId: "bridge-new", treatSelfAsOrphan: true });
    },
    sweepTombstones: async () => { events.push("sweep-tombstones"); },
    syncManagedAgents: async () => {
      events.push("sync-managed");
      owningBridgeId = "bridge-new";
    },
    startSpawnLoop: () => { events.push("start-spawn"); },
  });

  assert.deepEqual(events, [
    "register",
    "sweep-survivors",
    "sweep-tombstones",
    "sync-managed",
    "start-spawn",
  ]);
  assert.deepEqual(reaped, ["managed-hermes"], "replacement must reap predecessor before taking ownership");
  assert.equal(owningBridgeId, "bridge-new");
  assert.equal(result.started, true);
}

{
  const events = [];
  const result = await bootstrapManagedEnvironmentBridge({
    registerEnvironment: async () => false,
    sweepSurvivors: async () => { events.push("sweep-survivors"); },
    syncManagedAgents: async () => { events.push("sync-managed"); },
    startSpawnLoop: () => { events.push("start-spawn"); },
  });

  assert.deepEqual(events, [], "unknown ownership must not sweep, adopt, or spawn");
  assert.deepEqual(result, { started: false, skipped: "registration-unavailable" });
}

console.log("managed-teardown-ownership.test.js: all assertions passed");
