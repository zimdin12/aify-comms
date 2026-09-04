// The loop gate, and the shutdown term it was written to add.
//
// server.js is imported by no test, so its fourteen loop gates were never covered by anything. That is not
// a coverage statistic here — it is the reason the defect existed: `shutdownStarted` was declared, set at
// the top of `shutdownWithStatus()`, read by the idempotence check and by nothing else, and no loop
// consulted it. A missing term in an uncovered file stays missing.
//
// BOTH DIRECTIONS OF EVERY TERM ARE ASSERTED. The predicate is negative (`shouldSkipLoop`) to match the
// `if (...) return;` shape it replaces, and an inversion would disable every loop in the bridge — the
// terminal-control loop, the dispatch claim, the spawn claim. A test that only checked "skips when
// shutting down" would pass just as happily against `() => true`.

import assert from "node:assert/strict";
import test from "node:test";

import { LOOP_GATE_TERMS, shouldSkipLoop } from "../loop-gate.mjs";

/** A gate that runs: eligible, idle, not shutting down. Every test below perturbs exactly one term. */
const RUNS = { eligible: true, alreadyActive: false, shuttingDown: false };

test("the baseline gate RUNS — without this, every assertion below passes vacuously", () => {
  assert.equal(shouldSkipLoop(RUNS), false);
});

test("A SHUTTING-DOWN BRIDGE SKIPS — the term that was missing from all fourteen gates", () => {
  // The defect: `shutdownWithStatus()` sets this true and then awaits seconds of teardown, during which
  // the timers keep firing. Every claim taken in that window is taken by a process about to exit.
  assert.equal(shouldSkipLoop({ ...RUNS, shuttingDown: true }), true);
});

test("shutting down beats everything else — an eligible, idle loop still skips", () => {
  // Ordering within the disjunction must not matter. If `shuttingDown` were ever ANDed with a busy flag,
  // an idle loop would keep claiming for the whole shutdown window, which is the common case: the loops
  // are idle far more often than they are mid-tick.
  assert.equal(shouldSkipLoop({ eligible: true, alreadyActive: false, shuttingDown: true }), true);
  assert.equal(shouldSkipLoop({ eligible: true, alreadyActive: true, shuttingDown: true }), true);
});

test("an INELIGIBLE loop skips, and an eligible one does not", () => {
  // `eligible` carries IS_REMOTE / IS_ENVIRONMENT_BRIDGE / bridgeTerminalSupported(). Inverted, a resident
  // bridge would start the environment-bridge loops — the failure that reaps another bridge's workers.
  assert.equal(shouldSkipLoop({ ...RUNS, eligible: false }), true);
  assert.equal(shouldSkipLoop({ ...RUNS, eligible: true }), false);
});

test("an ALREADY-ACTIVE loop skips, and an idle one does not", () => {
  // The re-entrancy guard: a timer already armed, or a tick still in flight. Inverted, `ensure*Loop` would
  // arm a second interval on every call and the busy flags would stop serialising the claims.
  assert.equal(shouldSkipLoop({ ...RUNS, alreadyActive: true }), true);
  assert.equal(shouldSkipLoop({ ...RUNS, alreadyActive: false }), false);
});

test("the full truth table — eight rows, so no combination is left to inference", () => {
  // Cheap and total. Written out because the three terms are read from three different places in server.js
  // and a partial table is how an interaction survives review.
  for (const eligible of [true, false]) {
    for (const alreadyActive of [true, false]) {
      for (const shuttingDown of [true, false]) {
        const gate = { eligible, alreadyActive, shuttingDown };
        const expected = !eligible || alreadyActive || shuttingDown;
        assert.equal(shouldSkipLoop(gate), expected, JSON.stringify(gate));
      }
    }
  }
});

test("A MISSING TERM THROWS rather than defaulting to 'keep running'", () => {
  // The whole point of making it explicit. Any default would have to be `false` to be unobtrusive, and
  // `shuttingDown: false` by omission is precisely the bug — reinstated at whichever call site forgot it.
  for (const term of LOOP_GATE_TERMS) {
    const gate = { ...RUNS };
    delete gate[term];
    assert.throws(() => shouldSkipLoop(gate), new RegExp(term), `omitting ${term} must throw`);
  }
});

