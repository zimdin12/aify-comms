// What the suite runner is allowed to call a pass.
//
// run-all.mjs judged every file by EXIT STATUS alone and printed "all N suite(s) passed". A file whose
// tests all SKIPPED exits 0, so it read as passed while verifying nothing. That is not hypothetical
// here: delegated-terminal-against-real-aify-env.test.js is the standing proof that Phase 8's seam
// reaches a real environment tier, and it skips itself when the aify-env checkout is absent. On any
// machine but this one, the cross-repo proof ran zero tests and the runner said everything passed.
//
// This project has now produced that shape four times -- doctor's env-bridge counting registered rows,
// bridge-current green-by-default, unknown-all, and this. A run that gathered no evidence must not
// read like a run that gathered some.
import assert from "node:assert/strict";
import { test } from "node:test";

import { skippedFrom, slowest, summarise } from "./run-all-summary.mjs";

const NL = String.fromCharCode(10);

test("a skip count is read out of a node:test run", () => {
  const output = ["# tests 2", "# pass 0", "# fail 0", "# skipped 2"].join(NL);
  assert.equal(skippedFrom(output), 2);
});

test("a run with nothing skipped reads as zero, not as unknown", () => {
  assert.equal(skippedFrom(["# tests 5", "# pass 5", "# fail 0", "# skipped 0"].join(NL)), 0);
});

test("output that carries no summary at all reads as zero rather than throwing", () => {
  // The 109 files that use plain top-level assertions print no TAP summary. They are not skipping;
  // they simply do not report in this shape, and treating that as a skip would cry wolf on a third
  // of the suite.
  assert.equal(skippedFrom("all assertions passed"), 0);
  assert.equal(skippedFrom(""), 0);
});

test("a skipped file is named in the summary, not folded into the pass count", () => {
  const result = summarise([
    { file: "a.test.js", status: 0, skipped: 0 },
    { file: "b.test.js", status: 0, skipped: 2 },
    { file: "c.test.js", status: 0, skipped: 0 },
  ]);
  assert.equal(result.passed, 3);
  assert.deepEqual(result.failed, []);
  assert.deepEqual(result.skipped, [{ file: "b.test.js", skipped: 2 }]);
  assert.match(result.line, /3 suite\(s\) passed/);
  assert.match(result.line, /2 test\(s\) skipped in 1 file/,
    "the count must be visible in the one line a reader actually reads");
});

test("a clean run says so without inventing a skip clause", () => {
  const result = summarise([
    { file: "a.test.js", status: 0, skipped: 0 },
    { file: "b.test.js", status: 0, skipped: 0 },
  ]);
  assert.equal(result.line, "all 2 suite(s) passed");
});

test("failures still win over skips", () => {
  const result = summarise([
    { file: "a.test.js", status: 1, skipped: 0 },
    { file: "b.test.js", status: 0, skipped: 3 },
  ]);
  assert.deepEqual(result.failed, [{ file: "a.test.js", status: 1 }]);
  assert.equal(result.passed, 1);
  // A red run must not bury its failure under a skip note.
  assert.match(result.line, /1 suite\(s\) FAILED/);
});

// ---- where the wall time went ------------------------------------------------------------------

test("the slowest files come back first, with their share of the run", () => {
  const { totalMs, ranked, headShare } = slowest(
    [{ file: "fast", ms: 100 }, { file: "slow", ms: 700 }, { file: "mid", ms: 200 }], 2,
  );
  assert.equal(totalMs, 1000);
  assert.deepEqual(ranked.map((r) => r.file), ["slow", "mid"]);
  assert.equal(ranked[0].share, 0.7);
  assert.equal(headShare, 0.9, "the head share is what decides whether tiering is worth doing");
});

test("the TOTAL counts every file, not just the ones reported", () => {
  // A total computed from the ranked head would make any suite look like its own top N, and a share
  // measured against it would read 100% however long the tail was.
  assert.equal(slowest([{ file: "a", ms: 10 }, { file: "b", ms: 90 }], 1).totalMs, 100);
});

test("a run that timed nothing reports zero, never NaN", () => {
  // A share of nothing is 0. A hole where the instrument failed reads as a number in a report and
  // gets quoted, which this repo has done to itself with four separate measurements.
  const { totalMs, ranked, headShare } = slowest([{ file: "a" }, { file: "b", ms: "x" }]);
  assert.equal(totalMs, 0);
  assert.deepEqual(ranked, []);
  assert.equal(headShare, 0);
});

test("no results at all is not an error", () => {
  assert.equal(slowest([]).totalMs, 0);
  assert.equal(slowest().totalMs, 0);
});
