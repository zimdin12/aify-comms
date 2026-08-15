#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TerminalProcessManager, bridgeTerminalSupported, expandUserHome } from "../terminal-runtime.js";
// The pure text handling moved to `terminal-text.js` in v0.5.4. Imported from its OWNER rather than
// re-exported through `terminal-runtime.js`: a re-export keeps a stale import resolving and is what
// makes the next move look like it changed nothing.
import { classifyTerminalRuntimeOutput, terminalCommandWithoutResume } from "../terminal-text.js";
import { tmpDir } from "./_tmpdir.js";

assert.equal(typeof bridgeTerminalSupported(), "boolean");
assert.equal(classifyTerminalRuntimeOutput("pi", "No API key found for amazon-bedrock. Use /login.").kind, "auth");
assert.equal(classifyTerminalRuntimeOutput("pi", 'Session "dead-session" not found').kind, "missing_session");
assert.equal(terminalCommandWithoutResume("pi", "pi-aify --aify-agent worker --resume dead-session"), "pi-aify --aify-agent worker");
assert.equal(terminalCommandWithoutResume("hermes", "hermes-aify --resume hermes-1 --aify-agent h"), "hermes-aify --aify-agent h");

// Tilde-expansion guard: node-pty's chdir(2) does not expand "~", so
// operator-supplied workspaces like "~/projects/foo" would otherwise spawn
// and die instantly with ENOENT. Operator-reported 2026-05-28 on hermes-test
// (terminal_events showed "chdir(2) failed.: No such file or directory" for
// workspace "~/projects/blei-cms"). Keep this expansion intact.
assert.equal(expandUserHome("~"), os.homedir());
assert.equal(expandUserHome("~/projects/foo"), `${os.homedir()}/projects/foo`);
assert.equal(expandUserHome("/abs/path"), "/abs/path");
assert.equal(expandUserHome("relative/path"), "relative/path");
assert.equal(expandUserHome(""), "");
assert.equal(expandUserHome(null), "");
// POSIX-style ~user expansion is bash-specific; we don't pretend to handle
// it. Leaving it unchanged is safer than guessing whose home to substitute.
assert.equal(expandUserHome("~user/path"), "~user/path");


const tmp = tmpDir("aify-terminal-runtime-");
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

// B3 (visible-TUI): on the FINAL-exit path of a managed console PTY, the
// descendant worker tree must be best-effort reaped so Windows-reparented
// children (claude.exe + channel-sidecar + MCP) cannot survive headless.
// Use an overridden _reapPtyTree to OBSERVE the reap without spawning/killing
// any real process. The reap must fire on final exit for a pty-kind state...
{
  const reapManager = new TerminalProcessManager({ onExit: async () => {} });
  const reaped = [];
  reapManager._reapPtyTree = (term) => { reaped.push(term); };
  const fakeTerm = { pid: 0, kill: () => {} };
  await reapManager._handleExit(
    "b3-final",
    { id: "b3-final", runtime: "", outputTail: "", kind: "pty", term: fakeTerm, resolveExit: () => {} },
    {},
  );
  assert.equal(reaped.length, 1, "final PTY exit must best-effort reap the descendant worker tree");
  assert.equal(reaped[0], fakeTerm, "reap must target the PTY's own term handle");
}

// ...and must NOT fire for a non-pty (pipe) terminal exit.
{
  const reapManager = new TerminalProcessManager({ onExit: async () => {} });
  const reaped = [];
  reapManager._reapPtyTree = (term) => { reaped.push(term); };
  await reapManager._handleExit(
    "b3-pipe",
    { id: "b3-pipe", runtime: "", outputTail: "", kind: "pipe", proc: { pid: 0 }, resolveExit: () => {} },
    {},
  );
  assert.equal(reaped.length, 0, "non-pty terminal exit must not invoke the PTY descendant reap");
}

