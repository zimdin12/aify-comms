// The pi session pool, tested by CALLING it — and specifically the one property that a refactor could
// break without any test noticing.
//
// `pi-session-pool.mjs` registers and evicts pooled children; `PiSession.stop()` / `_teardownChild()`
// de-register the session itself ("remove me if I am still the current entry"). Those two live in
// different modules and MUST address the same Map, which is why the Map has its own owner module
// (`pi-session-registry.mjs`) that both import.
//
// IF THERE WERE TWO MAPS, EVERY ASSERTION EXCEPT ONE HERE WOULD STILL PASS. The pool would keep
// handing out a session the class had already torn down — a leaked child process per turn, showing up
// only as pi agents that never die. `stop() evicts from the map the pool reads` is the assertion that
// fails, and it is the reason the registry module exists.
//
// Nothing here starts a child. `ensureStarted` is never called, so `stop()` takes its `_proc === null`
// path: synchronous, no spawn, no timers left armed.

import assert from "node:assert/strict";
import test from "node:test";

// Sealed BEFORE the imports below, because `PiSession`'s constructor resolves its idle timeout at
// construction time from these. An operator value in the ambient environment would otherwise change
// what this test constructs.
const SEALED = ["AIFY_PI_IDLE_TIMEOUT_MS", "AIFY_PI_STARTUP_TIMEOUT_MS"];
for (const name of SEALED) delete process.env[name];

const { piSessionPool } = await import("../pi-session-registry.mjs");
const { PiSession } = await import("../pi-session.js");
const {
  acquirePiSession,
  getPiSession,
  shutdownAllPiSessions,
  __piSessionPoolSize,
  __piSessionPoolEntriesForTests,
} = await import("../pi-session-pool.mjs");

function assertSealed() {
  for (const name of SEALED) {
    assert.equal(process.env[name], undefined, `${name} must stay unset for this test to mean anything`);
  }
}

// `agentInfo: {}` is not incidental — see the CURRENT DEFECT case at the bottom of this file. Both
// production callers pass an object, so this constructs sessions the way the running bridge does.
function freshSession(agentId) {
  return new PiSession({ agentId, agentInfo: {} });
}

test.beforeEach(() => {
  assertSealed();
  piSessionPool.clear();
});

test("the pool's lookups and the registry are ONE map, in both directions", () => {
  const session = freshSession("agent-a");

  // registry -> pool
  piSessionPool.set("agent-a", session);
  assert.equal(getPiSession("agent-a"), session, "getPiSession must see what the registry holds");
  assert.equal(__piSessionPoolSize(), 1);
  assert.deepEqual(__piSessionPoolEntriesForTests(), [session]);

  // pool -> registry
  piSessionPool.delete("agent-a");
  assert.equal(getPiSession("agent-a"), null);
  assert.equal(piSessionPool.size, 0);
});

test("stop() evicts the session from the map the POOL reads", async () => {
  // The load-bearing case. Two Maps would leave `getPiSession` returning a dead session forever.
  const session = freshSession("agent-b");
  piSessionPool.set("agent-b", session);
  assert.equal(getPiSession("agent-b"), session);

  await session.stop("test");

  assert.equal(getPiSession("agent-b"), null, "a stopped session must not be handed out again");
  assert.equal(__piSessionPoolSize(), 0);
  assert.equal(session._state, "dead");
});

test("stop() on a SUPERSEDED session leaves the live entry alone", async () => {
  // Anti-vacuity for the eviction above: `stop()` deletes only when the pooled entry is still `this`.
  // A blanket `delete(agentId)` would pass the previous test and silently unpool the live child when a
  // superseded predecessor finally stopped.
  const live = freshSession("agent-c");
  const superseded = freshSession("agent-c");
  piSessionPool.set("agent-c", live);

  await superseded.stop("test");

  assert.equal(getPiSession("agent-c"), live, "the live session must survive its predecessor stopping");
  assert.equal(__piSessionPoolSize(), 1);
});

test("getPiSession is null-safe for every id shape that is not a pooled key", () => {
  assert.equal(getPiSession(""), null);
  assert.equal(getPiSession("   "), null);
  assert.equal(getPiSession(null), null);
  assert.equal(getPiSession(undefined), null);
  assert.equal(getPiSession("never-pooled"), null);
});

test("getPiSession trims, so a padded id finds the same session", () => {
  const session = freshSession("agent-d");
  piSessionPool.set("agent-d", session);
  assert.equal(getPiSession("  agent-d  "), session);
});

test("acquirePiSession refuses a blank agentId instead of pooling under an empty key", async () => {
  // A blank key would collapse every anonymous caller onto one shared child.
  for (const bad of [undefined, null, "", "   "]) {
    await assert.rejects(
      () => acquirePiSession({ agentId: bad }),
      /requires an agentId/,
      `agentId ${JSON.stringify(bad)} must be refused`,
    );
  }
  assert.equal(__piSessionPoolSize(), 0, "nothing may be pooled by a refused acquire");
});

test("shutdownAllPiSessions drains the pool and stops every session in it", async () => {
  const a = freshSession("agent-e");
  const b = freshSession("agent-f");
  piSessionPool.set("agent-e", a);
  piSessionPool.set("agent-f", b);

  await shutdownAllPiSessions("test-shutdown");

  assert.equal(__piSessionPoolSize(), 0, "the pool must be empty after shutdown");
  assert.equal(a._state, "dead", "every pooled session must have been stopped, not just dropped");
  assert.equal(b._state, "dead");
});

test("shutdownAllPiSessions clears the pool even when a session's stop() throws", async () => {
  // One bad session must not strand the rest of the fleet — `shutdownAllPiSessions` catches per
  // session, and this pins that the catch is real rather than incidental.
  const boom = freshSession("agent-g");
  boom.stop = async () => { throw new Error("stop exploded"); };
  const ok = freshSession("agent-h");
  piSessionPool.set("agent-g", boom);
  piSessionPool.set("agent-h", ok);

  await shutdownAllPiSessions("test-shutdown");

  assert.equal(__piSessionPoolSize(), 0);
  assert.equal(ok._state, "dead", "the healthy session must still have been stopped");
});

test("CURRENT DEFECT: the constructor's agentInfo default does not cover its own timeout lookup", () => {
  // Pinned, not fixed, because this slice is a relocation and a behaviour change does not belong in it.
  //
  // `PiSession`'s constructor writes `this.agentInfo = agentInfo || {}` and then calls
  // `idleTimeoutFor(agentInfo)` with the RAW value — so the default it just established does not apply
  // to its own next line. `idleTimeoutFor` reaches `getRuntimeConfig`, which is
  // `agentInfo.runtimeConfig || {}` with no guard on `agentInfo` itself, and the construction throws.
  //
  // NOT REACHABLE TODAY, and that is why it is a pin rather than a fix: `server.js` passes
  // `state.info || {}` and `pi-controller.js` dereferences `agentInfo.cwd` before it ever gets here, so
  // both production callers already hand over an object. The safety is entirely caller discipline, and
  // nothing states that — a third caller written to the constructor's apparent signature would crash.
  assert.throws(() => new PiSession({ agentId: "no-info" }), /runtimeConfig/);
  assert.equal(__piSessionPoolSize(), 0, "a failed construction must not leave anything pooled");
});

console.log("pi-session-pool.test.js: all assertions passed");
