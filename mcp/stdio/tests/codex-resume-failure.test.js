#!/usr/bin/env node
// Unit test for detectCodexResumeFailure — the classifier that decides
// whether a thread/resume error should trigger the auto-heal path.
//
// This test exists because the same "AbsolutePathBuf deserialized without
// a base path" bug kept resurfacing during a single debugging session:
// each fix was shipped without a test that proved the heal branch would
// actually fire on the real Codex error string. This locks down the
// classification so a future regression is caught at test time rather
// than during a live dispatch.
//
// Run: node mcp/stdio/tests/codex-resume-failure.test.js

import assert from "node:assert/strict";

const { detectCodexResumeFailure } = await import("../codex-errors.js");

// Helper: treat every case as either {message: "..."} (error object) or
// a bare string, since the real runtime flow receives errors from the
// JSON-RPC client in several shapes.
function cls(input) {
  return detectCodexResumeFailure(input);
}

// --- Corrupt rollout (the bug we kept chasing) ---

const corruptSamples = [
  { message: "Invalid request: AbsolutePathBuf deserialized without a base path" },
  { message: "AbsolutePathBuf deserialized without a base path" },
  { message: "rpc error: AbsolutePathBufGuard::new called without a base path" },
  new Error("Invalid request: AbsolutePathBuf deserialized without a base path"),
  // bare string, which happens when a JSON-RPC client serializes the error differently
  "Invalid request: AbsolutePathBuf deserialized without a base path",
];

for (const sample of corruptSamples) {
  const r = cls(sample);
  assert.equal(r.shouldHeal, true, `shouldHeal must be true for corrupt sample: ${JSON.stringify(sample)}`);
  assert.equal(r.corruptRollout, true, `corruptRollout must be true for corrupt sample`);
  assert.equal(r.noRollout, false, `noRollout must be false for corrupt sample`);
  assert.equal(r.oversizedRollout, false, `oversizedRollout must be false for corrupt sample`);
  assert.equal(r.healReason, "corrupt_rollout", `healReason must be "corrupt_rollout"`);
}

// --- Missing rollout (pre-existing heal path that must still work) ---

const noRolloutSamples = [
  { message: "no rollout found for thread id abc-123" },
  { message: "codex: no rollout found for thread id xyz" },
  new Error("no rollout found for thread id 019d925d-8c04-7d83-84e2-012ef4ec5555"),
];

for (const sample of noRolloutSamples) {
  const r = cls(sample);
  assert.equal(r.shouldHeal, true, `shouldHeal must be true for no-rollout sample`);
  assert.equal(r.noRollout, true, `noRollout must be true`);
  assert.equal(r.corruptRollout, false, `corruptRollout must be false`);
  assert.equal(r.oversizedRollout, false, `oversizedRollout must be false`);
  assert.equal(r.healReason, "no_rollout", `healReason must be "no_rollout"`);
}

// --- Oversized rollout/context (Codex websocket frame limit) ---

const oversizedSamples = [
  { message: "ERROR: remote app server at `ws://127.0.0.1:34577/` transport failed: Space limit exceeded: Message too long: 23456629 > 16777216" },
  new Error("transport failed: Space limit exceeded: Message too long: 23456629 > 16777216"),
  "Message too long: 23456629 > 16777216",
];

for (const sample of oversizedSamples) {
  const r = cls(sample);
  assert.equal(r.shouldHeal, true, `shouldHeal must be true for oversized rollout sample`);
  assert.equal(r.noRollout, false, `noRollout must be false`);
  assert.equal(r.corruptRollout, false, `corruptRollout must be false`);
  assert.equal(r.oversizedRollout, true, `oversizedRollout must be true`);
  assert.equal(r.healReason, "oversized_rollout", `healReason must be "oversized_rollout"`);
}

// --- Unrelated errors must NOT trigger heal ---

const nonHealSamples = [
  { message: "Invalid request: missing field 'model'" },
  { message: "thread/start returned non-success" },
  { message: "WebSocket closed unexpectedly" },
  new Error("ECONNREFUSED 127.0.0.1:12345"),
  "Some other random rpc error",
  // empty/null error should not crash the classifier, and must not heal
  null,
  undefined,
  "",
];

for (const sample of nonHealSamples) {
  const r = cls(sample);
  assert.equal(r.shouldHeal, false, `shouldHeal must be false for: ${JSON.stringify(sample)}`);
  assert.equal(r.corruptRollout, false);
  assert.equal(r.noRollout, false);
  assert.equal(r.oversizedRollout, false);
  assert.equal(r.healReason, null);
}

console.log("codex-resume-failure.test.js: all assertions passed");
