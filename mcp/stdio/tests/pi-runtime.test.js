import assert from "assert";
import fs from "fs";
import os from "os";
import path from "path";

process.env.AIFY_PI_COMMAND = process.execPath;

const {
  canLaunchRuntime,
  controlCapabilitiesForRuntime,
  defaultCapabilitiesForRuntime,
  defaultSessionHandleForRuntime,
  detectPiRuntimeFailure,
  launchCwdProblem,
  launchRuntimeRun,
  normalizeRuntime,
  runtimeLaunchAvailability,
} = await import("../runtimes.js");
const { __resetPiSessionPoolForTests, __piSessionPoolSize, acquirePiSession, shutdownAllPiSessions } = await import("../pi-session.js");

assert.equal(normalizeRuntime("pi"), "pi");
assert.equal(normalizeRuntime("omp"), "pi");
assert.equal(normalizeRuntime("oh-my-pi"), "pi");
assert.equal(normalizeRuntime("pi-agent"), "pi");

assert.equal(canLaunchRuntime("pi"), true);
assert.deepEqual(controlCapabilitiesForRuntime("pi"), { interrupt: true, steer: true });
assert.deepEqual(defaultCapabilitiesForRuntime("pi", "managed"), ["managed-run", "native-managed-run", "resume", "interrupt", "steer", "spawn"]);
assert.deepEqual(defaultCapabilitiesForRuntime("pi", "resident", "session-123"), ["resident-run", "resume", "interrupt", "steer"]);

process.env.PI_SESSION_ID = "pi-session-123";
assert.equal(defaultSessionHandleForRuntime("pi"), "pi-session-123");

const availability = runtimeLaunchAvailability("pi");
assert.equal(availability.available, true);
assert.match(availability.message, /Pi launcher available/);

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-pi-runtime-"));
const fakeOmp = path.join(tmpDir, "fake-omp.mjs");
const argvCapturePath = path.join(tmpDir, "fake-omp-argv.jsonl");
const stdinCapturePath = path.join(tmpDir, "fake-omp-stdin.jsonl");

