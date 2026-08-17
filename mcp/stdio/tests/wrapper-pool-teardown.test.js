// `disposeAll` — the whole-pool teardown, which disposed nothing.
//
// SEPARATE FROM `wrapper-pool.test.js` on purpose. That file is script-style: top-level assertions
// that abort at the first failure and report a line number. It covers every other export of the pool.
// This one is `node:test` because it is about a named defect and each case should say which property
// broke, not just where.
//
// THE DEFECT. `disposeAll` collected the pool's KEYS, called `POOL.clear()`, then looked each key up
// again in the map it had just emptied. Every `POOL.get(key)` returned undefined and the optional
// chain swallowed it, so the pool was emptied and not one wrapper was told to shut down. Each pooled
// entry owns a live child process — `omp --mode rpc`, a codex app-server, the opencode SDK, a hermes
// runtime — so the effect was an orphaned process per pooled agent, with the map that tracked them
// gone.
//
// It was found because the export ratchet listed it as named by no test. Its sibling `disposeWrapper`
// captures the entry BEFORE deleting and always worked, which is what made the difference visible.
//
// AND IT HAS NO CALLERS. Nothing in the bridge invokes whole-pool teardown, so the fix changes no
// behaviour today — the tests below describe what it will do the first time something does call it.

import assert from "node:assert/strict";
import test from "node:test";

import {
  _resetPoolForTests,
  disposeAll,
  ensureWrapper,
  getWrapper,
  listPooledAgents,
} from "../wrapper-pool.js";

function makeHandle({ id = "h" } = {}) {
  const state = { disposed: 0, alive: true };
  return {
    id,
    state,
    capabilities: [],
    dispatch: async () => ({ status: "completed" }),
    interrupt: async () => {},
    steer: async () => {},
    dispose: async () => {
      state.disposed += 1;
      state.alive = false;
    },
    alive: () => state.alive,
    onExit: () => {},
  };
}

async function pool(handles) {
  _resetPoolForTests();
  for (const [agentId, runtime, handle] of handles) {
    await ensureWrapper({ agentId, runtime, factory: async () => handle });
  }
}

test("disposeAll DISPOSES every pooled wrapper", async () => {
  // The assertion the function was named for and did not meet. Without it each of these handles owns
  // a child process that outlives the bridge's own record of it.
  const a = makeHandle({ id: "a" });
  const b = makeHandle({ id: "b" });
  await pool([["agent-a", "pi", a], ["agent-b", "codex", b]]);

  await disposeAll();

  assert.equal(a.state.disposed, 1, "the first pooled wrapper was never disposed");
  assert.equal(b.state.disposed, 1, "the second pooled wrapper was never disposed");
});

test("it also EMPTIES the pool", async () => {
  const a = makeHandle({ id: "a" });
  await pool([["agent-a", "pi", a]]);

  await disposeAll();

  assert.deepEqual(listPooledAgents(), []);
  assert.equal(getWrapper("agent-a", "pi"), null);
});

test("the pool is emptied BEFORE the handles are disposed", async () => {
  // Deliberate ordering, and the reason the original looked plausible: clearing first closes the
  // window where a concurrent `ensureWrapper` could be handed a handle that is already disposing.
  // The bug was the lookup afterwards, not the clear.
  const observed = [];
  const handle = makeHandle({ id: "a" });
  handle.dispose = async () => {
    observed.push(listPooledAgents().length);
  };
  await pool([["agent-a", "pi", handle]]);

  await disposeAll();

  assert.deepEqual(observed, [0], "the pool still held entries while a handle was disposing");
});

test("one handle that THROWS does not stop the others", async () => {
  // Teardown is best-effort by design: a wrapper whose process already died will reject, and that
  // must not leave the rest of the fleet's children running.
  const bad = makeHandle({ id: "bad" });
  bad.dispose = async () => { throw new Error("already gone"); };
  const good = makeHandle({ id: "good" });
  await pool([["agent-bad", "pi", bad], ["agent-good", "codex", good]]);

  await disposeAll();

  assert.equal(good.state.disposed, 1, "a throwing sibling prevented a good handle's dispose");
  assert.deepEqual(listPooledAgents(), []);
});

test("a handle with NO dispose method is survived", async () => {
  // The optional chain is load-bearing here rather than hiding the bug: a factory may return a
  // handle that has nothing to tear down, and that must not throw out of teardown.
  const bare = { alive: () => true, onExit: () => {} };
  await pool([["agent-bare", "pi", bare]]);

  await disposeAll();

  assert.deepEqual(listPooledAgents(), []);
});

test("disposing an EMPTY pool is a no-op, not an error", async () => {
  _resetPoolForTests();
  await disposeAll();
  assert.deepEqual(listPooledAgents(), []);
});

test("a second disposeAll does not dispose the same handle twice", async () => {
  // Shutdown paths get called more than once. A second pass over a cleared pool must find nothing
  // rather than re-dispose whatever it remembered.
  const a = makeHandle({ id: "a" });
  await pool([["agent-a", "pi", a]]);

  await disposeAll();
  await disposeAll();

  assert.equal(a.state.disposed, 1);
});

test("disposeAll WAITS for the disposals it started", async () => {
  // It is awaited by a caller that is about to exit the process. Returning before the children have
  // been told to stop would make the await meaningless — the orphaned-process outcome again, by a
  // different route.
  let resolveDispose;
  let finished = false;
  const slow = makeHandle({ id: "slow" });
  slow.dispose = () => new Promise((resolve) => {
    resolveDispose = () => { finished = true; resolve(); };
  });
  await pool([["agent-slow", "pi", slow]]);

  const pending = disposeAll();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(finished, false, "precondition: the disposal has not completed yet");

  resolveDispose();
  await pending;
  assert.equal(finished, true, "disposeAll resolved before its disposals did");
});

test("wrappers added AFTER the clear are left alone", async () => {
  // The window the clear-first ordering protects: a dispatch that arrives mid-teardown creates a
  // fresh wrapper, and that one belongs to the new state, not to the shutdown that already read the
  // pool.
  const survivor = makeHandle({ id: "survivor" });
  const handle = makeHandle({ id: "a" });
  handle.dispose = async () => {
    await ensureWrapper({ agentId: "agent-new", runtime: "pi", factory: async () => survivor });
  };
  await pool([["agent-a", "pi", handle]]);

  await disposeAll();

  assert.equal(survivor.state.disposed, 0, "a wrapper created during teardown was disposed");
  assert.equal(getWrapper("agent-new", "pi")?.handle, survivor);
  _resetPoolForTests();
});
