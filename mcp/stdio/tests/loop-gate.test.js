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
