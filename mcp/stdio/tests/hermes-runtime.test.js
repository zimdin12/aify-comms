#!/usr/bin/env node
import assert from "assert";
import fs from "fs";
import os from "os";
import path from "path";

process.env.AIFY_HERMES_COMMAND = process.execPath;
process.env.HERMES_SESSION_ID = "hermes-session-123";

const {
  canLaunchRuntime,
  controlCapabilitiesForRuntime,
  defaultCapabilitiesForRuntime,
  defaultSessionHandleForRuntime,
  launchRuntimeRun,
  normalizeRuntime,
  runtimeLaunchAvailability,
} = await import("../runtimes.js");

assert.equal(normalizeRuntime("hermes"), "hermes");
assert.equal(normalizeRuntime("hermes-agent"), "hermes");
assert.equal(normalizeRuntime("hermes_agent"), "hermes");

assert.equal(canLaunchRuntime("hermes"), true);
assert.deepEqual(controlCapabilitiesForRuntime("hermes"), { interrupt: true, steer: false });
// Phase 3 (hermes parity): managed hermes is now a real native-RPC adapter,
// not a Console-only stub. Capabilities advertise managed-run + native-managed-run.
assert.deepEqual(
  defaultCapabilitiesForRuntime("hermes", "managed"),
  ["managed-run", "native-managed-run", "resume", "interrupt", "spawn"],
);
assert.deepEqual(defaultCapabilitiesForRuntime("hermes", "resident", "session-123"), ["resident-run", "resume", "interrupt"]);
assert.equal(defaultSessionHandleForRuntime("hermes"), "hermes-session-123");

const availability = runtimeLaunchAvailability("hermes");
assert.equal(availability.available, true);
assert.match(availability.message, /Hermes launcher available/);

// ── End-to-end: createHermesController spawns a fake `hermes chat -q -Q`
// and resolves with the captured stdout as the reply. ─────────────────────

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-hermes-runtime-"));
const fakeHermes = path.join(tmpDir, "fake-hermes.mjs");
const argvCapturePath = path.join(tmpDir, "fake-hermes-argv.jsonl");

fs.writeFileSync(fakeHermes, `#!/usr/bin/env node
import fs from "fs";
if (process.env.AIFY_HERMES_ARGV_CAPTURE) {
  fs.appendFileSync(process.env.AIFY_HERMES_ARGV_CAPTURE, JSON.stringify(process.argv.slice(2)) + "\\n");
}
if (process.env.AIFY_HERMES_FAIL === "1") {
  process.stderr.write("Hermes provider missing API key. Run 'hermes config check'.\\n");
  process.exit(2);
}
if (process.env.AIFY_HERMES_HANG === "1") {
  setInterval(() => {}, 1000);
  await new Promise(() => {});
}
const argv = process.argv.slice(2);
const qIdx = argv.indexOf("-q");
const prompt = qIdx >= 0 ? argv[qIdx + 1] : "";
const bodyMatch = prompt.match(/Please summarize the build|anything|change topic/);
const echoed = bodyMatch ? bodyMatch[0] : "(no body)";
const reply = (process.env.AIFY_HERMES_REPLY || "[hermes ack] {{prompt}}").replace("{{prompt}}", echoed);
process.stdout.write(reply);
process.exit(0);
`);
fs.chmodSync(fakeHermes, 0o755);

process.env.AIFY_HERMES_COMMAND = fakeHermes;
process.env.AIFY_HERMES_ARGV_CAPTURE = argvCapturePath;

const sinkFrames = [];
const sinkStatuses = [];
const controller = launchRuntimeRun({
  agentId: "hermes-worker",
  agentInfo: {
    agentId: "hermes-worker",
    role: "coder",
    runtime: "hermes",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Hermes smoke",
    body: "Please summarize the build",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
    terminalSinkProvider: async () => async (output, status) => {
      sinkFrames.push(String(output || ""));
      if (status) sinkStatuses.push(String(status));
    },
  },
});
const result = await controller.promise;
assert.equal(result.status, "completed");
assert.match(result.summary, /\[hermes ack\] Please summarize the build/, result.summary);

// argv captured by the fake should include -Q (programmatic mode) BEFORE -q,
// and the full system+user prompt as the -q argument.
const argvLines = fs.readFileSync(argvCapturePath, "utf8").trim().split(/\r?\n/).filter(Boolean);
assert.equal(argvLines.length, 1);
const argv = JSON.parse(argvLines[0]);
assert.equal(argv[0], "chat");
assert.equal(argv[1], "-Q");
assert.equal(argv[2], "-q");
assert(argv[3].includes("Please summarize the build"), `expected prompt to embed body, got "${argv[3].slice(0, 200)}"`);
assert(argv.includes("--yolo"), `expected --yolo for managed runs by default, got ${JSON.stringify(argv)}`);

// Synthesized terminal sink received: a header echo, a thinking marker, and
// the reply. Late-attach via terminalSinkProvider is awaited before any
// frames push, so order is deterministic.
await new Promise((resolve) => setTimeout(resolve, 50));
const joined = sinkFrames.join("");
assert(joined.includes("> [dashboard]"), `expected dashboard header echo, got ${JSON.stringify(joined.slice(0, 200))}`);
assert(joined.includes("Please summarize the build"), `expected prompt body echo, got ${JSON.stringify(joined.slice(0, 200))}`);
assert(joined.includes("[hermes] thinking"), `expected thinking marker, got ${JSON.stringify(joined.slice(0, 200))}`);
assert(joined.includes("[hermes ack]"), `expected reply frame, got ${JSON.stringify(joined.slice(0, 200))}`);

// Steer must throw a clear error (hermes -q is single-shot, no mid-turn steer).
let steerError = null;
try {
  await controller.steer("change topic");
} catch (e) {
  steerError = e;
}
assert(steerError, "expected steer to reject");
assert.match(steerError.message, /single-shot/);

// Failure path: hermes exits non-zero → controller rejects with stderr text.
fs.writeFileSync(argvCapturePath, "");
process.env.AIFY_HERMES_FAIL = "1";
const failController = launchRuntimeRun({
  agentId: "hermes-worker",
  agentInfo: {
    agentId: "hermes-worker",
    role: "coder",
    runtime: "hermes",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 5000 },
  },
  run: {
    from: "dashboard",
    subject: "Hermes fail",
    body: "anything",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
await assert.rejects(failController.promise, /provider missing API key/);
delete process.env.AIFY_HERMES_FAIL;

// Interrupt: the controller's interrupt() method terminates the spawned proc
// and the promise resolves as cancelled.
process.env.AIFY_HERMES_HANG = "1";
const interruptController = launchRuntimeRun({
  agentId: "hermes-worker",
  agentInfo: {
    agentId: "hermes-worker",
    role: "coder",
    runtime: "hermes",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: { timeoutMs: 30000 },
  },
  run: {
    from: "dashboard",
    subject: "Hermes hang",
    body: "anything",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
});
// Give it a moment to spawn, then interrupt.
await new Promise((resolve) => setTimeout(resolve, 200));
await controller.interrupt?.(); // no-op (smoke controller already done)
await interruptController.interrupt();
const interruptResult = await interruptController.promise;
assert.equal(interruptResult.status, "cancelled");
delete process.env.AIFY_HERMES_HANG;

console.log("hermes-runtime.test.js: all assertions passed");
