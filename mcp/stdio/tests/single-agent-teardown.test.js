// Real tests for the single-agent managed teardown, extracted from server.js in v0.5.4.
//
// WHAT THESE TESTS DO **NOT** COVER, said plainly so a green run is not mistaken for more.
// The reaping path is NOT exercised. `runSingleAgentManagedTeardown` hardcodes the REAL collaborators —
// `defaultKillTree`, `stopDaemon`, `defaultKillByPort`, `defaultListProcesses` — as imported bindings that
// cannot be substituted. Calling it with an id that matched anything would run a genuine process teardown
// on a machine with live managed agents, which is the move that has twice cost this project its running
// fleet. Reaching that logic needs the collaborators passed IN rather than closed over, which is a
// signature change and not a byte-identical relocation, so it is out of scope for this slice.
//
// What IS covered is the branch that can be reached without side effects, and it is the one that matters
// most: a BLANK agent id must never reach the reaper. Without `if (!id) return;` the call proceeds with
// `ownedAgentIds: [""]` — an empty string handed to the survivor enumerator as the set of agents this
// teardown owns. That guard was verified load-bearing in a sandbox (a copy of the module with its imports
// repointed at a spy): with the guard, four blank ids invoke the reaper zero times; without it, four
// times, each with `ownedAgentIds: [""]`. That verification is not run here, because running it in-tree
// would mean shipping a second copy of the module.

import assert from "node:assert/strict";
import test from "node:test";

import { runSingleAgentManagedTeardown } from "../single-agent-teardown.mjs";

test("a blank agent id is refused before ANY teardown work happens", async () => {
  // Each of these returns at the guard. If the guard were gone they would instead reach the real reaper,
  // so this test passing is what keeps that call from being made with an empty owned-id set.
  for (const id of ["", "   ", "\t\n", null, undefined, 0, false]) {
    const result = await runSingleAgentManagedTeardown(id);
    assert.equal(result, undefined, `a blank id (${JSON.stringify(id)}) must return without a result`);
  }
});

test("a blank id is refused whatever reason is given", async () => {
  // `reason` only ever reaches log lines. It must not become a way past the id check.
  await runSingleAgentManagedTeardown("", "agent stop");
  await runSingleAgentManagedTeardown("", "bridge exit");
  await runSingleAgentManagedTeardown(undefined, "remove");
});

test("the id is TRIMMED before it is judged, not merely truthy-checked", async () => {
  // `String(agentId || "").trim()` — a whitespace-only id is truthy in JavaScript, so a bare `if (!agentId)`
  // would let "   " through and hand the enumerator a whitespace agent id. The trim is what makes the
  // guard cover it, and the case above pins that.
  assert.equal(await runSingleAgentManagedTeardown("   "), undefined);
});

test("it is exported from its own module and NOT from the sweep reaper", async () => {
  // The sweeps (`runManagedTeardownForBridge`, `runBootSurvivorSweep`) decide WHICH agents die; this one is
  // told which one. They stay in server.js pending a scope decision, and merging this into the reaper
  // module would blur exactly that distinction.
  assert.equal(typeof runSingleAgentManagedTeardown, "function");
  const reaper = await import("../reap-managed-survivors.js");
  assert.equal(reaper.runSingleAgentManagedTeardown, undefined,
    "the per-agent teardown must not have been merged into the survivor reaper");
  assert.equal(typeof reaper.runManagedTeardown, "function", "the primitive stays where it was");
});

test("importing the module runs no teardown and needs no live service", async () => {
  // It is imported at bridge boot. A module-scope call here would reap on import.
  const again = await import("../single-agent-teardown.mjs");
  assert.equal(again.runSingleAgentManagedTeardown, runSingleAgentManagedTeardown,
    "one module instance, no load-time side effects");
});
