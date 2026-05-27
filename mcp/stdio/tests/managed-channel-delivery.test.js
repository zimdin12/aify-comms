// Plan 5 (2026-05-25) — controller routing for channel-mode delivery on
// wrapper-backed managed dispatches.
//
// Server-side _agent_execution_mode (api_v2.py:1047) sets execution_mode=
// 'channel' for codex/hermes when wrapper-backed. Only the wrapper PTY's
// child bridge is allowed to claim those channel runs; the main environment
// bridge deliberately advertises no channel mode for wrapper-backed managed
// codex/hermes. This test pins the per-runtime controller
// routing: when launchRuntimeRun receives executionMode='channel', the
// adapter.controllerFor(opts) must return the controller whose delivery
// path actually talks to the runtime backing (codex app-server WS, hermes
// tui_gateway WS). That path is identical to the resident-
// channel path established in Plan 3, so the assertion is "same controller
// the resident-channel path picks for executionMode='channel'".

import { test } from "node:test";
import assert from "node:assert/strict";
import { CodexAdapter } from "../adapters/codex.js";
import { HermesAdapter } from "../adapters/hermes.js";
import { CodexController } from "../controllers/codex-controller.js";
import { HermesController } from "../controllers/hermes-controller.js";

function commonOpts(runtime, overrides = {}) {
  return {
    agentId: `${runtime}-mgr-wb-test`,
    agentInfo: {
      agentId: `${runtime}-mgr-wb-test`,
      runtime,
      sessionMode: "managed",
      runtimeConfig: overrides.runtimeConfig || {},
      cwd: process.cwd(),
    },
    run: { id: "r1", executionMode: "channel", subject: "ch", body: "x", from: "y" },
    runtimeState: {},
    callbacks: { onEvent: () => {}, onRefs: () => {} },
    executionMode: "channel",
    managedViaWrapper: true,
    ...overrides,
  };
}

test("codex managed + wrapper-backed + channel-mode lands on CodexController (channel branch)", () => {
  const adapter = new CodexAdapter();
  const ctrl = adapter.controllerFor(
    commonOpts("codex", { runtimeConfig: { appServerUrl: "ws://127.0.0.1:5599" } }),
  );
  assert.ok(ctrl, "expected controller for wrapper-backed managed channel codex");
  assert.ok(
    ctrl instanceof CodexController,
    `expected CodexController; got ${ctrl?.constructor?.name}`,
  );
  // Plan 5 ensures the delegated-managed short-circuit (line 68 of
  // codex-controller.js) only fires for executionMode='managed'. With
  // executionMode='channel' we must reach the real delivery path.
  const impl = ctrl._impl;
  assert.ok(impl, "CodexController must have an _impl picked");
  assert.notEqual(
    impl.constructor.name,
    "DelegatedManagedController",
    "wrapper-backed CHANNEL-mode codex must NOT short-circuit to DelegatedManagedController",
  );
});

test("hermes managed + wrapper-backed + channel-mode lands on HermesController (channel branch)", () => {
  const adapter = new HermesAdapter();
  const ctrl = adapter.controllerFor(
    commonOpts("hermes", { runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" } }),
  );
  assert.ok(ctrl, "expected controller for wrapper-backed managed channel hermes");
  assert.ok(
    ctrl instanceof HermesController,
    `expected HermesController; got ${ctrl?.constructor?.name}`,
  );
  const impl = ctrl._impl;
  assert.ok(impl, "HermesController must have an _impl picked");
  assert.notEqual(
    impl.constructor.name,
    "DelegatedManagedController",
    "wrapper-backed CHANNEL-mode hermes must NOT short-circuit to DelegatedManagedController",
  );
});
