// `bridge-current` asks running bridges to relaunch only for commits that touched code they load. The doctor-only set
// it leaves out is derived from imports (doctor-only-files.mjs); the bug these catch is a shared file read as
// doctor-only, which would hide a real relaunch.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { doctorOnlyFiles } from "../doctor-only-files.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("only what the doctor alone reaches is doctor-only, whatever form another file imports it in", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-doctor-only-"));
  try {
    const write = (name, text) => fs.writeFileSync(path.join(dir, name), text);
    write("doctor.js", 'import { a } from "./a-check.mjs";\nimport { s } from "./shared.mjs";\nimport { l } from "./lazy.mjs";\n');
    write("a-check.mjs", "export const a = 1;\n");
    write("shared.mjs", "export const s = 1;\n");
    write("lazy.mjs", "export const l = 1;\n");
    write("server.js", "import { s } from './shared.mjs';\nconst lazy = () => import('./lazy.mjs');\n");
    write("hook.js", "// an entry point nothing imports\n");
    write("side.mjs", "export const x = 1;\n");
    write("runner.mjs", "import \"./side.mjs\";\n");
    fs.appendFileSync(path.join(dir, "doctor.js"), "import { x } from \"./side.mjs\";\n");
    assert.deepEqual(doctorOnlyFiles(dir), ["a-check.mjs", "doctor.js"]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("on this tree the doctor's own checks are doctor-only and the bridge's entry points are not", () => {
  const only = doctorOnlyFiles(STDIO);
  for (const file of ["doctor.js", "doctor-only-files.mjs", "spawn-queue-check.mjs"]) assert.ok(only.includes(file), file);
  // notify-check.js is a runtime hook named like a doctor check: the reason a name pattern was refused.
  for (const file of ["server.js", "claude-channel.js", "notify-check.js", "aify-service-endpoint.mjs"]) {
    assert.ok(!only.includes(file), `${file} is runtime`);
  }
});

test("6143c3f9 changed only the doctor and its tests, so a bridge built before it is not behind", () => {
  const repo = path.resolve(STDIO, "..", "..");
  const count = (extra) => Number(execFileSync("git", ["rev-list", "--count", "6143c3f9^..6143c3f9", "--", "mcp/stdio",
    ":(exclude)mcp/stdio/tests", ...extra], { cwd: repo, encoding: "utf8" }).trim());
  assert.equal(count([]), 1, "control: the commit is in mcp/stdio and counted without the doctor-only set");
  assert.equal(count(doctorOnlyFiles(STDIO).map((f) => `:(exclude)mcp/stdio/${f}`)), 0);
});
