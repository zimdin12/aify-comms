// Wrapper-backed managed codex/hermes runs are queued as channel-mode, but
// only the wrapper PTY's child bridge should claim them. The environment
// bridge must stay out of that path; otherwise it can race the child bridge
// and drive stale runtimeConfig instead of the visible *-aify console.

import { test } from "node:test";
import assert from "node:assert/strict";
import { supportedExecutionModes, wrapperChildExecutionModes } from "../dispatch-execution.js";

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

// wrapperChildExecutionModes: the in-session wrapper-child augmentation that
// server.js applies when AIFY_MANAGED_VIA_WRAPPER=1. Codex's wrapper child IS
// the delivery surface so it claims channel/resident; hermes' wrapper child is
// the thin --tui (a WS client), so the hermes-managed-host.js loop owns channel
// delivery and the wrapper child must NOT claim it (else it races the loop and
// auto-mirrors a fabricated reply via ChannelDelegatedController).

test("wrapperChildExecutionModes: codex wrapper child adds channel + resident", () => {
  const modes = wrapperChildExecutionModes([], { runtime: "codex", isWrapperChild: true });
  assert.ok(modes.includes("channel"), "codex wrapper child claims channel");
  assert.ok(modes.includes("resident"), "codex wrapper child claims resident");
});

test("wrapperChildExecutionModes: HERMES wrapper child does NOT add channel/resident (loop owns it)", () => {
  const modes = wrapperChildExecutionModes(["managed"], { runtime: "hermes", isWrapperChild: true });
  assert.deepEqual(modes, ["managed"], "hermes wrapper child must not race the managed-host loop");
  assert.ok(!modes.includes("channel"), "no channel claim for hermes wrapper child");
  assert.ok(!modes.includes("resident"), "no resident claim for hermes wrapper child");
});

test("wrapperChildExecutionModes: non-wrapper-child is unchanged", () => {
  const modes = wrapperChildExecutionModes(["managed"], { runtime: "codex", isWrapperChild: false });
  assert.deepEqual(modes, ["managed"]);
});

test("wrapperChildExecutionModes: does not duplicate modes already present", () => {
  const modes = wrapperChildExecutionModes(["channel"], { runtime: "codex", isWrapperChild: true });
  assert.deepEqual(modes.filter((m) => m === "channel").length, 1);
  assert.ok(modes.includes("resident"));
});
