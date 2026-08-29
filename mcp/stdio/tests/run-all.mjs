#!/usr/bin/env node
import { readdirSync } from "node:fs";
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
const failed = [];
for (const file of files) {
  console.error(`\n[run-all] node ${file}`);
  const startedAt = Date.now();
  const result = spawnSync(process.execPath, [file], {
    cwd: root,
    stdio: ["inherit", "pipe", "inherit"],
    encoding: "utf8",
    env: process.env,
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
