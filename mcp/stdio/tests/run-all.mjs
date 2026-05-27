#!/usr/bin/env node
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const root = new URL("..", import.meta.url);
const testDirs = ["tests", "tests/adapters"];
const files = testDirs.flatMap((dir) => (
  readdirSync(new URL(`${dir}/`, root))
    .filter((name) => name.endsWith(".test.js"))
    .sort()
    .map((name) => join(dir, name))
));

for (const file of files) {
  console.error(`\n[run-all] node ${file}`);
  const result = spawnSync(process.execPath, [file], {
    cwd: root,
    stdio: "inherit",
    env: process.env,
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}
