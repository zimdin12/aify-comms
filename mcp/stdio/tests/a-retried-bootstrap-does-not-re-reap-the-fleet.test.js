#!/usr/bin/env node
// A BOOT sweep runs at boot. Once per process, whatever the retry does.
//
// THE COMBINATION NOTHING SHOULD HAVE: `bootstrapManagedEnvironmentBridge` reaps managed survivors --
// it kills processes -- and `ensureEnvironmentHeartbeat` calls it again on every heartbeat tick, every
// 30 seconds by default, for as long as `environmentBridgeBootstrapped` is false. That flag is set
// only when the bootstrap returns `{started: true}`, and every step AFTER the sweep can prevent that:
// `sweepTombstones`, `syncManagedAgents` and `startSpawnLoop` can each throw, and the caller's
// `.catch` turns a throw into `{started: false}`.
//
// So one throw after the sweep re-reaped the whole managed fleet every thirty seconds, indefinitely.
//
// THIS HAS HAPPENED. The file's own comment records 2026-08-18: a dependency-bag mistake made
// `syncManagedAgents` throw and "the environment bridge reaped its boot survivors and then never came
// up". That incident was closed by fixing the thrower. The structure that turned ONE throw into a
// repeating fleet-kill was left exactly as it was, and the next thrower would have done it again.
//
// NOT PROVEN TO BE THE 2026-08-26 MASS DEATHS, and this file does not claim it. The sweep logs a line
// whenever it kills anything and that line appears ONCE in the operator's bridge log, not repeatedly,
// so the retry loop was not running during those clusters. This is a latent defect found while
// investigating them, worth fixing on its own terms.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bootstrapManagedEnvironmentBridge,
  resetBootSurvivorSweepForTests,
} from "../managed-teardown-ownership.js";

function harness({ failAfterSweep = false } = {}) {
  const calls = { registered: 0, swept: 0, tombstones: 0, synced: 0, spawnLoop: 0 };
  return {
    calls,
    deps: {
      registerEnvironment: async () => { calls.registered += 1; return true; },
      sweepSurvivors: async () => { calls.swept += 1; return true; },
      sweepTombstones: async () => { calls.tombstones += 1; },
      syncManagedAgents: async () => {
        calls.synced += 1;
        if (failAfterSweep) throw new Error("Cannot destructure property 'MACHINE_ID' of 'undefined'");
      },
      startSpawnLoop: () => { calls.spawnLoop += 1; },
    },
  };
}

test("a bootstrap that FAILS AFTER THE SWEEP does not re-reap on the retry", async () => {
  // The 2026-08-18 shape exactly: registration succeeds, the sweep kills the survivors, and then the
  // sync throws. The heartbeat retries every 30s forever, because `started` never becomes true.
  resetBootSurvivorSweepForTests();
  const { calls, deps } = harness({ failAfterSweep: true });

  await assert.rejects(() => bootstrapManagedEnvironmentBridge(deps));
  for (let tick = 0; tick < 5; tick += 1) {
    await bootstrapManagedEnvironmentBridge(deps).catch(() => {});
  }

  assert.equal(
    calls.swept, 1,
    `the managed fleet was reaped ${calls.swept} times by ${calls.registered} bootstrap attempts; `
      + "one throw after the sweep turns a heartbeat into a repeating fleet-kill",
  );
});

test("registration IS still retried, because that is what a retry is for", () => {
  // The guard must not turn "do not re-kill" into "do not re-attempt". Registration is idempotent and
  // a transient failure there is exactly the case the heartbeat retry exists to recover from.
  resetBootSurvivorSweepForTests();
  const { calls, deps } = harness({ failAfterSweep: true });
  return bootstrapManagedEnvironmentBridge(deps)
    .catch(() => {})
    .then(() => bootstrapManagedEnvironmentBridge(deps).catch(() => {}))
    .then(() => {
      assert.equal(calls.registered, 2, "the retry stopped re-registering the environment");
    });
});

test("a first, successful bootstrap sweeps exactly once and starts everything", async () => {
  resetBootSurvivorSweepForTests();
  const { calls, deps } = harness();
  const result = await bootstrapManagedEnvironmentBridge(deps);

  assert.deepEqual(result, { started: true });
  assert.equal(calls.swept, 1);
  assert.equal(calls.tombstones, 1);
  assert.equal(calls.spawnLoop, 1);
});

test("a sweep that REFUSES still stops the bootstrap, and is not retried either", async () => {
  // `sweepSurvivors` returns false when it could not read ownership -- fail-safe, it reaped nothing.
  // The bootstrap must still refuse to start, and the retry must not run the sweep again: the guard is
  // set BEFORE the call, because a sweep that threw has already reaped whatever it reached.
  resetBootSurvivorSweepForTests();
  let sweeps = 0;
  const deps = {
    registerEnvironment: async () => true,
    sweepSurvivors: async () => { sweeps += 1; return false; },
    syncManagedAgents: async () => {},
    startSpawnLoop: () => {},
  };
  assert.deepEqual(await bootstrapManagedEnvironmentBridge(deps),
    { started: false, skipped: "survivor-sweep-unavailable" });
  await bootstrapManagedEnvironmentBridge(deps);
  assert.equal(sweeps, 1);
});

test("a failed REGISTRATION never reaches the sweep at all", async () => {
  // The order matters and is unchanged: nothing is killed before the environment is claimed.
  resetBootSurvivorSweepForTests();
  let sweeps = 0;
  const result = await bootstrapManagedEnvironmentBridge({
    registerEnvironment: async () => false,
    sweepSurvivors: async () => { sweeps += 1; return true; },
  });
  assert.deepEqual(result, { started: false, skipped: "registration-unavailable" });
  assert.equal(sweeps, 0, "survivors were reaped before the environment was even claimed");
});
