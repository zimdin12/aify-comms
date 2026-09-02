#!/usr/bin/env node
import { mkdtempSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

import { skippedFrom, slowest, summarise } from "./run-all-summary.mjs";

const root = new URL("..", import.meta.url);
const testDirs = ["tests", "tests/adapters", "tests/controllers"];
const files = testDirs.flatMap((dir) => (
  readdirSync(new URL(`${dir}/`, root))
    .filter((name) => name.endsWith(".test.js"))
    .sort()
    .map((name) => join(dir, name))
));

// stdout is PIPED rather than inherited, so the runner can see what a file reported. Exit status
// alone cannot distinguish a file that proved something from one whose tests all skipped, and this
// suite has exactly that case: delegated-terminal-against-real-aify-env.test.js skips itself when the
// aify-env checkout is absent, so on any other machine the cross-repo proof ran nothing and this
// printed "all N suite(s) passed". Each file's output is echoed the moment it finishes — the files
// take seconds each, so this stays as readable as streaming was.
const results = [];
// EVERY SCRATCH DIRECTORY THIS SUITE MAKES LANDS IN ONE PLACE, AND THAT PLACE IS DELETED.
//
// Tests here call `mkdtemp` freely and a test that fails part-way never reaches its own cleanup.
// Measured on this machine 2026-09-02: 148 `aify-*` directories in the user's Temp from one morning
// of suite runs across the three repos, and roughly 50,000 before a cleanup tool removed them. The
// prefixes are scenario names -- `aify-damaged`, `aify-reinstall`, `aify-cwd` -- so no shipped code
// path leaks; 80 test files do.
//
// REDIRECTING TEMP RATHER THAN FIXING EACH CALL is what makes this hold. `os.tmpdir()` reads
// TMPDIR/TEMP/TMP at CALL time, so every `mkdtemp` lands inside this root -- in each spawned test
// process, and in every launcher, bridge and daemon those tests spawn in turn. No test file changes,
// and forgetting to clean up stops mattering. All three variables are set because which one is read
// depends on the platform, and setting one leaves the leak in place on the other.
const TEMP_ROOT_PREFIX = "aify-comms-testrun-";
const PRUNE_AFTER_MS = 60 * 60 * 1000;

// A run that is killed never reaches its own teardown -- which is exactly the case that produced the
// pile -- so old roots are swept here. Failures are ignored: pruning is a courtesy and must never
// decide whether the suite passes.
for (const entry of readdirSync(tmpdir(), { withFileTypes: true })) {
  if (!entry.isDirectory() || !entry.name.startsWith(TEMP_ROOT_PREFIX)) continue;
  const full = join(tmpdir(), entry.name);
  try {
    if (statSync(full).mtimeMs < Date.now() - PRUNE_AFTER_MS) rmSync(full, { recursive: true, force: true });
  } catch { /* held by another run, or not ours to remove */ }
}

const tempRoot = mkdtempSync(join(tmpdir(), TEMP_ROOT_PREFIX));
const childEnv = { ...process.env, TMPDIR: tempRoot, TEMP: tempRoot, TMP: tempRoot };

/** Remove the whole root. Called on every exit path, including the failing one. */
function removeTempRoot() {
  try { rmSync(tempRoot, { recursive: true, force: true }); } catch { /* pruned next run */ }
}

const failed = [];
for (const file of files) {
  console.error(`\n[run-all] node ${file}`);
  const startedAt = Date.now();
  const result = spawnSync(process.execPath, [file], {
    cwd: root,
    stdio: ["inherit", "pipe", "inherit"],
    encoding: "utf8",
    env: childEnv,
  });
  const ms = Date.now() - startedAt;
  if (result.stdout) process.stdout.write(result.stdout);
  results.push({ file, status: result.status, skipped: skippedFrom(result.stdout), ms });
  if (result.status !== 0) {
    // Don't bail on the first failure — run every file so a single broken
    // suite can't silently hide the rest (this is how the orphaned
    // tests/controllers/* suite went unnoticed). Collect and report at the end.
    failed.push({ file, status: result.status });
  }
}

if (failed.length > 0) {
  console.error(`\n[run-all] ${failed.length} suite(s) FAILED:`);
  for (const { file, status } of failed) {
    console.error(`  - ${file} (exit ${status})`);
  }
  removeTempRoot();
  process.exit(1);
}
const summary = summarise(results);
if (summary.skipped.length > 0) {
  // Named, not folded into the pass count: a proof that did not run is the most useful thing this
  // summary can surface, and it is invisible in an exit status.
  console.error(`\n[run-all] skipped, so NOT verified here:`);
  for (const { file, skipped } of summary.skipped) {
    console.error(`  - ${file} (${skipped} test(s))`);
  }
}
console.error(`\n[run-all] ${summary.line}`);

// WHERE THE TIME WENT. Printed on every green run, not behind a flag, because a cost nobody is shown
// is one nobody tiers. The runner spawns one node process per file, SERIALLY, and the distribution is
// skewed enough that a handful of files decide the wall time while the rest are noise -- which is the
// difference between a suite worth tiering and one worth leaving alone.
const timing = slowest(results);
const seconds = (ms) => (ms / 1000).toFixed(1);
const percent = (share) => `${(share * 100).toFixed(1)}%`;
console.error(
  `\n[run-all] ${results.length} file(s) in ${seconds(timing.totalMs)}s. `
  + `The ${timing.ranked.length} slowest hold ${percent(timing.headShare)} of it:`,
);
for (const { file, ms, share } of timing.ranked) {
  console.error(`  ${seconds(ms).padStart(7)}s  ${percent(share).padStart(6)}  ${file}`);
}

removeTempRoot();
