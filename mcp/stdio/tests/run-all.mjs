#!/usr/bin/env node
import { mkdtempSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

import { skippedFrom, slowest, summarise } from "./run-all-summary.mjs";
import { orderLongestFirst, readTimings, writeTimings } from "./run-all-timings.mjs";

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
//: Beside the runner rather than in temp -- this file PRUNES temp, so a cache written there would
//: be swept by its own housekeeping and the ordering would silently never take effect.
const TIMINGS_FILE = new URL("./.run-all-timings.json", import.meta.url);
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

// HOW MANY AT ONCE, and why this is not 1 any more.
//
// It ran one file at a time and took 700 SECONDS on a 32-core machine, with the 15 slowest files
// holding 64.6% of it -- so most of the wall time was one core working while 31 idled. The python
// suite made the same move on 2026-09-03 (22 minutes to 2m50 at `-n 8`) and the argument is
// identical: a suite nobody will wait for is a suite that gets skipped, and this repo has already
// paid for that twice -- three of CLAUDE.md's own counts went stale, and a cross-repo proof ran
// nothing while reporting green.
//
// WHY IT IS SAFE HERE, checked rather than assumed. Every file already gets its own process; no
// test in this suite binds a FIXED port (they all listen on 0 and read the assigned one back); and
// each file now gets its OWN temp root inside the run's root, so two files cannot meet in a scratch
// directory. What remains shared is the operator's real home, which tests must already seal --
// a test that reaches outside its sandbox was a defect before this change and is a louder one now.
//
// AIFY_TEST_CONCURRENCY=1 restores the old behaviour. Reach for it when a failure looks like two
// files meeting rather than a real defect -- if it goes green at 1, that IS the finding, and the
// file that needs sealing is named by the diff between the two runs.
const CONCURRENCY = Math.max(1, Number(process.env.AIFY_TEST_CONCURRENCY || 6));

// LONGEST FIRST, from the previous run's timings if we have them. A pool that starts a 90-second
// file last leaves every other worker idle waiting for it, which costs more than the parallelism
// saves on a distribution this skewed. With no history the order is alphabetical and the pool still
// works -- it is an optimisation, not a correctness input.
const queue = orderLongestFirst(files, readTimings(TIMINGS_FILE));

/** Run one file, buffering its output so two files cannot interleave mid-line. */
function runOne(file) {
  return new Promise((resolve) => {
    const startedAt = Date.now();
    // ITS OWN TEMP ROOT. `os.tmpdir()` reads these at CALL time, in this process and in every
    // launcher, bridge and daemon the test spawns, so one variable isolates a whole tree.
    const fileTemp = mkdtempSync(join(tempRoot, `f-`));
    const child = spawn(process.execPath, [file], {
      cwd: root,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...childEnv, TMPDIR: fileTemp, TEMP: fileTemp, TMP: fileTemp },
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (status) => {
      resolve({ file, status, stdout, stderr, ms: Date.now() - startedAt });
    });
  });
}

async function worker() {
  for (;;) {
    const file = queue.shift();
    if (!file) return;
    const done = await runOne(file);
    // ANNOUNCED ON COMPLETION, not on start: with a pool, a "running X" line is followed by
    // another file's output and reads as X having produced it.
    console.error(`${String.fromCharCode(10)}[run-all] node ${file} (${(done.ms / 1000).toFixed(1)}s)`);
    if (done.stderr) process.stderr.write(done.stderr);
    if (done.stdout) process.stdout.write(done.stdout);
    results.push({ file: done.file, status: done.status, skipped: skippedFrom(done.stdout), ms: done.ms });
    if (done.status !== 0) {
      // Don't bail on the first failure — run every file so a single broken suite can't silently
      // hide the rest (this is how the orphaned tests/controllers/* suite went unnoticed).
      failed.push({ file: done.file, status: done.status });
    }
  }
}

const startedWholeRun = Date.now();
await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, () => worker()));
const wallMs = Date.now() - startedWholeRun;
writeTimings(TIMINGS_FILE, results);
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
// WALL AND SUMMED ARE DIFFERENT NUMBERS NOW, and either alone misleads: the sum says where to
// spend effort, the wall clock says what a person actually waited for. Before the pool they
// were the same number, which is why only one was printed.
console.error(
  `\n[run-all] ${results.length} file(s) at concurrency ${CONCURRENCY}: `
  + `${seconds(wallMs)}s wall, ${seconds(timing.totalMs)}s summed. `
  + `The ${timing.ranked.length} slowest hold ${percent(timing.headShare)} of the summed time:`,
);
for (const { file, ms, share } of timing.ranked) {
  console.error(`  ${seconds(ms).padStart(7)}s  ${percent(share).padStart(6)}  ${file}`);
}

removeTempRoot();
