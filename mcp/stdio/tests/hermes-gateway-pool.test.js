#!/usr/bin/env node
// The gateway-session POOL: who gets an existing child, who gets a new one, and who gets neither.
//
// Three exports of `hermes-managed-gateway-session.js` were named by no test —
// `getOrCreateHermesGatewaySession`, `_resetHermesGatewayPoolForTests` and `managedHermesUsesGateway`
// — while the module's other four were covered. That is the case the module-level gate cannot see
// and `every-export-is-named-by-a-test.test.js` was added for.
//
// WHAT THE POOL IS FOR. One `hermes dashboard` child per managed hermes agent, shared by the bridge
// and the dashboard Console — the whole reason this path exists instead of ACP, which is
// single-client. Handing out a SECOND child for an agent that already has one gives the two
// subscribers different sessions and the Console silently shows a different conversation from the
// one the bridge is driving.
//
// AND WHY IT MUST NOT HAND BACK A DEAD ONE. A stopped or failed session keeps its pool slot until
// something replaces it. Returning it would hand the caller a child that will never answer, with no
// error to explain why — so those two states are evicted and rebuilt while every other state is
// reused.
//
// THE CONSTRUCTOR IS INERT, which is what makes this testable at all: it assigns fields and spawns
// nothing until `ensureStarted()`. Nothing here starts a process, opens a socket or picks a port.

import assert from "node:assert/strict";
import test from "node:test";

import {
  HermesManagedGatewaySession,
  __hermesGatewayPoolSize,
  __injectHermesGatewaySessionForTests,
  _resetHermesGatewayPoolForTests,
  getOrCreateHermesGatewaySession,
  managedHermesUsesGateway,
} from "../hermes-managed-gateway-session.js";

const ENV_FLAG = "AIFY_HERMES_MANAGED_USE_GATEWAY";

function withEnv(value, fn) {
  // SEALED AND RESTORED. The flag is read from the live environment at call time, so a test that
  // set it and walked away would decide the behaviour of every later test in the run — and on this
  // host the operator's own environment is what the process inherits.
  const had = Object.prototype.hasOwnProperty.call(process.env, ENV_FLAG);
  const previous = process.env[ENV_FLAG];
  if (value === undefined) delete process.env[ENV_FLAG];
  else process.env[ENV_FLAG] = value;
  try {
    return fn();
  } finally {
    if (had) process.env[ENV_FLAG] = previous;
    else delete process.env[ENV_FLAG];
  }
}

test.beforeEach(() => { _resetHermesGatewayPoolForTests(); });
test.after(() => { _resetHermesGatewayPoolForTests(); });

test("an agent with no session gets a new one, and it is pooled", () => {
  const session = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
  assert.ok(session instanceof HermesManagedGatewaySession);
  assert.equal(session.agentId, "lc-hermes");
  assert.equal(__hermesGatewayPoolSize(), 1);
});

test("the SAME agent gets the SAME session back", () => {
  const first = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
  const second = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
  assert.equal(second, first, "a second child would give the bridge and the Console different sessions");
  assert.equal(__hermesGatewayPoolSize(), 1);
});

test("the agent id is trimmed, so whitespace does not fork the pool", () => {
  const first = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
  const second = getOrCreateHermesGatewaySession({ agentId: "  lc-hermes  ", agentInfo: {} });
  assert.equal(second, first);
  assert.equal(__hermesGatewayPoolSize(), 1);
});

test("two different agents get two different sessions", () => {
  const a = getOrCreateHermesGatewaySession({ agentId: "lc-a", agentInfo: {} });
  const b = getOrCreateHermesGatewaySession({ agentId: "lc-b", agentInfo: {} });
  assert.notEqual(a, b);
  assert.equal(__hermesGatewayPoolSize(), 2);
});

