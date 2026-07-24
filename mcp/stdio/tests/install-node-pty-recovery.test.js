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

test("doctor reports an unloadable installed node-pty", () => {
  assert.match(doctor, /bridge-terminal/);
  assert.match(doctor, /require\(["']node-pty["']\)/);
});

test("doctor compares bridge processes to the install marker timestamp", () => {
  assert.match(doctor, /statSync\(join\(AIFY_HOME, ["']\.aify-version["']\)\)\.mtimeMs/);
});
