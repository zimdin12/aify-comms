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

assert.equal(normalizeRuntime("hermes"), "hermes");
assert.equal(normalizeRuntime("hermes-agent"), "hermes");
assert.equal(normalizeRuntime("hermes_agent"), "hermes");

assert.equal(canLaunchRuntime("hermes"), true);
// 89ad862 (2026-06-03, native-session-id rework review fixes): steer is back —
// HermesAdapter.supportsSteering is true again because the gateway delivery
// loop (hermes-managed-host.js) injects mid-turn via the gateway `session.steer`
// RPC on a 4009-busy. Matches service/runtimes/hermes.py supports_steering=True.
// (The 2026-05-30 api_server chat path that had no steer was retired.)
assert.deepEqual(controlCapabilitiesForRuntime("hermes"), { interrupt: true, steer: true });
// defaultCapabilitiesForRuntime derives from the HermesAdapter supports_*
// flags. "steer" appears again (supportsSteering == true since 89ad862).
assert.deepEqual(
  defaultCapabilitiesForRuntime("hermes", "managed"),
  ["managed-run", "resume", "interrupt", "steer", "spawn"],
);
assert.deepEqual(defaultCapabilitiesForRuntime("hermes", "resident", "session-123"), ["resume", "interrupt", "steer"]);
assert.equal(defaultSessionHandleForRuntime("hermes"), "hermes-session-123");

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
