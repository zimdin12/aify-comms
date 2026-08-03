// Teardown must still reap when the service is already gone.
//
// LIVE LEAK, 2026-08-03. The operator shut everything down and killed every hermes they had open;
// nine hermes processes survived — three gateway-host triads, the oldest by two days, none
// listening, holding ~880MB. Cause: runManagedTeardownForBridge resolves its targets from a FRESH
// ownership read against the SERVICE. On a full shutdown the service is already down, so the read
// failed, teardown returned "ownership-unavailable", reaped nothing, and deferred to "the next
// boot sweep" — which on a full shutdown never comes.
//
// The fail-safe itself is right: never reap from a stale cache, because a managed->resident switch
// could mean killing a resident the operator is using. The fix is narrower — fall back to what this
// bridge instance already PROVED it owned on an earlier successful read.

import assert from "node:assert/strict";
import { resolveFreshManagedTeardownTargets } from "../managed-teardown-ownership.js";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const ok = async () => [
  { agentId: "a1", owningBridgeId: "self" },
  { agentId: "a2", owningBridgeId: "self" },
  { agentId: "other", owningBridgeId: "someone-else" },
];
const boom = async () => { throw new Error("ECONNREFUSED 127.0.0.1:8800"); };

test("a fresh read still wins and still filters to THIS bridge", async () => {
  const r = await resolveFreshManagedTeardownTargets({ selfBridgeId: "self", fetchOwnership: ok });
  assert.deepEqual(r.agentIds, ["a1", "a2"]);
  assert.equal(r.source, "fresh-ownership");
  assert.ok(!r.degraded);
});

test("service down + proven prior ownership -> reaps THAT, not nothing", async () => {
  const r = await resolveFreshManagedTeardownTargets({
    selfBridgeId: "self", fetchOwnership: boom, lastKnownOwnedAgentIds: ["a1", "a2"],
  });
  assert.deepEqual(r.agentIds, ["a1", "a2"], "the leak: this used to be []");
  assert.equal(r.degraded, true);
  assert.notEqual(r.skipped, "ownership-unavailable");
});

test("service down + NOTHING ever proven -> original fail-safe, reap nothing", async () => {
  for (const last of [null, undefined, [], ["", "   "]]) {
    const r = await resolveFreshManagedTeardownTargets({
      selfBridgeId: "self", fetchOwnership: boom, lastKnownOwnedAgentIds: last,
    });
    assert.deepEqual(r.agentIds, [], `no evidence must reap nothing (got ${JSON.stringify(r.agentIds)})`);
    assert.equal(r.skipped, "ownership-unavailable");
  }
});

test("the fallback never invents targets — it cannot exceed what was proven", async () => {
  const r = await resolveFreshManagedTeardownTargets({
    selfBridgeId: "self", fetchOwnership: boom, lastKnownOwnedAgentIds: ["a1", "a1", " a2 "],
  });
  assert.deepEqual(r.agentIds, ["a1", "a2"], "deduped and trimmed, nothing added");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (e) { failed += 1; console.log(`  FAIL ${name}\n       ${e.message}`); }
}
console.log(`\n${tests.length - failed}/${tests.length} teardown-ownership-fallback tests passed`);
if (failed) process.exit(1);
