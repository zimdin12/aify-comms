// Whether any runtime controller is mid-turn — the turn-busy heartbeat's only input.
//
// The heartbeat POSTs `turn_busy=1` every 30s while this reports active, which is what keeps a long turn
// reading as `working` instead of flapping to `online` between hook events. That flapping is an
// operator-reported failure this mechanism exists to fix, and until v0.5.4 the mechanism lived in
// `server.js`, the bin entry point, which nothing imports — so it had no test.

import assert from "node:assert/strict";
import test from "node:test";
import { isUsedInBridge } from "./bridge-sources.mjs";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { __markControllerStart, anyControllerActive } from "../controller-activity.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const settled = () => new Promise((resolve) => setImmediate(resolve));

test("nothing is active before anything starts", () => {
  assert.equal(anyControllerActive(), false);
});

test("a marked controller reads active, and stops reading active once it resolves", async () => {
  let finish;
  const promise = new Promise((resolve) => { finish = resolve; });
  __markControllerStart(promise);
  assert.equal(anyControllerActive(), true, "a running turn must read as active");

  finish();
  await promise;
  await settled();
  assert.equal(anyControllerActive(), false, "a finished turn must stop holding the heartbeat open");
});

test("A REJECTED turn also stops reading active — the leak that would pin an agent at 'working'", async () => {
  // The property worth the most here. Cleanup is attached as `.then(cleanup, cleanup)`, on BOTH settle
  // paths. A single-argument `.then(cleanup)` would leak on rejection only — so the bug would appear
  // exclusively for turns that FAILED, leaving the heartbeat asserting work forever with nothing able to
  // clear it. That is the "always working" symptom this repo has already paid for once.
  const promise = Promise.reject(new Error("turn blew up"));
  __markControllerStart(promise);
  assert.equal(anyControllerActive(), true, "a failing turn is still a running turn while it runs");

  await promise.catch(() => {});
  await settled();
  assert.equal(anyControllerActive(), false, "a REJECTED turn must release the heartbeat too");
});

test("two overlapping turns both have to finish before it goes quiet", async () => {
  // Tested because the naive implementation is a boolean, and a boolean cannot represent this: the first
  // turn to finish would clear the flag while the second was still running, and the agent would drop to
  // `online` mid-work.
  let finishA, finishB;
  const a = new Promise((r) => { finishA = r; });
  const b = new Promise((r) => { finishB = r; });
  __markControllerStart(a);
  __markControllerStart(b);
  assert.equal(anyControllerActive(), true);

  finishA(); await a; await settled();
  assert.equal(anyControllerActive(), true, "one of two finishing must NOT clear the heartbeat");

  finishB(); await b; await settled();
  assert.equal(anyControllerActive(), false, "both finished, now quiet");
});

test("marking the same promise twice does not require two settles to clear", async () => {
  // A Set keyed on promise identity, so a duplicate mark is one entry. If it were an array or a counter,
  // a double-mark would leave a residue that never drains.
  let finish;
  const promise = new Promise((r) => { finish = r; });
  __markControllerStart(promise);
  __markControllerStart(promise);
  finish(); await promise; await settled();
  assert.equal(anyControllerActive(), false, "a double-marked turn must still clear on one settle");
});

test("a non-promise is returned untouched and tracked as nothing", () => {
  // Callers mark-and-forward in one expression, so the return value matters. And a controller that failed
  // to produce a promise must not register as an eternal turn — which is exactly what adding a
  // non-thenable to the set would do, since nothing would ever settle to remove it.
  for (const value of [undefined, null, 0, "", "not-a-promise", {}, { then: 42 }]) {
    assert.equal(__markControllerStart(value), value, "the input must be returned as-is");
  }
  assert.equal(anyControllerActive(), false, "none of those may register as a running turn");
});

test("the promise set is PRIVATE — every caller goes through the two functions", () => {
  // The design point. An entry added from outside would carry no cleanup and never be removed, pinning
  // the heartbeat permanently. Compare bridge-agent-state.mjs, which does export its Maps because thirty
  // readers mutate them directly; exporting mutable state is a fallback, not the default.
  const src = readFileSync(path.join(STDIO, "controller-activity.mjs"), "utf-8");
  assert.doesNotMatch(src, /^export const ACTIVE_CONTROLLER_PROMISES/m, "the set must not be exported");
  assert.equal((src.match(/^export function /gm) || []).length, 2, "exactly two operations");
  assert.ok(!/^import\s/m.test(src), "tracking promises needs no dependencies");

  // Cleanup on both settle paths, asserted on source because the rejection case above proves the
  // behaviour but not that it is deliberate rather than incidental.
  assert.match(src, /promise\.then\(cleanup, cleanup\)/, "cleanup must be attached to BOTH settle paths");
});

test("THE BRIDGE delegates and keeps no copy", () => {
  const src = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(src, /^const ACTIVE_CONTROLLER_PROMISES = new Set\(\);$/m, "no second set");
  assert.doesNotMatch(src, /^function __markControllerStart\b/m, "must be imported, not redeclared");
  // BRIDGE-WIDE. The caller moved to `dispatch-loop.mjs` with the dispatch pass in v0.5.4 and this
  // went red on a pure relocation — the intent was always about the BRIDGE, and naming the file the
  // call happened to sit in is what made it break on a move.
  assert.equal(isUsedInBridge("__markControllerStart"), true,
    "the bridge must still mark controller starts somewhere");
  // The heartbeat's isActive now asks the owner instead of measuring the collection itself. That
  // substitution is the one non-byte-identical change in the slice.
  assert.match(src, /isActive: \(\) => anyControllerActive\(\)/, "the heartbeat must read through the predicate");
});
