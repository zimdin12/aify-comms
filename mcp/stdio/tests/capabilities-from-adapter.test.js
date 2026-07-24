// Regression: bridge-side capability helpers now derive from the adapter.
// Pi resident no longer advertises `resident-run` (Plan 2 pi flip).
import assert from "assert";
import test from "node:test";
import { supportedRuntimes } from "../adapters/index.js";
import {
  defaultCapabilitiesForRuntime,
  controlCapabilitiesForRuntime,
} from "../runtimes.js";

test("pi resident no longer advertises resident-run", () => {
  const caps = defaultCapabilitiesForRuntime("pi", "resident", "session-x", {});
  assert.ok(!caps.includes("resident-run"),
    `pi resident must not have resident-run after Plan 2 — got ${JSON.stringify(caps)}`);
});

test("pi managed still has managed-run + steer + interrupt", () => {
  const caps = defaultCapabilitiesForRuntime("pi", "managed", "", {});
  assert.ok(caps.includes("managed-run"));
  assert.ok(caps.includes("steer"));
  assert.ok(caps.includes("interrupt"));
});

test("claude resident still has resident-run", () => {
  const caps = defaultCapabilitiesForRuntime("claude-code", "resident", "session-x", {});
  assert.ok(caps.includes("resident-run"));
});

test("hermes managed advertises native steer", () => {
  const caps = controlCapabilitiesForRuntime("hermes", "managed");
  assert.equal(caps.steer, true);
});

test("opencode managed has native promptAsync steer", () => {
  const caps = defaultCapabilitiesForRuntime("opencode", "managed", "", {});
  assert.ok(caps.includes("managed-run"));
  assert.ok(caps.includes("interrupt"));
  assert.ok(caps.includes("steer"));
});

test("controlCapabilitiesForRuntime derives from adapter for pi", () => {
  const caps = controlCapabilitiesForRuntime("pi");
  // PiAdapter.supportsSteering == true, supportsInterrupt == true
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, true);
});

test("controlCapabilitiesForRuntime derives from adapter for opencode", () => {
  const caps = controlCapabilitiesForRuntime("opencode");
  // OpencodeAdapter supports promptAsync steer and interrupt.
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, true);
});

test("every managed harness advertises ordinary-send steer", () => {
  for (const runtime of supportedRuntimes()) {
    assert.strictEqual(
      controlCapabilitiesForRuntime(runtime).steer,
      true,
      `${runtime} must expose its native busy-input path as steer`,
    );
  }
});
