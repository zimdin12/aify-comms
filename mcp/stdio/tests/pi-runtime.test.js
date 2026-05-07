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
  launchRuntimeRun,
  normalizeRuntime,
  runtimeLaunchAvailability,
} = await import("../runtimes.js");

assert.equal(normalizeRuntime("pi"), "pi");
assert.equal(normalizeRuntime("omp"), "pi");
assert.equal(normalizeRuntime("oh-my-pi"), "pi");
assert.equal(normalizeRuntime("pi-agent"), "pi");

assert.equal(canLaunchRuntime("pi"), true);
assert.deepEqual(controlCapabilitiesForRuntime("pi"), { interrupt: true, steer: false });
assert.deepEqual(defaultCapabilitiesForRuntime("pi", "managed"), ["managed-run", "resume", "interrupt", "spawn"]);
assert.deepEqual(defaultCapabilitiesForRuntime("pi", "resident", "session-123"), ["resident-run", "resume", "interrupt"]);

process.env.PI_SESSION_ID = "pi-session-123";
assert.equal(defaultSessionHandleForRuntime("pi"), "pi-session-123");

const availability = runtimeLaunchAvailability("pi");
assert.equal(availability.available, true);
assert.match(availability.message, /Pi launcher available/);

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-pi-runtime-"));
const fakeOmp = path.join(tmpDir, "fake-omp.mjs");
fs.writeFileSync(fakeOmp, `#!/usr/bin/env node
import readline from "readline";
console.log(JSON.stringify({ type: "ready", sessionId: "pi-session-fake" }));
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const message = JSON.parse(line);
  if (message.type === "prompt") {
    console.log(JSON.stringify({ id: message.id, type: "response", command: "prompt", success: true, sessionId: "pi-session-fake" }));
    console.log(JSON.stringify({ type: "agent_start" }));
    console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "hello " } }));
    console.log(JSON.stringify({ type: "message_update", assistantMessageEvent: { type: "text_delta", delta: "from pi" } }));
    console.log(JSON.stringify({ type: "agent_end", id: "turn-fake", sessionId: "pi-session-fake" }));
  }
});
`, { mode: 0o755 });

process.env.AIFY_PI_COMMAND = fakeOmp;
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

console.log("pi-runtime.test.js: all assertions passed");
