import assert from "assert";
import test from "node:test";
import { OpencodeAdapter } from "../../adapters/opencode.js";

test("OpencodeAdapter identity", () => {
  const a = new OpencodeAdapter();
  assert.strictEqual(a.name, "opencode");
  assert.strictEqual(a.displayName, "OpenCode");
  assert.deepStrictEqual(a.sessionEnvVars, ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]);
});

test("OpencodeAdapter prefers OPENCODE_SESSION_ID", () => {
  process.env.OPENCODE_SESSION_ID = "oc-primary";
  process.env.OPENCODE_SESSION = "oc-fallback";
  const a = new OpencodeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "oc-primary");
  delete process.env.OPENCODE_SESSION_ID;
  delete process.env.OPENCODE_SESSION;
});

test("OpencodeAdapter falls back to OPENCODE_SESSION", () => {
  delete process.env.OPENCODE_SESSION_ID;
  process.env.OPENCODE_SESSION = "oc-fallback-only";
  const a = new OpencodeAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "oc-fallback-only");
  delete process.env.OPENCODE_SESSION;
});

test("OpencodeAdapter Plan 2 capabilities", () => {
  const a = new OpencodeAdapter();
  // aify-comms doesn't wire `opencode serve` today — capabilities reflect
  // current aify-comms delivery surface, not what opencode CAN do.
  assert.strictEqual(a.supportsResident, false);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, false);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, false);
  assert.strictEqual(a.preferredDeliveryMode, "managed");
});
