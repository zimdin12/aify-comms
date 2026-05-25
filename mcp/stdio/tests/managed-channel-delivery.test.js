// Plan 5 (2026-05-25) — controller routing for channel-mode delivery on
// wrapper-backed managed dispatches.
//
// Server-side _agent_execution_mode (api_v2.py:1047) sets execution_mode=
// 'channel' for codex/hermes/pi when wrapper-backed. The bridge (main
// bridge OR wrapper child) claims via executionModes=['channel'] (Plan 5
// Task B1 in mcp/stdio/dispatch-execution.js) and the service whitelists
// these runtimes for channel-claim (Plan 5 Task B2 widens
// _CHANNEL_CLAIM_RUNTIMES). This test pins the per-runtime controller
// routing: when launchRuntimeRun receives executionMode='channel', the
// adapter.controllerFor(opts) must return the controller whose delivery
// path actually talks to the runtime backing (codex app-server WS, hermes
// tui_gateway WS, pi RPC session). That path is identical to the resident-
// channel path established in Plan 3, so the assertion is "same controller
// the resident-channel path picks for executionMode='channel'".

import { test } from "node:test";
import assert from "node:assert/strict";
import { CodexAdapter } from "../adapters/codex.js";
import { HermesAdapter } from "../adapters/hermes.js";
import { PiAdapter } from "../adapters/pi.js";
import { CodexController } from "../controllers/codex-controller.js";
import { HermesController } from "../controllers/hermes-controller.js";
import { PiController } from "../controllers/pi-controller.js";

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

test("pi managed + wrapper-backed + channel-mode lands on PiController (managed delivery path)", () => {
  const adapter = new PiAdapter();
  const ctrl = adapter.controllerFor(commonOpts("pi"));
  assert.ok(ctrl, "expected controller for wrapper-backed managed channel pi");
  assert.ok(
    ctrl instanceof PiController,
    `expected PiController; got ${ctrl?.constructor?.name}`,
  );
  // pi's adapter rejects resident-mode only; channel-mode falls through to
  // PiController (acquirePiSession) — same delivery actor used by managed.
});
