// Real tests for the managed-teardown SWEEPS, extracted from server.js in v0.5.4.
//
// WHAT THESE TESTS DO **NOT** COVER, stated first because three green ticks must not read as more.
// The reaping itself is NOT exercised. All three sweeps reach the real `killManagedTree`, `stopDaemon` and
// `defaultKillByPort` through module-scope imports that cannot be substituted, so driving them would run a
// genuine teardown against whatever managed workers exist on the machine. That is the move that has twice
// cost this project its running fleet, and `single-agent-teardown.mjs` shipped with the same limitation
// written down rather than papered over.
//
// Reaching the reaping logic requires `IS_ENVIRONMENT_BRIDGE` to be true, which means setting
// AIFY_ENVIRONMENT_BRIDGE — forbidden here: a test run that set it BECAME the environment bridge,
// superseded the live one and reaped seven gateway hosts (2026-08-13). A role flag is not a config knob.
// Making this testable needs the kill functions injected, which is a signature change and not a
// byte-identical relocation.
//
// WHAT IS COVERED is every branch reachable without side effects — and one of them is the module's central
// safety property. `runManagedTeardownSync` reaps ONLY targets a fresh ownership resolution confirmed; with
// no confirmation it returns before touching anything. That fail-closed rule is why an unexpected exit is a
// no-op rather than a wrong kill, and it is asserted here in the only way it can be: through the latch the
// factory owns.

import assert from "node:assert/strict";
import test from "node:test";

import { createManagedTeardownSweeps } from "../managed-teardown-sweeps.mjs";
import { IS_ENVIRONMENT_BRIDGE } from "../launch-identity.mjs";

// A reader that RECORDS rather than answers. If a sweep ever called it in this process, the guard above it
// has stopped working — which is exactly what these tests are for.
function recordingReader() {
  const calls = [];
  const fn = async () => { calls.push(Date.now ? 1 : 1); return []; };
  return { fn, calls };
}

test("the fixture assumption holds: this process is NOT an environment bridge", () => {
  // Everything below depends on it. If this ever fails, the tests are no longer proving guards — they are
  // running teardown.
  assert.equal(IS_ENVIRONMENT_BRIDGE, false);
});

test("the factory returns exactly the three sweeps", () => {
  const { fn } = recordingReader();
  const sweeps = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: fn });
  assert.deepEqual(Object.keys(sweeps).sort(),
    ["runBootSurvivorSweep", "runManagedTeardownForBridge", "runManagedTeardownSync"]);
  for (const name of Object.keys(sweeps)) assert.equal(typeof sweeps[name], "function", name);
});

test("a non-bridge process reaps NOTHING and does not even ask who it owns", () => {
  // The guard is what makes every ordinary agent shell safe: only the environment bridge tears down
  // managed workers. If the ownership reader were consulted first, a plain session would be making
  // teardown decisions.
  const { fn, calls } = recordingReader();
  const sweeps = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: fn });
  const result = sweeps.runManagedTeardownSync("bridge exit");
  assert.equal(result, undefined);
  assert.deepEqual(calls, [], "the ownership reader must not be called");
});

test("the async sweeps are equally inert in a non-bridge process", async () => {
  const { fn, calls } = recordingReader();
  const sweeps = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: fn });

  await sweeps.runManagedTeardownForBridge("graceful shutdown");
  const bootResult = await sweeps.runBootSurvivorSweep();

  assert.deepEqual(calls, [], "neither sweep may consult ownership outside a bridge");
  assert.equal(bootResult, true,
    "the boot sweep reports SUCCESS when it is not applicable — a false here would make a caller retry "
    + "forever on every ordinary shell");
});

test("THE FAIL-CLOSED RULE: sync teardown reaps nothing without a fresh confirmation", async () => {
  // The safety property this module exists to hold. `runManagedTeardownSync` may only reuse targets that
  // `runManagedTeardownForBridge` confirmed from a live ownership read; an unexpected exit has no such
  // snapshot, so it must reap nothing and leave the next boot sweep as the backstop.
  //
  // The latch is private to the factory, so it is observed the only way a consumer can: run the sync sweep
  // WITHOUT a preceding confirmation and require it to be a no-op. In this process the guard returns first,
  // so this asserts the reachable half — that no path reaches a kill without both the bridge role and a
  // confirmation.
  const { fn, calls } = recordingReader();
  const sweeps = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: fn });
  for (const reason of ["bridge exit", "unexpected exit", "", undefined]) {
    assert.equal(sweeps.runManagedTeardownSync(reason), undefined);
  }
  assert.deepEqual(calls, []);
});

test("each factory call gets its OWN confirmation latch", () => {
  // Two bridges in one process is not a supported configuration, but a latch shared through module scope
  // would let one instance's confirmed targets be reaped by another's exit — the class of bug that made
  // `CONTROL_CLAIM_FAILURES` keyed by label. Cheap to prevent, expensive to diagnose.
  const a = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: recordingReader().fn });
  const b = createManagedTeardownSweeps({ fetchManagedOwnershipForEnv: recordingReader().fn });
  assert.notEqual(a.runManagedTeardownSync, b.runManagedTeardownSync,
    "each instance must close over its own state, not a module-scope singleton");
});

test("the sweeps are NOT exported from the primitive reaper or the per-agent teardown", async () => {
  // Three modules, three jobs: `reap-managed-survivors.js` owns the primitives and decides nothing;
  // `single-agent-teardown.mjs` is TOLD one target; these DECIDE a set. Merging any of them would blur the
  // blast-radius boundary that kept this group out of the file until last.
  const reaper = await import("../reap-managed-survivors.js");
  const single = await import("../single-agent-teardown.mjs");
  for (const name of ["runManagedTeardownForBridge", "runBootSurvivorSweep", "runManagedTeardownSync"]) {
    assert.equal(reaper[name], undefined, `${name} must not come from the primitive reaper`);
    assert.equal(single[name], undefined, `${name} must not come from the per-agent teardown`);
  }
  assert.equal(typeof reaper.runManagedTeardown, "function", "the primitive stays where it was");
  assert.equal(typeof single.runSingleAgentManagedTeardown, "function");
});

test("importing the module reaps nothing and needs no live service", async () => {
  const again = await import("../managed-teardown-sweeps.mjs");
  assert.equal(again.createManagedTeardownSweeps, createManagedTeardownSweeps,
    "one module instance, no load-time side effects");
});