// ...and must NOT fire on the hermes resume-heal restart path (early return
// re-spawns the session; reaping there would kill the healthy fresh tree).
{
  const healReapManager = new TerminalProcessManager({
    onOutput: async () => {},
    onExit: async () => {},
    onHeal: async () => {},
  });
  const healReaped = [];
  healReapManager._reapPtyTree = (term) => { healReaped.push(term); };
  // Stub start() so the heal branch's re-spawn is a no-op observable.
  let restarted = 0;
  healReapManager.start = async () => { restarted += 1; };
  const healState = {
    id: "b3-heal",
    runtime: "hermes",
    outputTail: "",
    kind: "pty",
    term: { pid: 0, kill: () => {} },
    sessionHandle: "stale-hermes",
    command: "hermes-aify --resume stale-hermes --aify-agent h",
    classification: { kind: "missing_session", status: "failed", sessionHandle: "stale-hermes", message: "gone" },
    resolveExit: () => {},
  };
  await healReapManager._handleExit("b3-heal", healState, {});
  assert.equal(restarted, 1, "heal path must re-spawn a fresh session");
  assert.equal(healReaped.length, 0, "heal/restart path must NOT reap the descendant tree (would kill the fresh session)");
}

// Real-PTY section. node-pty's winpty backend throws an UNCAUGHT
// "Signals not supported on windows." from inside its own data-socket exit
// handler when a winpty-backed child is torn down (heal/restart/stop paths
// exercised below). That is a node-pty platform limitation on this Windows
// host, not an aify-comms bug — the production terminateProcessTree() already
// routes win32 teardown through taskkill and swallows the follow-on
// proc.kill(signal) throw. The pipe-fallback + coalescing + classifier
// assertions above already run on every platform; only the live winpty spawns
// are skipped here. On POSIX this whole section runs as before.
if (process.platform !== "win32") {
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

const fakeHermes = path.join(tmp, "fake-hermes.mjs");
fs.writeFileSync(fakeHermes, `
if (process.argv.includes("--resume") && process.argv[process.argv.indexOf("--resume") + 1] === "stale-hermes") {
  console.log("resuming... | gpt-5.5 | voice off");
  setInterval(() => {}, 1000);
} else {
  console.log("AIFY_HERMES_FRESH_STARTED");
  setTimeout(() => process.exit(0), 50);
}
`);
const fakeHermesCommandPath = fakeHermes.replace(/\\/g, "/");
const previousHermesResumeMs = process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS;
process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = "50";
await manager.start({
  id: "hermes-heal",
  command: `node ${fakeHermesCommandPath} --resume stale-hermes`,
  cwd: tmp,
  runtime: "hermes",
  sessionHandle: "stale-hermes",
  agentId: "hermes-agent",
});
const hermesHealDeadline = Date.now() + 5000;
while (Date.now() < hermesHealDeadline && !chunks.join("").includes("AIFY_HERMES_FRESH_STARTED")) {
  await new Promise((resolve) => setTimeout(resolve, 50));
}
if (previousHermesResumeMs === undefined) delete process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS;
else process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS = previousHermesResumeMs;
assert.match(chunks.join(""), /Hermes saved session handle did not become ready/);
assert.match(chunks.join(""), /AIFY_HERMES_FRESH_STARTED/);
assert.equal(heals.at(-1)?.agentId, "hermes-agent");
assert.equal(heals.at(-1)?.previousSessionHandle, "stale-hermes");

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
} // end real-PTY section (skipped on win32: node-pty winpty teardown limitation)

// listOwnedSessions (WS4 Task 4.2): enumerate owned PTYs with their root pid so
// the env-bridge dead-PTY check can host-report a row whose local pid died.
// Drive it off injected map state (no real process needed).
{
  const ownedManager = new TerminalProcessManager({ onExit: async () => {} });
  ownedManager.terminals.set("owned-pty", {
    id: "owned-pty",
    status: "attached",
    agentId: "agent-x",
    runtime: "hermes",
    term: { pid: 4321 },
  });
  ownedManager.terminals.set("owned-pipe", {
    id: "owned-pipe",
    status: "attached",
    agentId: "agent-y",
    runtime: "pi",
    proc: { pid: 5678 },
  });
  ownedManager.terminals.set("no-pid", { id: "no-pid", status: "attached", agentId: "z" });
  const owned = ownedManager.listOwnedSessions();
  assert.equal(owned.length, 2, "only pid-bearing owned sessions are listed");
  const byId = Object.fromEntries(owned.map((s) => [s.terminalId, s]));
  assert.equal(byId["owned-pty"].pid, 4321);
  assert.equal(byId["owned-pty"].agentId, "agent-x");
  assert.equal(byId["owned-pipe"].pid, 5678);
  assert.ok(!byId["no-pid"], "a session without a pid is excluded");
}

await manager.stopAll("test cleanup");
fs.rmSync(tmp, { recursive: true, force: true });

console.log("terminal-runtime.test.js: all assertions passed");
process.exit(0);
