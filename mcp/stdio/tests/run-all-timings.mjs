// The previous run's per-file durations, used only to ORDER the pool.
//
// WHY ORDER MATTERS MORE THAN IT SOUNDS. The distribution is extreme -- measured 2026-09-02, the 15
// slowest of 412 files held 64.6% of the wall time, the worst single file 90 seconds. A pool that
// happens to start that file last leaves five workers idle waiting for it, and the run is no faster
// than its longest tail. Longest-first is the standard remedy and it needs no scheduler, only last
// run's numbers.
//
// IT IS AN OPTIMISATION, NEVER AN INPUT TO CORRECTNESS. Missing, unreadable or stale timings order
// the queue alphabetically and everything still runs; a file that has never been seen sorts first,
// because an unknown cost is more safely assumed large than small. Nothing here may throw: a cache
// that breaks a test run is worse than no cache.

import { readFileSync, writeFileSync } from "node:fs";

/** Durations from the last run, or an empty map. Never throws. */
export function readTimings(path) {
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    // `typeof [] === "object"`, so the obvious guard accepts an ARRAY -- caught by this module's own
    // test. Harmless in effect (every lookup misses and the order falls back to alphabetical) and
    // exactly the shape of guard this repo keeps finding: one that passes on an input it was written
    // to reject, and reports nothing.
    const isMap = parsed !== null && typeof parsed === "object" && !Array.isArray(parsed);
    return isMap ? parsed : {};
  } catch {
    return {};
  }
}

/** Record this run's durations for the next one. Never throws. */
export function writeTimings(path, results) {
  try {
    const timings = {};
    for (const result of results) {
      if (result && result.file && Number.isFinite(result.ms)) timings[result.file] = result.ms;
    }
    writeFileSync(path, JSON.stringify(timings, null, 0));
  } catch { /* a cache that cannot be written must not fail the run */ }
}

/**
 * The files, slowest first, with never-seen files ahead of everything.
 *
 * PURE, and the reason it is a function rather than three lines inline: the ordering is the whole
 * value of the cache, and a comparator that quietly sorted fastest-first would still produce a green
 * run -- just a slow one nobody could attribute.
 */
export function orderLongestFirst(files, timings = {}) {
  const cost = (file) => (Number.isFinite(timings[file]) ? timings[file] : Number.POSITIVE_INFINITY);
  return [...files].sort((a, b) => (cost(b) - cost(a)) || a.localeCompare(b));
}
