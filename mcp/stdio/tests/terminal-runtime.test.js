#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TerminalProcessManager, bridgeTerminalSupported, classifyTerminalRuntimeOutput, terminalCommandWithoutResume } from "../terminal-runtime.js";

assert.equal(typeof bridgeTerminalSupported(), "boolean");
assert.equal(classifyTerminalRuntimeOutput("pi", "No API key found for amazon-bedrock. Use /login.").kind, "auth");
assert.equal(classifyTerminalRuntimeOutput("pi", 'Session "dead-session" not found').kind, "missing_session");
assert.equal(terminalCommandWithoutResume("pi", "pi-aify --aify-agent worker --resume dead-session"), "pi-aify --aify-agent worker");
assert.equal(terminalCommandWithoutResume("hermes", "hermes-aify --resume hermes-1 --aify-agent h"), "hermes-aify --aify-agent h");


const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "aify-terminal-runtime-"));
const chunks = [];
const exits = [];
const heals = [];
const manager = new TerminalProcessManager({
  onOutput: async (_id, text) => chunks.push(text),
  onExit: async (_id, detail) => exits.push(detail),
  onHeal: async (_id, detail) => heals.push(detail),
});

const serializedCalls = [];
const serializedManager = new TerminalProcessManager({
  idleFlushMs: 10,
  maxLatencyMs: 30,
  maxBatchChars: 16 * 1024,
  onOutput: async (_id, text) => {
    serializedCalls.push(text);
    await new Promise((resolve) => setTimeout(resolve, 25));
  },
});
await Promise.all([
  serializedManager.emitOutputForTest("serial", "A"),
  serializedManager.emitOutputForTest("serial", "B"),
  serializedManager.emitOutputForTest("serial", "C"),
]);
await serializedManager.flushOutputForTest("serial");
assert.deepEqual(serializedCalls, ["ABC"], "terminal output chunks should be coalesced and delivered in emission order");

const sizeFlushCalls = [];
const sizeFlushManager = new TerminalProcessManager({
  idleFlushMs: 1000,
  maxLatencyMs: 1000,
  maxBatchChars: 4,
  onOutput: async (_id, text) => sizeFlushCalls.push(text),
});
await sizeFlushManager.emitOutputForTest("size", "ab");
assert.deepEqual(sizeFlushCalls, [], "small chunks should wait for the flush window");
await sizeFlushManager.emitOutputForTest("size", "cd");
await sizeFlushManager.flushOutputForTest("size");
assert.deepEqual(sizeFlushCalls, ["abcd"], "hitting maxBatchChars should flush the coalesced batch");



const exitOrderEvents = [];
const exitOrderManager = new TerminalProcessManager({
  idleFlushMs: 1000,
  maxLatencyMs: 1000,
  onOutput: async (_id, text) => exitOrderEvents.push(`output:${text}`),
  onExit: async () => exitOrderEvents.push("exit"),
});
await exitOrderManager.emitOutputForTest("exit-order", "last chunk");
await exitOrderManager._handleExit("exit-order", { id: "exit-order", runtime: "", outputTail: "", resolveExit: () => {} }, {});
assert.deepEqual(
  exitOrderEvents,
  ["output:last chunk", "exit"],
  "terminal exit must flush buffered output before posting stopped/failed status",
);

const duplicateExitEvents = [];
const duplicateExitManager = new TerminalProcessManager({
  onExit: async () => duplicateExitEvents.push("exit"),
});
const duplicateExitState = { id: "dup-exit", runtime: "", outputTail: "", resolveExit: () => {} };
await duplicateExitManager._handleExit("dup-exit", duplicateExitState, {});
await duplicateExitManager._handleExit("dup-exit", duplicateExitState, {});
assert.deepEqual(duplicateExitEvents, ["exit"], "duplicate child exit/error events must not emit terminal exit twice");

