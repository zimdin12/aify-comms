#!/usr/bin/env node
import assert from "assert";

process.env.AIFY_HERMES_COMMAND = process.execPath;
process.env.AIFY_HERMES_AIFY_COMMAND = process.execPath;
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
const { HermesManagedGatewaySession } = await import("../hermes-managed-gateway-session.js");

assert.equal(normalizeRuntime("hermes"), "hermes");
assert.equal(normalizeRuntime("hermes-agent"), "hermes");
assert.equal(normalizeRuntime("hermes_agent"), "hermes");

assert.equal(canLaunchRuntime("hermes"), true);
// Managed Hermes does not advertise safe mid-turn steering: active-turn submissions interrupt
// the current turn, so dispatch queues them until turn-end. Interrupt remains supported.
assert.deepEqual(controlCapabilitiesForRuntime("hermes"), { steer: false, interrupt: true });
// defaultCapabilitiesForRuntime derives from the HermesAdapter supports_*
// flags, so managed and resident Hermes both omit "steer".
assert.deepEqual(
  defaultCapabilitiesForRuntime("hermes", "managed"),
  ["managed-run", "resume", "interrupt", "spawn"],
);
assert.deepEqual(defaultCapabilitiesForRuntime("hermes", "resident", "session-123"), ["resume", "interrupt"]);
assert.equal(defaultSessionHandleForRuntime("hermes"), "hermes-session-123");

// A 4009 race in the native-gateway fallback must not turn the follow-up into an
// interrupting session.steer. The main managed-host path handles busy requeueing;
// this fallback rejects the race without interrupting the live turn.
const busyGateway = new HermesManagedGatewaySession({ agentId: "busy-hermes" });
busyGateway._state = "ready";
busyGateway.ensureStarted = async () => {};
busyGateway._resolveSessionId = async () => "busy-session";
const busyMethods = [];
busyGateway._sendRpc = async (frame) => {
  busyMethods.push(frame.method);
  throw Object.assign(new Error("session busy"), { code: 4009 });
};
await assert.rejects(
  busyGateway.runTurn({ promptText: "wait for the current turn", run: {}, callbacks: {} }),
  /session busy/,
);
assert.deepEqual(busyMethods, ["prompt.submit"]);

const availability = runtimeLaunchAvailability("hermes");
assert.equal(availability.available, true);
assert.match(availability.message, /Hermes aify wrapper available/);

// Wrapper-backed managed Hermes should not start a native hidden Hermes
// process from the environment bridge. It delegates to the child MCP bridge
// running inside the hermes-aify PTY, where gateway metadata is available and
// Dashboard Console can render the visible TUI.
const controller = launchRuntimeRun({
  agentId: "hermes-worker",
  agentInfo: {
    agentId: "hermes-worker",
    role: "coder",
    runtime: "hermes",
    sessionMode: "managed",
    cwd: process.cwd(),
    runtimeConfig: {},
  },
  run: {
    from: "dashboard",
    subject: "Hermes smoke",
    body: "Please summarize the build",
    executionMode: "managed",
  },
  runtimeState: {},
  callbacks: { onEvent: () => {}, onRuntimeState: () => {}, onRefs: () => {} },
  managedViaWrapper: true,
});
const result = await controller.promise;
assert.equal(result.status, "delegated");
assert.match(result.summary, /wrapper-PTY child bridge/);

console.log("hermes-runtime.test.js: all assertions passed");
