// CodexController — extracted from createCodexController in runtimes.js as
// part of Plan 3 Task 11. Top-level dispatcher: picks a per-mode controller
// at construction time and delegates start/injectMessage/interrupt/steer.
//
// Dispatch routing (preserves the legacy createCodexController logic 1:1):
//   - managed + managedViaWrapper → no-op delegated controller (wrapper's
//     child bridge owns the actual dispatch).
//   - executionMode "resident"/"channel" with a live app-server URL →
//     CodexLegacyController (WS app-server path).
//   - executionMode "managed" without a WS app-server → CodexManagedController
//     (persistent CodexSession pool, mirror of HermesSession/PiSession).
//   - else (default fallback) → CodexLegacyController.
//
// File budget per 500-line rule: ≤400 lines. Mode-specific implementations
// live in their own files (codex-managed-controller.js,
// codex-legacy-controller.js).

import { BaseController } from "./base-controller.js";
import { getRuntimeConfig, hasCodexLiveAppServer } from "../runtimes-helpers.js";
import { CodexManagedController } from "./codex-managed-controller.js";
import { CodexLegacyController } from "./codex-legacy-controller.js";

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

export class CodexController extends BaseController {
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

    // channel-mode (set by server-side _agent_execution_mode for wrapper-
    // backed managed runs) routes via the WS app-server when one is
    // available — same controller as resident. Wrapper child bridge owns
    // the codex app-server URL via runtimeConfig.appServerUrl.
    const cfg = getRuntimeConfig(agentInfo || {});
    const hasWsAppServer =
      (executionMode === "resident" || executionMode === "channel") &&
      hasCodexLiveAppServer(cfg);

    if (executionMode === "managed" && !hasWsAppServer) {
      return new CodexManagedController(opts);
    }

    return new CodexLegacyController(opts);
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