test("a NON-BOOLEAN term throws — truthiness is not a decision", () => {
  // `shuttingDown: undefined` and `shuttingDown: 0` would both read as "not shutting down". A timer handle
  // is the realistic accident: `alreadyActive: spawnLoopTimer` is an object or null, never a boolean, and
  // it would work by truthiness right up until someone passed a number.
  for (const bad of [undefined, null, 0, 1, "", "false", {}]) {
    assert.throws(() => shouldSkipLoop({ ...RUNS, shuttingDown: bad }), /shuttingDown/, JSON.stringify(bad));
  }
});

test("a non-object argument throws instead of skipping or running by accident", () => {
  for (const bad of [undefined, null, true, 42, "gate"]) {
    assert.throws(() => shouldSkipLoop(bad), TypeError);
  }
});

test("the exported term list matches what the guard actually enforces", () => {
  // Redundant on purpose. Two of this series' gate bugs were found only by two assertions disagreeing; a
  // term added to the signature but not to the guard would otherwise be documented and unenforced.
  assert.deepEqual([...LOOP_GATE_TERMS].sort(), ["alreadyActive", "eligible", "shuttingDown"]);
  for (const term of LOOP_GATE_TERMS) {
    const gate = { ...RUNS };
    delete gate[term];
    assert.throws(() => shouldSkipLoop(gate), /shouldSkipLoop requires/);
  }
});

// --- the shells now own their busy flags -----------------------------------------------------
//
// In v0.5.4 each loop SHELL moved into the module holding its pass, taking its busy flag with it. The
// timer stayed in server.js, because `ensure*Loop` arms it and `cleanupOnExit` clears it — two readers,
// one of them the shutdown chain.
//
// That move turned `shutdownStarted` from a module variable the shell closed over into a PARAMETER. The
// risk it introduces is specific and silent: a caller that captured the value once, at import, would
// pass `false` forever and the shutdown gate would never fire again — which is precisely the defect
// `ef89bd6c` was written to fix. So the shells are asserted to re-read it per call.

import { runDispatchLoop } from "../dispatch-loop.mjs";

// ONE SHELL, not five, since v0.6.2 deleted the environment bridge. `runSpawnLoop`,
// `runEnvironmentControlLoop`, `runTerminalControlLoop` and `syncManagedEnvironmentAgents` were that
// bridge's loops and went with it; `runDispatchLoop` is the one a resident still runs. The property
// is unchanged and still worth asserting -- it is the shell, not the loop, that this guards.
const SHELLS = [
  ["runDispatchLoop", runDispatchLoop],
];

test("EVERY LOOP SHELL SKIPS WHEN shutdownStarted IS TRUE", async () => {
  // No HTTP stub is installed here on purpose: if a shell did NOT skip, it would reach `httpCall` and
  // either throw or hang. Returning cleanly is the observable proof that the gate held.
  for (const [name, shell] of SHELLS) {
    await assert.doesNotReject(
      () => shell({ shutdownStarted: true }),
      `${name} must return without doing work while shutting down`,
    );
  }
});

test("a shell never reaches its pass while shutting down — observed, not inferred", async () => {
  // The observable is a dependency the pass calls and the gate short-circuits past. My first version
  // asserted only that two `shutdownStarted: true` calls did not reject, which a shell ignoring the flag
  // entirely would also satisfy.
  let reached = 0;
  await assert.doesNotReject(() => runDispatchLoop({
    shutdownStarted: true,
    AUTO_REREGISTER_AFTER_FAILURES: 4,
    CLAIM_OPTS: {},
    CLAIM_WAIT_MS: 0,
    MACHINE_ID: "m",
    terminateResidentHost: () => {},
    // The observable: the pass reaches for this, the gate returns before it can.
    reportResidentRuntimeLost: () => { reached += 1; },
  }));
  assert.equal(reached, 0, "a shutting-down shell must not reach its pass");
});

// THE OPPOSITE DIRECTION IS DELIBERATELY NOT ASSERTED HERE, and the reason is worth stating rather than
// leaving as a gap someone later "fixes".
//
// Four of the five shells gate on `IS_REMOTE && IS_ENVIRONMENT_BRIDGE`, and `IS_ENVIRONMENT_BRIDGE` is
// read from `--environment-bridge` or `AIFY_ENVIRONMENT_BRIDGE=1` at module load. Setting that variable
// to make a shell proceed would make THIS TEST PROCESS an environment bridge — which is not a
// hypothetical: a hostile-env sweep in this repo set exactly that variable, the test registered as the
// environment bridge, superseded the live one, and reaped seven running gateway hosts.
//
// So the "it does proceed" direction belongs to the live round-trip, not to a unit suite. What IS
// covered here is the gate's own truth table above, which is where the decision actually lives.
