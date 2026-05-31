// Regression (holistic-review F1, 2026-05-31): the channel-sidecar bridge id
// MUST be agent-scoped. bridge_instances.id is a PRIMARY KEY, so a machine-global
// `channel-<machine>` id let only ONE co-located claude agent own the row —
// every other agent's sidecar could not insert/refresh its liveness heartbeat
// (lost heartbeats, wrong status, cross-agent supersession that permanently
// blocked claims). This locks the invariant so it can't silently revert.
import { test } from "node:test";
import assert from "node:assert/strict";
import { channelBridgeId } from "../claude-channel.js";

test("channelBridgeId is agent-scoped (includes the agentId)", () => {
  const a = channelBridgeId("sc-claude");
  const b = channelBridgeId("sc-manager");
  assert.notEqual(a, b, "different agents MUST get different bridge ids");
  assert.ok(a.includes("sc-claude"), `bridge id must contain the agentId; got ${a}`);
  assert.ok(b.includes("sc-manager"), `bridge id must contain the agentId; got ${b}`);
});

test("channelBridgeId is stable for the same agent (idempotent across polls)", () => {
  assert.equal(channelBridgeId("sc-claude"), channelBridgeId("sc-claude"));
});

test("channelBridgeId falls back to the machine-global prefix when agentId is empty", () => {
  // An unbound poll (no agentId yet) must not crash; it returns the bare prefix.
  const bare = channelBridgeId("");
  assert.ok(bare.startsWith("channel-"), `expected channel-<machine> prefix; got ${bare}`);
  assert.ok(!bare.endsWith("-"), "must not leave a dangling separator");
});