async function waitFor(predicate, label, timeoutMs = 2000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function readStdinMessages() {
  if (!fs.existsSync(stdinCapturePath)) return [];
  return fs.readFileSync(stdinCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
}
assert.equal(detectPiRuntimeFailure('No API key found for amazon-bedrock. Use /login.').authFailure, true);
assert.equal(detectPiRuntimeFailure('FATAL ERROR: Committing semi space failed. Allocation failed - JavaScript heap out of memory').fatalRuntime, true);
assert.equal(detectPiRuntimeFailure('Session "dead-session" not found').shouldHeal, true);
assert.deepEqual(
  {
    shouldHeal: detectPiRuntimeFailure('Error: Session "wrong-project" is in another project (C:\\tmp).').shouldHeal,
    healReason: detectPiRuntimeFailure('Error: Session "wrong-project" is in another project (C:\\tmp).').healReason,
  },
  { shouldHeal: true, healReason: "project_mismatch" },
);


fs.writeFileSync(fakeOmp, `#!/usr/bin/env node
import fs from "fs";
import readline from "readline";
if (process.env.AIFY_PI_ARGV_CAPTURE) {
  fs.appendFileSync(process.env.AIFY_PI_ARGV_CAPTURE, JSON.stringify(process.argv.slice(2)) + "\\n");
}
if (process.env.AIFY_PI_AUTH_FAIL === "1") {
  console.error("No API key found for amazon-bedrock. Use /login.");
  setInterval(() => {}, 1000);
}
if (process.env.AIFY_PI_OOM_FAIL === "1") {
  console.error("FATAL ERROR: Committing semi space failed. Allocation failed - JavaScript heap out of memory");
  console.error("at notify (B:/~BUN/root/omp-windows-x64.exe:370144:30)");
  process.exit(134);
}
if (process.argv.includes("--resume") && process.argv[process.argv.indexOf("--resume") + 1] === "dead-session") {
  console.error('Session "dead-session" not found');
  process.exit(1);
}
if (process.argv.includes("--resume") && process.argv[process.argv.indexOf("--resume") + 1] === "wrong-project") {
  console.error('Error: Session "wrong-project" is in another project (C:\\tmp).');
  process.exit(1);
}
const eventSessionId = Object.prototype.hasOwnProperty.call(process.env, "AIFY_PI_EVENT_SESSION_ID")
  ? process.env.AIFY_PI_EVENT_SESSION_ID
  : "pi-session-fake";
const withEventSession = (event) => eventSessionId ? { ...event, sessionId: eventSessionId } : event;
console.log(JSON.stringify(withEventSession({ type: "ready" })));
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  if (process.env.AIFY_PI_STDIN_CAPTURE) {
    fs.appendFileSync(process.env.AIFY_PI_STDIN_CAPTURE, line + "\\n");
  }
  const message = JSON.parse(line);
  if (message.type === "get_state") {
    console.log(JSON.stringify({
      id: message.id,
      type: "response",
      command: "get_state",
      success: true,
      data: {
        sessionId: process.env.AIFY_PI_GET_STATE_SESSION_ID || "pi-session-fake",
        sessionFile: "/tmp/pi-session-fake.jsonl"
      }
    }));
  } else if (message.type === "prompt") {
    if (message.message.includes("steer me now")) {
      console.log(JSON.stringify({
        id: message.id,
        type: "response",
        command: "prompt",
        success: false,
        error: "Agent is already processing. Use steer() or followUp() to queue messages, or wait for completion."
      }));
      return;
    }
    console.log(JSON.stringify(withEventSession({ id: message.id, type: "response", command: "prompt", success: true })));
    console.log(JSON.stringify({ type: "agent_start" }));
    if (message.message.includes("Wait for steer")) {
      console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "waiting" } }));
    } else if (message.message.includes("Pi assistant mentions EPIPE")) {
      console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "This assistant reply discusses EPIPE without indicating a runtime crash." } }));
      process.exit(1);
    } else if (message.message.includes("Pi long structured reply")) {
      const trailer = JSON.stringify({ status: "ok", id: "long-structured-reply" });
      console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "BEGIN-REPORT\\n" + "x".repeat(300_000) + "\\n" + trailer } }));
      console.log(JSON.stringify(withEventSession({ type: "agent_end", id: "turn-long" })));
    } else if (message.message.includes("Pi final fallback")) {
      const assistant = { role: "assistant", content: [{ type: "text", text: "final text from agent_end" }] };
      console.log(JSON.stringify({ type: "message_end", message: assistant }));
      console.log(JSON.stringify(withEventSession({ type: "agent_end", id: "turn-fake", messages: [assistant] })));
    } else {
      console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "hello " } }));
      console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "from pi" } }));
      console.log(JSON.stringify(withEventSession({ type: "agent_end", id: "turn-fake" })));
    }
  } else if (message.type === "steer") {
    console.log(JSON.stringify(withEventSession({ id: message.id, type: "response", command: "steer", success: true })));
    console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: " + steered" } }));
    console.log(JSON.stringify(withEventSession({ type: "agent_end", id: "turn-steered" })));
  }
});
`, { mode: 0o755 });

process.env.AIFY_PI_COMMAND = fakeOmp;
process.env.AIFY_PI_ARGV_CAPTURE = argvCapturePath;
process.env.AIFY_PI_STDIN_CAPTURE = stdinCapturePath;
const missingCwd = path.join(tmpDir, "missing-workspace");
assert.match(
  launchCwdProblem(missingCwd),
  /does not exist on this bridge host/,
  "missing workspaces should be detected before runtime spawn",
);
const badCwdController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: missingCwd,
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi missing cwd",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await assert.rejects(
  badCwdController.promise,
  /Workspace ".*missing-workspace" does not exist on this bridge host/,
  "missing cwd should fail with a workspace error, not a launcher ENOENT",
);

const events = [];
const runtimeStates = [];
const controller = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi smoke",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: (type, text) => events.push([type, text]),
    onRuntimeState: (state) => runtimeStates.push(state),
    onRefs: () => {},
  },
});
const result = await controller.promise;
assert.equal(result.status, "completed");
assert.equal(result.summary, "hello from pi");
assert.equal(result.runtimeState.sessionId, "pi-session-fake");
assert(runtimeStates.some((state) => state.sessionId === "pi-session-fake"));
assert(events.some(([type]) => type === "pi"));
assert(
  readStdinMessages().some((message) => message.type === "get_state"),
  "Pi managed runs should query OMP RPC state to capture canonical sessionId",
);

__resetPiSessionPoolForTests();
process.env.AIFY_PI_GET_STATE_SESSION_ID = "pi-session-from-state";
process.env.AIFY_PI_EVENT_SESSION_ID = "";
const stateOnlyController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi state capture",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: (state) => runtimeStates.push(state),
    onRefs: () => {},
  },
});
const stateOnlyResult = await stateOnlyController.promise;
assert.equal(stateOnlyResult.runtimeState.sessionId, "pi-session-from-state");
assert(runtimeStates.some((state) => state.sessionId === "pi-session-from-state"));
delete process.env.AIFY_PI_GET_STATE_SESSION_ID;
delete process.env.AIFY_PI_EVENT_SESSION_ID;

