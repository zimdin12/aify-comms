#!/usr/bin/env node
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const root = new URL("..", import.meta.url);
const testDirs = ["tests", "tests/adapters", "tests/controllers"];
const files = testDirs.flatMap((dir) => (
  readdirSync(new URL(`${dir}/`, root))
    .filter((name) => name.endsWith(".test.js"))
    .sort()
    .map((name) => join(dir, name))
));

const failed = [];
for (const file of files) {
  console.error(`\n[run-all] node ${file}`);
  const result = spawnSync(process.execPath, [file], {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
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
console.error(`\n[run-all] all ${files.length} suite(s) passed`);