const realPipeEvents = [];
const realPipeManager = new TerminalProcessManager({
  idleFlushMs: 1000,
  maxLatencyMs: 1000,
  onOutput: async (_id, text) => realPipeEvents.push(`output:${text}`),
  onExit: async () => realPipeEvents.push("exit"),
});
await realPipeManager.startPipeProcess({
  id: "pipe-output-order",
  command: `node -e "process.stdout.write('PIPE_LAST_CHUNK')"`,
  cwd: tmp,
});
const realPipeDeadline = Date.now() + 5000;
while (Date.now() < realPipeDeadline && !realPipeEvents.includes("exit")) {
  await new Promise((resolve) => setTimeout(resolve, 25));
}
assert.deepEqual(realPipeEvents, ["output:PIPE_LAST_CHUNK", "exit"], "pipe fallback should drain stdio before terminal exit status");

const pipeStopEvents = [];
const pipeStopManager = new TerminalProcessManager({
  onExit: async () => pipeStopEvents.push("exit"),
});
await pipeStopManager.startPipeProcess({
  id: "pipe-stop",
  command: `node -e "process.stdin.resume(); process.stdin.on('end', () => process.exit(0));"`,
  cwd: tmp,
});
await pipeStopManager.stop("pipe-stop", "test pipe stop");
assert.deepEqual(pipeStopEvents, ["exit"], "pipe fallback stop should wait for real process exit like PTY stop");

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


const fakePi = path.join(tmp, "fake-pi.mjs");
fs.writeFileSync(fakePi, `
if (process.argv.includes("--resume") && process.argv[process.argv.indexOf("--resume") + 1] === "dead-session") {
  console.error('Session "dead-session" not found');
  process.exit(1);
}
console.log("AIFY_PI_FRESH_STARTED");
setTimeout(() => process.exit(0), 50);
`);
const fakePiCommandPath = fakePi.replace(/\\/g, "/");
await manager.start({
  id: "pi-heal",
  command: `node ${fakePiCommandPath} --resume dead-session`,
  cwd: tmp,
  runtime: "pi",
  sessionHandle: "dead-session",
  agentId: "pi-agent",
});
const healDeadline = Date.now() + 5000;
while (Date.now() < healDeadline && !chunks.join("").includes("AIFY_PI_FRESH_STARTED")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}
assert.match(chunks.join(""), /starting a fresh pi session without --resume/);
assert.match(chunks.join(""), /AIFY_PI_FRESH_STARTED/);
assert.equal(heals.at(-1)?.agentId, "pi-agent");
assert.equal(heals.at(-1)?.previousSessionHandle, "dead-session");

const stopHealChunks = [];
const stopHealManager = new TerminalProcessManager({
  onOutput: async (_id, text) => stopHealChunks.push(text),
});
await stopHealManager.start({
  id: "stop-no-heal",
  command: `node ${fakePiCommandPath} --resume dead-session`,
  cwd: tmp,
  runtime: "pi",
  sessionHandle: "dead-session",
});
await stopHealManager.stop("stop-no-heal", "test stop before heal");
await new Promise((resolve) => setTimeout(resolve, 100));
assert(!stopHealChunks.join("").includes("starting a fresh pi session"), "intentional terminal stop must not trigger stale-handle heal");


const fakeAuthPi = path.join(tmp, "fake-auth-pi.mjs");
fs.writeFileSync(fakeAuthPi, `
console.error("No API key found for amazon-bedrock. Use /login.");
setInterval(() => {}, 1000);
`);
const fakeAuthPiCommandPath = fakeAuthPi.replace(/\\/g, "/");
await manager.start({
  id: "pi-auth",
  command: `node ${fakeAuthPiCommandPath}`,
  cwd: tmp,
  runtime: "pi",
});
const authDeadline = Date.now() + 5000;
while (Date.now() < authDeadline && !chunks.join("").includes("Pi authentication failed fast")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}
assert.match(chunks.join(""), /Pi authentication failed fast/);

await manager.stopAll("test cleanup");
fs.rmSync(tmp, { recursive: true, force: true });

console.log("terminal-runtime.test.js: all assertions passed");
process.exit(0);
