import assert from "assert";
import test from "node:test";
import { PiAdapter } from "../../adapters/pi.js";

test("PiAdapter identity", () => {
  const a = new PiAdapter();
  assert.strictEqual(a.name, "pi");
  assert.strictEqual(a.displayName, "Pi");
  assert.deepStrictEqual(a.sessionEnvVars, ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]);
});

test("PiAdapter prefers PI_SESSION_ID", () => {
  process.env.PI_SESSION_ID = "pi-1";
  process.env.OMP_SESSION_ID = "omp-1";
  process.env.AIFY_PI_SESSION_ID = "aify-pi-1";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "pi-1");
  delete process.env.PI_SESSION_ID;
  delete process.env.OMP_SESSION_ID;
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter falls back to OMP_SESSION_ID", () => {
  delete process.env.PI_SESSION_ID;
  process.env.OMP_SESSION_ID = "omp-2";
  process.env.AIFY_PI_SESSION_ID = "aify-pi-2";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "omp-2");
  delete process.env.OMP_SESSION_ID;
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter falls back to AIFY_PI_SESSION_ID last", () => {
  delete process.env.PI_SESSION_ID;
  delete process.env.OMP_SESSION_ID;
  process.env.AIFY_PI_SESSION_ID = "aify-pi-3";
  const a = new PiAdapter();
  assert.strictEqual(a.getCurrentSessionId(), "aify-pi-3");
  delete process.env.AIFY_PI_SESSION_ID;
});

test("PiAdapter normalizeModelOverride strips placeholders (pi-specific regression)", () => {
  const a = new PiAdapter();
  assert.strictEqual(a.normalizeModelOverride("unknown"), "");
  assert.strictEqual(a.normalizeModelOverride("gpt-5.5"), "gpt-5.5");
});

test("PiAdapter Plan 2 capabilities — pi flip key declarations", () => {
  const a = new PiAdapter();
  // pi is single-client RPC; resident impossible
  assert.strictEqual(a.supportsResident, false);
  assert.strictEqual(a.supportsManaged, true);
  assert.strictEqual(a.supportsSteering, true);
  assert.strictEqual(a.supportsInterrupt, true);
  assert.strictEqual(a.supportsMultiClient, false);
  assert.strictEqual(a.preferredDeliveryMode, "managed-via-wrapper");
});
