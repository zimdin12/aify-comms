#!/usr/bin/env node
// Pool semantics: ensure-or-create-once per (agentId, runtime), reuse
// while alive, drop+respawn after dispose or after the handle reports
// alive() === false, and only invoke factory on actual misses.
import assert from "node:assert/strict";
import {
  ensureWrapper,
  getWrapper,
  disposeWrapper,
  listPooledAgents,
  _resetPoolForTests,
} from "../wrapper-pool.js";

function makeHandle({ id = "h", alive = true } = {}) {
  let _alive = alive;
  const exitListeners = [];
  return {
    id,
    capabilities: [],
    dispatch: async () => ({ status: "completed" }),
    interrupt: async () => {},
    steer: async () => {},
    dispose: async () => {
      _alive = false;
      for (const fn of exitListeners) fn();
    },
    alive: () => _alive,
    onExit: (fn) => exitListeners.push(fn),
  };
}

// --- reuse while alive ---
_resetPoolForTests();
let factoryCalls = 0;
const factory1 = async () => {
  factoryCalls++;
  return makeHandle({ id: "wrapper-1" });
};
const h1 = await ensureWrapper({ agentId: "a", runtime: "pi", factory: factory1 });
const h2 = await ensureWrapper({ agentId: "a", runtime: "pi", factory: factory1 });
assert.equal(h1, h2, "second ensureWrapper for same (agentId,runtime) must return the SAME handle");
assert.equal(factoryCalls, 1, "factory must NOT be called twice when handle is alive");

// --- different runtime gets its own slot ---
const h3 = await ensureWrapper({ agentId: "a", runtime: "codex", factory: async () => makeHandle({ id: "wrapper-3" }) });
assert.notEqual(h1, h3, "different runtime must get a fresh handle");

// --- getWrapper returns the entry ---
const entry = getWrapper("a", "pi");
assert.ok(entry, "getWrapper must return entry for live agent");
assert.equal(entry.handle, h1);

// --- dispose drops the entry and a subsequent ensureWrapper respawns ---
await disposeWrapper("a", "pi");
assert.equal(getWrapper("a", "pi"), null, "disposeWrapper must remove the entry");
const h4 = await ensureWrapper({ agentId: "a", runtime: "pi", factory: factory1 });
assert.notEqual(h4, h1, "ensureWrapper after dispose must call factory again");
assert.equal(factoryCalls, 2, "factory call count reflects respawn");

// --- dead handle is auto-evicted on lookup ---
const deadHandle = makeHandle({ id: "wrapper-dead", alive: false });
_resetPoolForTests();
let respawns = 0;
const factoryDead = async () => {
  respawns++;
  return respawns === 1 ? deadHandle : makeHandle({ id: `wrapper-respawn-${respawns}` });
};
await ensureWrapper({ agentId: "b", runtime: "pi", factory: factoryDead });
assert.equal(getWrapper("b", "pi"), null, "getWrapper must return null when stored handle reports alive=false");
const h5 = await ensureWrapper({ agentId: "b", runtime: "pi", factory: factoryDead });
assert.equal(h5.id, "wrapper-respawn-2", "ensureWrapper must respawn when prior handle is dead");

// --- onExit triggers pool eviction ---
_resetPoolForTests();
const exiter = makeHandle({ id: "wrapper-exiter" });
await ensureWrapper({ agentId: "c", runtime: "pi", factory: async () => exiter });
assert.ok(getWrapper("c", "pi"), "entry present before exit");
await exiter.dispose(); // triggers onExit listener registered by the pool
assert.equal(getWrapper("c", "pi"), null, "onExit must evict the pool entry");

// --- listPooledAgents shape ---
_resetPoolForTests();
await ensureWrapper({ agentId: "x", runtime: "pi", factory: async () => makeHandle() });
await ensureWrapper({ agentId: "y", runtime: "codex", factory: async () => makeHandle() });
const listed = listPooledAgents();
assert.equal(listed.length, 2, "listPooledAgents lists current entries");
assert.deepEqual(
  listed.map((e) => e.key).sort(),
  ["x::pi", "y::codex"].sort(),
);

console.log("wrapper-pool.test.js: all assertions passed");