__resetPiSessionPoolForTests();
process.env.AIFY_PI_AUTH_FAIL = "1";
const authFailController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi auth fail",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await assert.rejects(
  authFailController.promise,
  /Pi authentication failed fast: .*No API key found for amazon-bedrock/,
  "Pi auth/provider failures should fail fast instead of waiting for the full run timeout",
);
delete process.env.AIFY_PI_AUTH_FAIL;

__resetPiSessionPoolForTests();
process.env.AIFY_PI_OOM_FAIL = "1";
const oomFailController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi oom fail",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await assert.rejects(
  oomFailController.promise,
  /Pi runtime crashed: .*JavaScript heap out of memory/,
  "Pi runtime fatal/OOM failures should fail clearly instead of surfacing raw EPIPE/noisy stack text",
);
delete process.env.AIFY_PI_OOM_FAIL;

__resetPiSessionPoolForTests();
const assistantEpipeController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi assistant mentions EPIPE",
    body: "Pi assistant mentions EPIPE",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await assert.rejects(
  assistantEpipeController.promise,
  (error) => {
    assert(!/Pi runtime crashed/i.test(String(error?.message || error)), "assistant text must not be classified as a runtime crash");
    assert.match(String(error?.message || error), /EPIPE/i);
    return true;
  },
);

__resetPiSessionPoolForTests();
const longReplyController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi long structured reply",
    body: "Pi long structured reply",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
const longReplyResult = await longReplyController.promise;
assert.equal(longReplyResult.status, "completed");
assert(longReplyResult.summary.includes("BEGIN-REPORT"), "long Pi replies should preserve the beginning for context");
assert(longReplyResult.summary.includes("long-structured-reply"), "long Pi replies should preserve terminal structured content");
assert(longReplyResult.summary.includes("truncated"), "long Pi replies should mark omitted middle content");


__resetPiSessionPoolForTests();
const healEvents = [];
const handleChanges = [];
const healedController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi dead resume",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: { sessionId: "dead-session" },
  callbacks: {
    onEvent: (type, text) => healEvents.push([type, text]),
    onRuntimeState: (state) => runtimeStates.push(state),
    onRefs: () => {},
    onSessionHandleChange: (newHandle, meta) => handleChanges.push({ newHandle, meta }),
  },
});
const healedResult = await healedController.promise;
assert.equal(healedResult.status, "completed");
assert.equal(healedResult.runtimeState.sessionId, "pi-session-fake");
assert(healEvents.some(([, text]) => /starting fresh/.test(text)), "dead Pi session handles should heal to fresh context");
const healedArgvLines = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
assert(healedArgvLines.some((argv) => argv.includes("--resume") && argv.includes("dead-session")), "first Pi launch should attempt the saved handle");
assert(!healedArgvLines.at(-1).includes("--resume"), "healed Pi relaunch should omit the dead --resume handle");
assert.deepEqual(handleChanges.at(-1), { newHandle: "", meta: { reason: "missing_session", previous: "dead-session" } });

__resetPiSessionPoolForTests();
const wrongProjectHandleChanges = [];
const wrongProjectController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi wrong-project resume",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: { sessionId: "wrong-project" },
  callbacks: {
    onEvent: (type, text) => healEvents.push([type, text]),
    onRuntimeState: (state) => runtimeStates.push(state),
    onRefs: () => {},
    onSessionHandleChange: (newHandle, meta) => wrongProjectHandleChanges.push({ newHandle, meta }),
  },
});
const wrongProjectResult = await wrongProjectController.promise;
assert.equal(wrongProjectResult.status, "completed");
assert.equal(wrongProjectResult.runtimeState.sessionId, "pi-session-fake");
assert.deepEqual(wrongProjectHandleChanges.at(-1), { newHandle: "", meta: { reason: "project_mismatch", previous: "wrong-project" } });

const residentDeadHandleController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "resident",
    sessionHandle: "dead-session",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, startupTimeoutMs: 1000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi resident dead resume",
    body: "Say hello",
    executionMode: "resident",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
    onSessionHandleChange: (newHandle, meta) => handleChanges.push({ newHandle, meta }),
  },
});
await assert.rejects(
  residentDeadHandleController.promise,
  /Resident Pi session "dead-session" is not resumable: .*Clear the saved session handle or start a fresh managed Pi session/,
  "resident Pi dead handles should fail with an actionable message instead of auto-healing",
);



