import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "server.js"), "utf8");
const main = source.match(/async function main\(\) \{([\s\S]*?)\n\}/)?.[1] || "";

test("Codex live discovery cannot block MCP startup", () => {
  assert.doesNotMatch(main, /await autoRegisterConfiguredAgent\(\)/);
  assert.match(main, /autoRegisterConfiguredAgent\(\)\.catch/);
});
