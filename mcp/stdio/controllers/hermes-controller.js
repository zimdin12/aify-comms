// HermesController — extracted from createHermesController in runtimes.js as
// part of Plan 3 Task 10. Top-level dispatcher: picks a per-mode controller
// at construction time and delegates start/injectMessage/interrupt/steer.
//
// Dispatch routing (preserves the legacy createHermesController logic 1:1):
//   - managed + managedViaWrapper → no-op delegated controller (wrapper's
//     child bridge claims and delivers via the wrapper's local gateway).
//   - executionMode === "channel" or "resident" + runtimeConfig.gatewayUrl →
//     HermesResidentController (resident-channel via tui_gateway WS).
//   - executionMode === "channel" or "resident" without a gateway →
//     HermesSingleShotController (legacy `hermes chat -q` per dispatch).
//   - else (managed / default) → HermesManagedController
//     (ACP-backed persistent session, or gateway-backed if
//     AIFY_HERMES_MANAGED_USE_GATEWAY=1).
//
// File budget per 500-line rule: <=400 lines. Mode-specific implementations
// live in their own files (hermes-resident-controller.js,
// hermes-managed-controller.js, hermes-single-shot-controller.js).

import { BaseController } from "./base-controller.js";
import { getRuntimeConfig } from "../runtimes-helpers.js";
import { HermesResidentController } from "./hermes-resident-controller.js";
import { HermesManagedController } from "./hermes-managed-controller.js";
import { HermesSingleShotController } from "./hermes-single-shot-controller.js";

// Lightweight delegated controller used for managed + managedViaWrapper: the
// wrapper's child bridge owns the actual dispatch, so this controller resolves
// immediately with a "delegated" status and exposes no-op control surfaces.
class DelegatedManagedController extends BaseController {
  constructor(opts) {
    super(opts);
    this._capabilities = { interrupt: false, steer: false };
    this._promise = Promise.resolve({
      status: "delegated",
      summary: "managed dispatch delegated to wrapper-PTY child bridge",
      runtimeState: {},
      externalRefs: {},
    });
  }

  start() {
    // Plan 4 ready: managed-via-wrapper delegates to wrapper PTY child bridge
    // which has its own handshake. From this bridge's perspective the
    // controller is "ready" the instant it's started.
    this.markReady();
    return {
      capabilities: this._capabilities,
      interrupt: async () => {},
      steer: async () => {},
      promise: this._promise,
    };
  }

  async injectMessage(_opts) { /* delegated to wrapper child bridge */ }
  async interrupt(_opts) { /* delegated to wrapper child bridge */ }
  async steer(_opts) { /* delegated to wrapper child bridge */ }
}

export class HermesController extends BaseController {
  constructor(opts) {
    super(opts);
    this._impl = this._pickImpl(opts);
  }

  _pickImpl(opts) {
    const { agentInfo, run, managedViaWrapper } = opts || {};
    const executionMode = String(
      opts?.executionMode || run?.executionMode || agentInfo?.sessionMode || "managed",
    ).trim().toLowerCase();

    if (executionMode === "managed" && managedViaWrapper) {
      return new DelegatedManagedController(opts);
    }

    // channel-mode dispatches (set by server-side _agent_execution_mode for
    // wrapper-backed managed runs) route to the resident-channel controller
    // when a gatewayUrl is available. The wrapper child bridge (running with
    // AIFY_MANAGED_VIA_WRAPPER=1) is the actor here — it knows the local
    // dashboard gateway URL via runtimeConfig.gatewayUrl.
    if (executionMode === "channel" || executionMode === "resident") {
      const cfg = getRuntimeConfig(agentInfo || {});
      const gatewayUrl = String(cfg.gatewayUrl || "").trim();
      if (/^wss?:\/\//i.test(gatewayUrl)) {
        return new HermesResidentController(opts);
      }
      return new HermesSingleShotController(opts);
    }

    return new HermesManagedController(opts);
  }

  start(ctx) { return this._impl.start(ctx); }
  async injectMessage(opts) { return this._impl.injectMessage(opts); }
  async interrupt(opts) { return this._impl.interrupt(opts); }
  async steer(opts) { return this._impl.steer(opts); }
  get terminalSink() { return this._impl.terminalSink; }

  // Plan 4 ready: forward the listener to the active sub-impl so markReady()
  // emitted from the per-mode controller reaches the bridge.
  setReadyListener(fn) {
    super.setReadyListener(fn);
    if (this._impl && typeof this._impl.setReadyListener === "function") {
      this._impl.setReadyListener(fn);
    }
  }
}
