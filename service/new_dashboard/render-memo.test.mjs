// The render memo, tested by CALLING it.
//
// It lived in app.js and was unreachable. The dashboard polls, so every section is asked to re-render on
// a timer whether or not its data moved — and rendering anyway is not merely wasteful: it destroys and
// rebuilds DOM under an operator who may be mid-selection, mid-scroll, or holding a dropdown open. This
// is the guard that stops that, and all of its correctness is in the signature comparison.

import assert from "node:assert/strict";
import test from "node:test";

import { renderSection } from "./render-memo.mjs";

/** Distinct keys per test — the signature store is module-global and shared across this file. */
let n = 0;
const key = () => `k${n += 1}`;

test("the FIRST call always renders", () => {
  const k = key();
  let renders = 0;
  renderSection(k, ["a"], () => { renders += 1; });
  assert.equal(renders, 1);
});

test("AN UNCHANGED SIGNATURE SKIPS THE RENDER", () => {
  // The whole point. Without it every poll rebuilds the section's DOM.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", 1], render);
  renderSection(k, ["a", 1], render);
  renderSection(k, ["a", 1], render);
  assert.equal(renders, 1, "only the first call may render");
});

test("a CHANGED signature renders again", () => {
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a"], render);
  renderSection(k, ["b"], render);
  assert.equal(renders, 2);
});

test("signatures are compared by VALUE, not identity", () => {
  // `JSON.stringify`. A reference comparison would re-render on every poll, since callers build a fresh
  // array each time — which is exactly the bug this memo exists to prevent, hiding as a working memo.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", { b: 1 }], render);
  renderSection(k, ["a", { b: 1 }], render);
  assert.equal(renders, 1, "an equal-but-not-identical signature must not re-render");
});

test("ORDER IS PART OF THE SIGNATURE", () => {
  // Stringified arrays are order-sensitive, so a reordered list counts as a change. Pinned because it
  // means callers must build their signature deterministically or the memo never holds.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, ["a", "b"], render);
  renderSection(k, ["b", "a"], render);
  assert.equal(renders, 2);
});

test("each KEY is memoised independently", () => {
  // One store for every section. Sharing a slot would make two sections alternately blank each other.
  const a = key();
  const b = key();
  let ra = 0;
  let rb = 0;
  renderSection(a, ["x"], () => { ra += 1; });
  renderSection(b, ["x"], () => { rb += 1; });
  assert.deepEqual([ra, rb], [1, 1], "the same signature under a different key still renders");
  renderSection(a, ["x"], () => { ra += 1; });
  assert.equal(ra, 1);
});

test("undefined and null signatures are distinguishable from each other", () => {
  // `JSON.stringify(undefined)` is undefined and `JSON.stringify(null)` is "null" — different values, so
  // a section swinging between them re-renders rather than sticking on whichever came first.
  const k = key();
  let renders = 0;
  const render = () => { renders += 1; };
  renderSection(k, null, render);
  renderSection(k, undefined, render);
  assert.equal(renders, 2);
});

test("the signature is recorded BEFORE the render runs", () => {
  // Order matters for re-entrancy: a render that triggers another render of the same section would
  // otherwise recurse. Provoked directly here rather than reasoned about.
  const k = key();
  let renders = 0;
  const render = () => {
    renders += 1;
    if (renders < 5) renderSection(k, ["same"], render);
  };
  assert.doesNotThrow(() => renderSection(k, ["same"], render));
  assert.equal(renders, 1, "the re-entrant call must be memoised out");
});
