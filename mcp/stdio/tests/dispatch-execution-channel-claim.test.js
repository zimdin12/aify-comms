// Wrapper-backed managed codex/hermes runs are queued as channel-mode, but
// only the wrapper PTY's child bridge should claim them. The environment
// bridge must stay out of that path; otherwise it can race the child bridge
// and drive stale runtimeConfig instead of the visible *-aify console.

import { test } from "node:test";
import assert from "node:assert/strict";
import { supportedExecutionModes } from "../dispatch-execution.js";

const WRAPPER_BACKED = new Set(["codex", "hermes"]);

test("codex managed + wrapper-backed does not push main-bridge claim modes", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, []);
});

test("hermes managed + wrapper-backed does not push main-bridge claim modes", () => {
  const modes = supportedExecutionModes(
    { runtime: "hermes", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, []);
});

test("pi managed stays native managed even if settings accidentally include pi", () => {
  const modes = supportedExecutionModes(
    { runtime: "pi", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: new Set(["pi"]) },
  );
  assert.deepEqual(modes, ["managed"]);
});

test("codex managed + NOT wrapper-backed still pushes 'managed' (legacy path)", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: new Set() },
  );
  assert.deepEqual(modes, ["managed"]);
});

test("resident codex still returns 'resident' regardless of wrapper-backed flag", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "resident", capabilities: ["resident-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["resident"]);
});

test("opencode managed ignores wrapper-backed setting and stays native managed", () => {
  const modes = supportedExecutionModes(
    { runtime: "opencode", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: new Set(["opencode"]) },
  );
  assert.deepEqual(modes, ["managed"]);
});
