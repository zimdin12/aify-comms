// The three facts an environment advertises, decided in one place and from one answer.
//
// They come from different tiers, which is exactly why they kept disagreeing. Whether a terminal can
// be opened has been aify-env's answer since v0.6 Phase 8 and was still being read off the bridge's
// own node-pty. The reason behind it is what an operator reads when the answer is no, and without it
// making `terminal` honest just moved the confusion. And how many processes aify-env is running that
// this bridge does not know about is the number that would have shown the orphan the operator found
// by hand -- a live PTY for `ef-manager`, pid 155844, that no screen would display.
//
// ALL THREE COME OFF THE SAME `/health` RESPONSE. That is not a performance argument: a second
// `GET /processes` was measured at 0.3 ms median on loopback over twelve runs, which is nothing. It is
// that a signal costing no extra call is one nobody has to justify keeping.
import assert from "node:assert/strict";
import { test } from "node:test";

import { advertisedEnvironmentState, buildEnvironmentPayload } from "../environment-advertisement.mjs";

const delegated = (envProcessId) => ({ envProcessId, kind: "delegated" });
const ours = (id) => ({ id, service: "aify-comms" });

test("with delegation on, aify-env decides the terminal and the bridge's own pty does not", () => {
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: false, localTerminal: true, envProcesses: [],
  });
  assert.equal(state.terminal, false, "a loaded local node-pty decided a delegated environment again");
  assert.match(state.reason, /aify-env/);
});

test("a process this bridge does not hold is counted", () => {
  // THE OPERATOR'S ORPHAN, as a number. aify-env owns it; this bridge has no terminal for it.
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: true, localTerminal: true,
    envProcesses: [ours("p1"), ours("p2")],
    ownedTerminals: [delegated("p2")],
  });
  assert.equal(state.unknownProcesses, 1);
});

test("a fully accounted environment reports zero, which is a different fact from null", () => {
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: true, envProcesses: [ours("p1")],
    ownedTerminals: [delegated("p1")],
  });
  assert.equal(state.unknownProcesses, 0);
});

test("COULD NOT ASK reports null, never zero", () => {
  // A bridge that never reached aify-env accounts for NOTHING. Reporting 0 would say the opposite
  // with confidence, which is the false green this whole family of checks exists to avoid.
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: null, envProcesses: null, ownedTerminals: [],
  });
  assert.equal(state.unknownProcesses, null);
});

test("another service's processes are never this bridge's to count", () => {
  // aify-env is a shared tier. A process we never started is not one we failed to know about.
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: true,
    envProcesses: [{ id: "p9", service: "somebody-else" }],
    ownedTerminals: [],
  });
  assert.equal(state.unknownProcesses, 0);
});

test("a locally-spawned terminal does not account for a delegated process", () => {
  // Only a delegated terminal has an aify-env process behind it. Counting a local one as cover would
  // hide exactly the orphan this number exists to show.
  const state = advertisedEnvironmentState({
    delegationEnabled: true, envHealthy: true,
    envProcesses: [ours("p1")],
    ownedTerminals: [{ envProcessId: "p1", kind: "local" }],
  });
  assert.equal(state.unknownProcesses, 1);
});

test("no arguments advertises no terminal and no knowledge", () => {
  const state = advertisedEnvironmentState();
  assert.equal(state.terminal, false);
  assert.equal(state.unknownProcesses, null);
});

// ---- the payload the heartbeat actually sends ----------------------------------------------------

test("the payload carries all three, so nothing is decided twice on the way out", () => {
  const payload = buildEnvironmentPayload({
    terminalManager: {
      envDelegation: { isEnabled: () => true },
      terminals: new Map([["t1", delegated("p1")]]),
    },
    envHealthy: true,
    envProcesses: [ours("p1"), ours("p2")],
    localTerminal: false,
  });
  assert.equal(payload.terminal, true);
  assert.equal(payload.pty, true, "pty and terminal must not disagree: they answer one question");
  assert.equal(payload.metadata.unknownProcesses, 1);
  assert.match(payload.metadata.terminalReason, /aify-env/);
});

test("the payload reports no terminal, and says why, when aify-env is silent", () => {
  const payload = buildEnvironmentPayload({
    terminalManager: { envDelegation: { isEnabled: () => true }, terminals: new Map() },
    envHealthy: null,
    envProcesses: null,
    localTerminal: true,
  });
  assert.equal(payload.terminal, false);
  assert.deepEqual(payload.terminalRuntimes, [], "runtimes were advertised for a terminal we cannot open");
  assert.match(payload.metadata.terminalReason, /did not answer/);
  assert.equal(payload.metadata.unknownProcesses, null);
});

test("with delegation OFF the local pty still decides and nothing is counted", () => {
  // The pre-Phase-8 answer, unchanged. A bridge hosting its own terminals is entitled to answer for
  // them, and it has no aify-env listing to compare against.
  const payload = buildEnvironmentPayload({
    terminalManager: { envDelegation: null, terminals: new Map() },
    envHealthy: null,
    envProcesses: null,
    localTerminal: true,
  });
  assert.equal(payload.terminal, true);
  assert.equal(payload.metadata.unknownProcesses, null);
});

test("a manager with no terminals map does not throw", () => {
  // This runs on every heartbeat. A shape it does not expect must not stop the environment reporting
  // at all, which would be a worse failure than any number it could get wrong.
  assert.doesNotThrow(() => buildEnvironmentPayload({ terminalManager: {}, envProcesses: [ours("p1")] }));
  assert.doesNotThrow(() => buildEnvironmentPayload({}));
});
