#!/usr/bin/env node
// Does the runner actually run every test in here?
//
// `run-all.mjs` discovers files by `endsWith(".test.js")` across three named directories. Both halves
// of that can go quietly wrong, and quietly is the problem: a test file nobody runs looks exactly like
// a test file that passes. Nothing reports it, and the suite count is the only tell -- which is a
// number people read as "big" rather than as "one bigger than yesterday".
//
// This repo has been bitten by the shape twice already. The 1000-line gate scanned `service/**` and
// left fifteen files ungoverned, and the JS half's two hand-listed roots covered everything only by
// coincidence. An unguarded population reports green exactly like a guarded one.
//
// So: derive what SHOULD run, compare it to what the runner WILL run, and fail on the difference. The
// directory list is read out of the runner rather than copied here, because two lists that must agree
// are a defect with a delay on it.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const BRIDGE = join(dirname(fileURLToPath(import.meta.url)), "..");
const RUNNER = join(BRIDGE, "tests", "run-all.mjs");

/** Test-shaped by any extension this project uses, so a new one cannot arrive unnoticed. */
const TEST_SHAPED = /\.test\.(js|mjs|cjs)$/;

/** What the runner itself picks up. Kept beside TEST_SHAPED so the gap between them is visible. */
const RUNNER_PICKS_UP = /\.test\.js$/;

/** The directories run-all.mjs looks in, read from the runner so the two cannot drift apart. */
function runnerDirs() {
  const source = readFileSync(RUNNER, "utf8");
  const match = /const testDirs = \[([^\]]*)\]/.exec(source);
  assert.notEqual(match, null, "run-all.mjs no longer declares testDirs; re-read it before editing this");
  const dirs = match[1]
    .split(",")
    .map((entry) => entry.trim().replace(/^["'`]|["'`]$/g, ""))
    .filter((entry) => entry !== "");
  assert.ok(dirs.length > 0, "run-all.mjs declares testDirs but it parsed empty");
  return dirs;
}

test("every test-shaped file in the runner's directories is one the runner will run", () => {
  const missed = runnerDirs().flatMap((dir) => (
    readdirSync(join(BRIDGE, dir))
      .filter((name) => TEST_SHAPED.test(name) && !RUNNER_PICKS_UP.test(name))
      .map((name) => join(dir, name))
  ));

  assert.deepEqual(
    missed,
    [],
    "these files look like tests and run-all.mjs will not run them -- rename them to .test.js, or "
    + "widen the runner's filter, but do not leave them where they read as covered",
  );
});

test("the runner's directories all exist", () => {
  // A renamed directory would silently shrink the suite rather than erroring.
  for (const dir of runnerDirs()) {
    assert.doesNotThrow(() => readdirSync(join(BRIDGE, dir)), `run-all.mjs names ${dir}, which is gone`);
  }
});

test("the discovery rule is actually narrower than the test-shaped rule", () => {
  // The positive control. If someone widened the runner to every extension, these two patterns would
  // agree and the first test would pass by having nothing to look for -- a green that proves nothing.
  // It should then be DELETED as done, not left standing as decoration.
  assert.ok(TEST_SHAPED.test("x.test.mjs"), "the test-shaped rule does not recognise .test.mjs");
  assert.equal(RUNNER_PICKS_UP.test("x.test.mjs"), false, "the runner now runs .test.mjs; retire this file");
});
