import assert from "node:assert/strict";
import test from "node:test";
import { managedViaWrapperRuntimesFromSettingsResponse } from "../managed-wrapper-settings.js";

test("managed_via_wrapper parser accepts flat /settings response", () => {
  const runtimes = managedViaWrapperRuntimesFromSettingsResponse({
    managed_via_wrapper: ["codex", "hermes", "pi", "opencode"],
  });
  assert.deepEqual([...runtimes].sort(), ["codex", "hermes"]);
});

test("managed_via_wrapper parser accepts legacy nested settings response", () => {
  const runtimes = managedViaWrapperRuntimesFromSettingsResponse({
    settings: { managed_via_wrapper: true },
  });
  assert.deepEqual([...runtimes].sort(), ["codex", "hermes"]);
});