test("an empty agent id is refused rather than pooled under an empty key", () => {
  // One shared session under "" would be handed to every anonymous caller in turn.
  for (const agentId of [undefined, "", "   "]) {
    assert.throws(
      () => getOrCreateHermesGatewaySession({ agentId, agentInfo: {} }),
      /agentId required/,
      `agentId ${JSON.stringify(agentId)} was accepted`,
    );
  }
  assert.equal(__hermesGatewayPoolSize(), 0);
});

test("a STOPPED or FAILED session is replaced, not handed back", () => {
  for (const state of ["stopped", "failed"]) {
    _resetHermesGatewayPoolForTests();
    const dead = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
    dead._state = state;
    const fresh = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
    assert.notEqual(fresh, dead, `a ${state} session was handed back and will never answer`);
    assert.equal(fresh._state, "idle");
    assert.equal(__hermesGatewayPoolSize(), 1, "the dead one must not still hold a slot");
  }
});

test("every LIVE state is reused, including the ones mid-flight", () => {
  // `starting` is the one a reader drops: two callers arriving during startup must share the child
  // that is coming up, not race to spawn a second.
  for (const state of ["idle", "starting", "ready"]) {
    _resetHermesGatewayPoolForTests();
    const existing = getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} });
    existing._state = state;
    assert.equal(
      getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: {} }), existing,
      `a ${state} session was discarded`,
    );
  }
});

test("agentInfo from the first caller is the one the session keeps", () => {
  // The second caller's info is NOT applied — pinned as observed behaviour, because the alternative
  // reading (last writer wins) would silently repoint a live child at another workspace.
  const first = getOrCreateHermesGatewaySession({
    agentId: "lc-hermes", agentInfo: { cwd: "/first" },
  });
  getOrCreateHermesGatewaySession({ agentId: "lc-hermes", agentInfo: { cwd: "/second" } });
  assert.equal(first.agentInfo.cwd, "/first");
});

test("the reset helper stops what it evicts", () => {
  const stopped = [];
  __injectHermesGatewaySessionForTests("g1", { stop: () => { stopped.push("g1"); } });
  __injectHermesGatewaySessionForTests("g2", { stop: () => { stopped.push("g2"); } });
  assert.equal(__hermesGatewayPoolSize(), 2);
  _resetHermesGatewayPoolForTests();
  assert.deepEqual(stopped.sort(), ["g1", "g2"], "a cleared pool must not leak live children");
  assert.equal(__hermesGatewayPoolSize(), 0);
});

test("the reset helper survives a session whose stop throws", () => {
  // It runs in test teardown and between suites; one bad entry must not leave the rest pooled.
  __injectHermesGatewaySessionForTests("bad", { stop: () => { throw new Error("boom"); } });
  __injectHermesGatewaySessionForTests("good", { stop: () => {} });
  _resetHermesGatewayPoolForTests();
  assert.equal(__hermesGatewayPoolSize(), 0);
});

test("the gateway path is OFF unless the flag is exactly 1", () => {
  assert.equal(withEnv(undefined, managedHermesUsesGateway), false);
  for (const value of ["", "0", "true", "yes", "on", "2", "1 1", "01"]) {
    assert.equal(
      withEnv(value, managedHermesUsesGateway), false,
      `${JSON.stringify(value)} enabled a fallback path that is meant to be opt-in`,
    );
  }
});

test("the flag is honoured with surrounding whitespace", () => {
  for (const value of ["1", " 1", "1 ", "  1  "]) {
    assert.equal(withEnv(value, managedHermesUsesGateway), true, `${JSON.stringify(value)} was ignored`);
  }
});

test("reading the flag does not mutate the environment", () => {
  // A predicate that normalised in place would change what every later reader sees.
  withEnv("  1  ", () => {
    managedHermesUsesGateway();
    assert.equal(process.env[ENV_FLAG], "  1  ");
  });
  assert.equal(Object.prototype.hasOwnProperty.call(process.env, ENV_FLAG), false,
    "the seal must leave the environment as it found it");
});
