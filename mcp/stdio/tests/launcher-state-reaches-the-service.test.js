// What the launcher exported has to arrive at the service, or exporting it changed nothing.
//
// aify-wrapper now exports HARNESS_WRAPPER_VERSION and HARNESS_REGISTRY_FINGERPRINT into the runtime.
// A launcher REPORTS its state; it does not host a doctor. For that to mean anything the bridge has to
// carry them to the control plane, so "which launcher started this session, and which registry was it
// built against" is answerable from inside the container instead of by opening a file on the host --
// which is the only reason aify-comms carries a host-side launcher check at all.
//
// Absent is absent: a session started by something that is not one of our launchers reports nothing
// rather than an empty string that reads like a real answer.
import assert from "node:assert/strict";
import { test } from "node:test";

import { launcherStateFrom } from "../environment-identity.mjs";

test("the exported markers are carried through", () => {
  const got = launcherStateFrom({
    HARNESS_WRAPPER_VERSION: "0.6.0",
    HARNESS_REGISTRY_FINGERPRINT: "feb3b6422e2f1e55",
  });
  assert.deepEqual(got, { launcherVersion: "0.6.0", launcherRegistryFingerprint: "feb3b6422e2f1e55" });
});

test("a runtime not started by one of our launchers reports nothing", () => {
  // `{}` and not `{launcherVersion: ""}`: an empty string is a value, and a consumer cannot tell it
  // from a launcher that genuinely reported an empty version. Absent must stay absent.
  assert.deepEqual(launcherStateFrom({}), {});
  assert.deepEqual(launcherStateFrom({ HARNESS_WRAPPER_VERSION: "" }), {});
  assert.deepEqual(launcherStateFrom({ HARNESS_WRAPPER_VERSION: "   " }), {});
});

test("a half-reporting launcher reports the half it has", () => {
  assert.deepEqual(launcherStateFrom({ HARNESS_WRAPPER_VERSION: "0.6.0" }), { launcherVersion: "0.6.0" });
  assert.deepEqual(
    launcherStateFrom({ HARNESS_REGISTRY_FINGERPRINT: "abc123" }),
    { launcherRegistryFingerprint: "abc123" },
  );
});

test("an unrendered placeholder is not a version", () => {
  // install.sh substitutes these; a launcher that escaped substitution must not report `@@...@@` as
  // though it were a real build.
  assert.deepEqual(launcherStateFrom({ HARNESS_WRAPPER_VERSION: "@@WRAPPER_VERSION@@" }), {});
  assert.deepEqual(launcherStateFrom({ HARNESS_REGISTRY_FINGERPRINT: "@@REGISTRY_FINGERPRINT@@" }), {});
});
