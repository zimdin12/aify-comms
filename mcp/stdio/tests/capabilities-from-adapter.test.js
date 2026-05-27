// Regression: bridge-side capability helpers now derive from the adapter.
// Pi resident no longer advertises `resident-run` (Plan 2 pi flip).
import assert from "assert";
import test from "node:test";
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

test("opencode managed has no steer", () => {
  const caps = defaultCapabilitiesForRuntime("opencode", "managed", "", {});
  assert.ok(caps.includes("managed-run"));
  assert.ok(caps.includes("interrupt"));
  assert.ok(!caps.includes("steer"));
});

test("controlCapabilitiesForRuntime derives from adapter for pi", () => {
  const caps = controlCapabilitiesForRuntime("pi");
  // PiAdapter.supportsSteering == true, supportsInterrupt == true
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, true);
});

test("controlCapabilitiesForRuntime derives from adapter for opencode", () => {
  const caps = controlCapabilitiesForRuntime("opencode");
  // OpencodeAdapter.supportsSteering == false, supportsInterrupt == true
  assert.strictEqual(caps.interrupt, true);
  assert.strictEqual(caps.steer, false);
});