__resetPiSessionPoolForTests();
const finalFallbackController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi final fallback",
    body: "Say hello without deltas",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
const finalFallbackResult = await finalFallbackController.promise;
assert.equal(finalFallbackResult.status, "completed");
assert.equal(finalFallbackResult.summary, "final text from agent_end");

__resetPiSessionPoolForTests();
const steerController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi steer",
    body: "Wait for steer",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
assert.equal(steerController.capabilities.steer, true);
await waitFor(
  () => fs.existsSync(stdinCapturePath) && fs.readFileSync(stdinCapturePath, "utf8").includes("Wait for steer"),
  "initial Pi prompt before steer",
);
await steerController.steer("steer me now");
const steerResult = await steerController.promise;
assert.equal(steerResult.status, "completed");
assert.equal(steerResult.summary, "waiting + steered");
const stdinLines = readStdinMessages();
const steerCommand = stdinLines.find((message) => message.type === "steer" && message.message === "steer me now");
assert(steerCommand, "Pi steer should send a real OMP RPC steer command");
const steerPrompt = stdinLines.find((message) => message.type === "prompt" && message.message === "steer me now");
assert(!steerPrompt, "Pi steer must not send a second prompt while OMP is busy");

__resetPiSessionPoolForTests();
const defaultModelController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    model: " DEFAULT ",
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Pi default model",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await defaultModelController.promise;
const argvLines = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
const defaultModelArgv = JSON.parse(argvLines.at(-1));
assert(!defaultModelArgv.includes("--model"), 'Pi model "default" should not be passed as --model');

__resetPiSessionPoolForTests();
const runtimeConfigDefaultModelController = launchRuntimeRun({
  agentId: "pi-worker",
  agentInfo: {
    agentId: "pi-worker",
    role: "coder",
    runtime: "pi",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000, model: "default" },
  },
  run: {
    from: "dashboard",
    subject: "Pi runtime config default model",
    body: "Say hello",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  },
});
await runtimeConfigDefaultModelController.promise;
const runtimeConfigArgvLines = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
const runtimeConfigDefaultModelArgv = JSON.parse(runtimeConfigArgvLines.at(-1));
assert(!runtimeConfigDefaultModelArgv.includes("--model"), 'Pi runtimeConfig model "default" should not be passed as --model');

// --- Phase 1 additions: pool reuse, idle teardown, crash recovery ---

