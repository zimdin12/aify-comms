// The pool's ordering, which is invisible when it is wrong.
//
// WHY THIS IS TESTED AT ALL. A comparator sorting the wrong way still produces a GREEN run -- just a
// slow one, and slowness gets blamed on the machine. Measured 2026-09-02: the 15 slowest of 412
// files held 64.6% of the wall time and the worst single file took 90 seconds, so a pool that starts
// that file last leaves five workers idle waiting for it and the parallelism buys almost nothing.
// The ordering IS the win; nothing else observes it.
//
// And the cache must never be able to fail a run: a broken timings file is worth strictly less than
// the tests it would stop.

import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { orderLongestFirst, readTimings, writeTimings } from "../tests/run-all-timings.mjs";

const FILES = ["a.test.js", "b.test.js", "c.test.js"];

test("the slowest file is started FIRST, which is the whole point of the cache", () => {
  const order = orderLongestFirst(FILES, { "a.test.js": 100, "b.test.js": 90_000, "c.test.js": 500 });
  assert.deepEqual(order, ["b.test.js", "c.test.js", "a.test.js"]);
});

test("a file with NO timing sorts first, because unknown is safer treated as expensive", () => {
  // A new file assumed cheap and started last is the exact stall this ordering exists to prevent,
  // and a new file is the one most likely to be slow.
  const order = orderLongestFirst(FILES, { "a.test.js": 100, "c.test.js": 500 });
  assert.equal(order[0], "b.test.js");
});

test("with no timings at all the order is stable and alphabetical", () => {
  // The pool must work on a fresh checkout. Ordering is an optimisation; running everything is not.
  assert.deepEqual(orderLongestFirst(FILES, {}), FILES);
  assert.deepEqual(orderLongestFirst(FILES), FILES);
});

test("the input list is not mutated, so the caller's `files` stays the discovered set", () => {
  const files = [...FILES];
  orderLongestFirst(files, { "c.test.js": 9 });
  assert.deepEqual(files, FILES);
});

test("EVERY file survives the ordering — a comparator may not lose one", () => {
  // The failure that would be hardest to see: a dropped file means a suite silently stops running
  // and the summary's own count moves, which nobody reads against a target.
  const many = Array.from({ length: 50 }, (_, i) => `f${i}.test.js`);
  const timings = Object.fromEntries(many.map((f, i) => [f, i % 7]));
  assert.deepEqual([...orderLongestFirst(many, timings)].sort(), [...many].sort());
});

test("an unreadable or missing cache reads as EMPTY rather than throwing", () => {
  const dir = mkdtempSync(join(tmpdir(), "aify-timings-"));
  assert.deepEqual(readTimings(join(dir, "absent.json")), {});
  const broken = join(dir, "broken.json");
  writeFileSync(broken, "{not json");
  assert.deepEqual(readTimings(broken), {});
  const wrongShape = join(dir, "array.json");
  writeFileSync(wrongShape, "[1,2,3]");
  assert.deepEqual(readTimings(wrongShape), {}, "an array is not a timings map");
});

test("a round trip preserves the durations, and skips entries with none", () => {
  const dir = mkdtempSync(join(tmpdir(), "aify-timings-"));
  const path = join(dir, "t.json");
  writeTimings(path, [
    { file: "a.test.js", ms: 120 },
    { file: "b.test.js", ms: undefined },
    { file: "", ms: 5 },
    null,
  ]);
  assert.deepEqual(readTimings(path), { "a.test.js": 120 });
});

test("writing to a path that cannot exist does NOT throw", () => {
  // A cache that fails a run is worth less than no cache. This is the guard, not a hypothetical.
  writeTimings(join(tmpdir(), "aify-no-such-dir-" + Date.now(), "deep", "t.json"), [{ file: "a", ms: 1 }]);
});
