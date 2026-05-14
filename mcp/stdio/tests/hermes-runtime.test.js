#!/usr/bin/env node
import assert from "assert";

process.env.AIFY_HERMES_COMMAND = process.execPath;
process.env.HERMES_SESSION_ID = "hermes-session-123";

const {
  canLaunchRuntime,
  controlCapabilitiesForRuntime,
  defaultCapabilitiesForRuntime,
  defaultSessionHandleForRuntime,
  normalizeRuntime,
  runtimeLaunchAvailability,
} = await import("../runtimes.js");

assert.equal(normalizeRuntime("hermes"), "hermes");
assert.equal(normalizeRuntime("hermes-agent"), "hermes");
assert.equal(normalizeRuntime("hermes_agent"), "hermes");

assert.equal(canLaunchRuntime("hermes"), true);
assert.deepEqual(controlCapabilitiesForRuntime("hermes"), { interrupt: true, steer: false });
assert.deepEqual(defaultCapabilitiesForRuntime("hermes", "managed"), ["managed-run", "resume", "interrupt", "spawn"]);
assert.deepEqual(defaultCapabilitiesForRuntime("hermes", "resident", "session-123"), ["resident-run", "resume", "interrupt"]);
assert.equal(defaultSessionHandleForRuntime("hermes"), "hermes-session-123");

const availability = runtimeLaunchAvailability("hermes");
assert.equal(availability.available, true);
assert.match(availability.message, /Hermes launcher available/);

console.log("hermes-runtime.test.js: all assertions passed");
