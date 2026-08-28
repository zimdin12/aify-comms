// The terminal control loop runs where a terminal is REACHABLE, not where node-pty happened to build.
//
// FOUND BY CHECKING A CLAIM RATHER THAN RESTATING IT, one round after the same method found a
// different hole. Both `ensureTerminalControlLoop` and `runTerminalControlLoop` gated on
// `bridgeTerminalSupported()` -- did node-pty load in THIS process. That was the right question until
// v0.6 Phase 8 flipped on 2026-08-25, after which the bridge does not use its own node-pty for a
// managed spawn at all: it delegates to aify-env and refuses rather than falling back.
//
// WHAT IT COSTS ON A HOST WHERE node-pty DOES NOT BUILD, which is an ordinary thing -- it is a native
// module needing a toolchain. Delegation makes such a host perfectly workable, aify-env opens the
// terminals, and the loop that claims terminal controls never starts. No spawns, no console input, no
// label reconcile, no stream re-attach: every part of the delegated path present and unreachable, with
// nothing saying why.
//
// IT IS INERT WHERE node-pty DOES LOAD, which is why nobody has hit it. This host loads it, so the loop
// runs and the wrong gate never shows -- the same shape as the environment-capability defect, and
// correct-looking for the same reason: the two answers coincide on the machine it was written on.
//
// THE PREDICATE IS ASKED IN TWO PLACES, which is the other half. `ensureTerminalControlLoop` decides
// whether the loop is ever started; `runTerminalControlLoop` decides whether each pass runs. They were
// two hand-written copies, and a loop that starts and then skips every pass is indistinguishable from
// one that never started.
import assert from "node:assert/strict";
import { test } from "node:test";

import { stopTerminalControlLoop } from "../terminal-control-loop.mjs";
import { terminalLoopEligible, terminalsArePossible } from "../terminals-are-possible.mjs";

test("a local pty makes terminals possible", () => {
  // The pre-Phase-8 answer, unchanged. A bridge that hosts its own terminals still qualifies.
  assert.equal(terminalsArePossible({ localTerminal: true, delegationEnabled: false }), true);
});

test("DELEGATION makes terminals possible with no local pty at all", () => {
  // THE DEFECT, in one assertion. This is the host where node-pty does not build and everything else
  // works, and the loop refused to run on it.
  assert.equal(terminalsArePossible({ localTerminal: false, delegationEnabled: true }), true);
});

test("neither means no terminal is reachable", () => {
  assert.equal(terminalsArePossible({ localTerminal: false, delegationEnabled: false }), false);
  assert.equal(terminalsArePossible(), false, "a default that says yes would start a loop that cannot work");
});

test("both is still yes", () => {
  assert.equal(terminalsArePossible({ localTerminal: true, delegationEnabled: true }), true);
});

// ---- the whole loop condition ------------------------------------------------------------------

const eligible = (over = {}) => terminalLoopEligible({
  isRemote: true, isEnvironmentBridge: true, localTerminal: true, delegationEnabled: false, ...over,
});

test("the loop runs for a remote environment bridge that can reach a terminal", () => {
  assert.equal(eligible(), true);
});

test("a delegating bridge with no local pty still runs the loop", () => {
  assert.equal(eligible({ localTerminal: false, delegationEnabled: true }), true);
});

test("a bridge that is not the environment bridge never runs it", () => {
  // Every managed terminal on a host belongs to ONE bridge. A second one claiming controls is the
  // collision the environment-bridge role exists to prevent.
  assert.equal(eligible({ isEnvironmentBridge: false }), false);
});

test("a local-only bridge never runs it", () => {
  // Terminal controls come from the hub. With no remote service there is nothing to claim.
  assert.equal(eligible({ isRemote: false }), false);
});

test("a bridge that can reach no terminal never runs it", () => {
  assert.equal(eligible({ localTerminal: false, delegationEnabled: false }), false);
});

test("no arguments does not start a loop", () => {
  // The default matters: this predicate decides whether a background loop exists at all, and one
  // started on a bridge that should not have it claims controls belonging to another.
  assert.equal(terminalLoopEligible(), false);
});

test("the two questions are the same question", () => {
  // `ensureTerminalControlLoop` and `runTerminalControlLoop` both ask it. Two hand-written copies is
  // how they come to disagree, and the disagreement is invisible: a loop that starts and then skips
  // every pass looks exactly like one that never started.
  for (const input of [
    { isRemote: true, isEnvironmentBridge: true, localTerminal: true, delegationEnabled: false },
    { isRemote: true, isEnvironmentBridge: true, localTerminal: false, delegationEnabled: true },
    { isRemote: true, isEnvironmentBridge: true, localTerminal: false, delegationEnabled: false },
    { isRemote: false, isEnvironmentBridge: true, localTerminal: true, delegationEnabled: true },
    { isRemote: true, isEnvironmentBridge: false, localTerminal: true, delegationEnabled: true },
  ]) {
    assert.equal(
      terminalLoopEligible(input),
      Boolean(input.isRemote) && Boolean(input.isEnvironmentBridge)
        && terminalsArePossible(input),
      `the composed gate disagrees with its parts for ${JSON.stringify(input)}`,
    );
  }
});


// ---- the loop's lifecycle now lives with the loop ------------------------------------------
//
// v0.5.4 moved the loop's BODY out of server.js and left the timer behind, with a note saying so.
// That was right at the time: the module had no opinion about whether the loop should run. It has
// one now, and the split meant server.js asked that question and this module asked it again, in two
// hand-written copies.

test("stopping a loop that was never started is safe", () => {
  // Shutdown runs on paths that never started one -- a bridge that is not the environment bridge, a
  // local-only bridge, a process that failed before the loop was reached. A teardown that threw
  // there would turn an ordinary exit into a crash, and this is called from the exit handler.
  assert.doesNotThrow(() => stopTerminalControlLoop());
  assert.doesNotThrow(() => stopTerminalControlLoop(), "a second stop threw");
});
