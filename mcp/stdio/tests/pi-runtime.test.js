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
  launchCwdProblem,
  launchRuntimeRun,
  normalizeRuntime,
  runtimeLaunchAvailability,
} = await import("../runtimes.js");

assert.equal(normalizeRuntime("pi"), "pi");
assert.equal(normalizeRuntime("omp"), "pi");
assert.equal(normalizeRuntime("oh-my-pi"), "pi");
assert.equal(normalizeRuntime("pi-agent"), "pi");

assert.equal(canLaunchRuntime("pi"), true);
assert.deepEqual(controlCapabilitiesForRuntime("pi"), { interrupt: true, steer: true });
assert.deepEqual(defaultCapabilitiesForRuntime("pi", "managed"), ["managed-run", "resume", "interrupt", "steer", "spawn"]);
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

fs.writeFileSync(fakeOmp, `#!/usr/bin/env node
import fs from "fs";
import readline from "readline";
if (process.env.AIFY_PI_ARGV_CAPTURE) {
  fs.appendFileSync(process.env.AIFY_PI_ARGV_CAPTURE, JSON.stringify(process.argv.slice(2)) + "\\n");
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

console.log("pi-runtime.test.js: all assertions passed");
