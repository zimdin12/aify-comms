#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TerminalProcessManager, bridgeTerminalSupported } from "../terminal-runtime.js";

assert.equal(typeof bridgeTerminalSupported(), "boolean");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aify-terminal-runtime-"));
const chunks = [];
const exits = [];
const manager = new TerminalProcessManager({
  onOutput: async (_id, text) => chunks.push(text),
  onExit: async (_id, detail) => exits.push(detail),
});

await manager.start({
  id: "term-test",
  command: process.platform === "win32" ? "cd && echo AIFY_TERMINAL_READY" : "sh -lc 'pwd; echo AIFY_TERMINAL_READY'",
  cwd: tmp,
});

const deadline = Date.now() + 5000;
while (Date.now() < deadline && !chunks.join("").includes("AIFY_TERMINAL_READY")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

const output = chunks.join("");
assert.match(output.replace(/\\/g, "/"), new RegExp(tmp.replace(/\\/g, "/").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
assert.match(output, /AIFY_TERMINAL_READY/);
const termExitDeadline = Date.now() + 5000;
while (Date.now() < termExitDeadline && manager.has("term-test")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

await manager.start({
  id: "interactive",
  command: process.platform === "win32" ? "cmd" : "sh",
  cwd: tmp,
});
assert.doesNotThrow(() => manager.resize("interactive", 120, 40));
manager.input("interactive", process.platform === "win32" ? "echo AIFY_INPUT_OK\r\nexit\r\n" : "echo AIFY_INPUT_OK\nexit\n");

const inputDeadline = Date.now() + 5000;
while (Date.now() < inputDeadline && !chunks.join("").includes("AIFY_INPUT_OK")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}
assert.match(chunks.join(""), /AIFY_INPUT_OK/);
const interactiveExitDeadline = Date.now() + 5000;
while (Date.now() < interactiveExitDeadline && manager.has("interactive")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}

await manager.stopAll("test cleanup");
fs.rmSync(tmp, { recursive: true, force: true });

console.log("terminal-runtime.test.js: all assertions passed");
process.exit(0);