__resetPiSessionPoolForTests();
fs.writeFileSync(argvCapturePath, "");
const reusePoolAgentInfo = {
  agentId: "pi-reuse-worker",
  role: "coder",
  runtime: "pi",
  sessionMode: "managed",
  cwd: process.cwd(),
  runtimeConfig: { timeoutMs: 5000 },
};
const reuse1 = launchRuntimeRun({
  agentId: "pi-reuse-worker",
  agentInfo: reusePoolAgentInfo,
  run: { from: "dashboard", subject: "Pi reuse 1", body: "Say hello", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
const reuse1Result = await reuse1.promise;
assert.equal(reuse1Result.status, "completed");
const argvAfterFirst = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
assert.equal(argvAfterFirst.length, 1, "first dispatch should spawn the omp child exactly once");

const reuse2 = launchRuntimeRun({
  agentId: "pi-reuse-worker",
  agentInfo: reusePoolAgentInfo,
  run: { from: "dashboard", subject: "Pi reuse 2", body: "Say hello again", executionMode: "managed" },
  runtimeState: { sessionId: "pi-session-fake" },
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
const reuse2Result = await reuse2.promise;
assert.equal(reuse2Result.status, "completed");
const argvAfterSecond = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
assert.equal(argvAfterSecond.length, 1, "second dispatch on the same agent should reuse the persistent omp child (no new spawn)");
assert.equal(reuse2Result.runtimeState.sessionId, "pi-session-fake");

// Idle teardown: short timeout forces respawn between turns.
__resetPiSessionPoolForTests();
fs.writeFileSync(argvCapturePath, "");
const idleAgentInfo = {
  agentId: "pi-idle-worker",
  role: "coder",
  runtime: "pi",
  sessionMode: "managed",
  cwd: process.cwd(),
  runtimeConfig: { timeoutMs: 5000, piIdleTimeoutMs: 100 },
};
const idle1 = launchRuntimeRun({
  agentId: "pi-idle-worker",
  agentInfo: idleAgentInfo,
  run: { from: "dashboard", subject: "Pi idle 1", body: "Say hello", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await idle1.promise;
await new Promise((resolve) => setTimeout(resolve, 250));
const idle2 = launchRuntimeRun({
  agentId: "pi-idle-worker",
  agentInfo: idleAgentInfo,
  run: { from: "dashboard", subject: "Pi idle 2", body: "Say hello again", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await idle2.promise;
const idleArgv = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
assert.equal(idleArgv.length, 2, "idle timeout should release the omp child and the next dispatch should respawn");

// Crash recovery: persistent child dies between turns — next dispatch respawns.
__resetPiSessionPoolForTests();
fs.writeFileSync(argvCapturePath, "");
const crashAgentInfo = {
  agentId: "pi-crash-worker",
  role: "coder",
  runtime: "pi",
  sessionMode: "managed",
  cwd: process.cwd(),
  runtimeConfig: { timeoutMs: 5000 },
};
const crash1 = launchRuntimeRun({
  agentId: "pi-crash-worker",
  agentInfo: crashAgentInfo,
  run: { from: "dashboard", subject: "Pi crash 1", body: "Say hello", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await crash1.promise;
// Forcibly kill the live child to simulate a between-turn crash.
const crashSessionPool = await import("../pi-session.js");
for (const session of crashSessionPool.__piSessionPoolEntriesForTests?.() || []) {
  if (session._proc) try { session._proc.kill(); } catch {}
}
await new Promise((resolve) => setTimeout(resolve, 100));
const crash2 = launchRuntimeRun({
  agentId: "pi-crash-worker",
  agentInfo: crashAgentInfo,
  run: { from: "dashboard", subject: "Pi crash 2", body: "Say hello again", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await crash2.promise;
const crashArgv = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
assert.equal(crashArgv.length, 2, "crashed persistent child should respawn on the next dispatch");

// shutdownAllPiSessions cleanly drains the pool.
__resetPiSessionPoolForTests();
const shutdownAgentInfo = {
  agentId: "pi-shutdown-worker",
  role: "coder",
  runtime: "pi",
  sessionMode: "managed",
  cwd: process.cwd(),
  runtimeConfig: { timeoutMs: 5000 },
};
const shutdownCtrl = launchRuntimeRun({
  agentId: "pi-shutdown-worker",
  agentInfo: shutdownAgentInfo,
  run: { from: "dashboard", subject: "Pi shutdown", body: "Say hello", executionMode: "managed" },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await shutdownCtrl.promise;
assert.equal(__piSessionPoolSize(), 1, "pool should retain agent session for reuse after a successful turn");
await shutdownAllPiSessions("test");
assert.equal(__piSessionPoolSize(), 0, "shutdownAllPiSessions should drain the pool");

// Phase 2: terminalSinkProvider is invoked once per managed pi session
// acquisition, the sink receives synthesized frames (ready, agent_*, prompt
// echo), and frames flow before the dispatch's resolution.
__resetPiSessionPoolForTests();
fs.writeFileSync(argvCapturePath, "");
const sinkAgentInfo = {
  agentId: "pi-sink-worker",
  role: "coder",
  runtime: "pi",
  sessionMode: "managed",
  cwd: process.cwd(),
  runtimeConfig: { timeoutMs: 5000 },
};
const sinkFrames = [];
let sinkProviderCalls = 0;
const sinkController = launchRuntimeRun({
  agentId: "pi-sink-worker",
  agentInfo: sinkAgentInfo,
  run: { from: "dashboard", subject: "Pi sink", body: "Say hello", executionMode: "managed" },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
    terminalSinkProvider: async ({ agentId: providedAgentId }) => {
      sinkProviderCalls++;
      assert.equal(providedAgentId, "pi-sink-worker");
      return async (output) => {
        sinkFrames.push(output);
      };
    },
  },
});
await sinkController.promise;
// Let the async sink chain drain (sink is invoked from the flush microtask chain).
await new Promise((resolve) => setTimeout(resolve, 50));
assert.equal(sinkProviderCalls, 1, "terminalSinkProvider should be called once per acquired managed pi session");
const sinkJoined = sinkFrames.join("");
assert(sinkJoined.includes("[pi rpc ready]"), `expected ready frame, got ${JSON.stringify(sinkJoined.slice(0, 200))}`);
assert(sinkJoined.includes("> Say hello"), `expected prompt echo, got ${JSON.stringify(sinkJoined.slice(0, 200))}`);
await shutdownAllPiSessions("sink-test");

await shutdownAllPiSessions("test final");
console.log("pi-runtime.test.js: all assertions passed");
