import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const installer = fs.readFileSync(path.join(root, "install.sh"), "utf8");
const doctor = fs.readFileSync(path.join(root, "mcp/stdio/doctor.js"), "utf8");

test("installer rebuilds node-pty when its native module cannot load", () => {
  assert.match(installer, /require\(["']node-pty["']\)/);
  assert.match(installer, /npm rebuild node-pty/);
});

// `bridge-terminal` used to be asserted here, as a regex over doctor.js. It moved to
// `aify-env doctor` in v0.6, where lib/environment-checks.mjs answers it and both arms are tested by
// CALLING terminalCheck() -- available, and node-pty failing to load -- rather than by checking that
// a line was written. The installer half below stays: rebuilding node-pty is still aify-comms' job.

test("doctor compares bridge processes to the install marker timestamp", () => {
  assert.match(doctor, /statSync\(join\(AIFY_HOME, ["']\.aify-version["']\)\)\.mtimeMs/);
});
