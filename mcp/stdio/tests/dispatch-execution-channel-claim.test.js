// Plan 5 (2026-05-25) — symmetric channel-claim for wrapper-backed managed
// codex/hermes/pi. Pins the main bridge's claim contract: when the agent is
// recorded as sessionMode='managed' and the runtime is in
// `managedViaWrapperRuntimes`, the main bridge must claim 'channel' so the
// queued execution_mode='channel' run (set by api_v2.py:1047) actually gets
// picked up. Without this, those runs sit queued forever — observed
// 2026-05-25 for graph-senior-dev (codex managed), pi managed, and hermes
// managed.

import { test } from "node:test";
import assert from "node:assert/strict";
import { supportedExecutionModes } from "../dispatch-execution.js";

const WRAPPER_BACKED = new Set(["codex", "hermes", "pi"]);

test("codex managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "codex", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
});

test("hermes managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "hermes", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
});

test("pi managed + wrapper-backed pushes 'channel'", () => {
  const modes = supportedExecutionModes(
    { runtime: "pi", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: WRAPPER_BACKED },
  );
  assert.deepEqual(modes, ["channel"]);
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

test("opencode managed + wrapper-backed does NOT push 'channel' (opencode out of scope for Plan 5)", () => {
  const modes = supportedExecutionModes(
    { runtime: "opencode", sessionMode: "managed", capabilities: ["native-managed-run"] },
    { managedViaWrapperRuntimes: new Set(["opencode"]) },
  );
  assert.deepEqual(modes, []);
});
