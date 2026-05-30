// HermesController — extracted from createHermesController in runtimes.js as
// part of Plan 3 Task 10. Top-level dispatcher: picks a per-mode controller
// at construction time and delegates start/injectMessage/interrupt/steer.
//
// Dispatch routing (preserves the legacy createHermesController logic 1:1):
//   - managed + managedViaWrapper → no-op delegated controller (wrapper's
//     child bridge claims and delivers via the wrapper's local gateway).
//   - executionMode === "channel" or "resident" → delivery is owned by the
//     per-agent `hermes-managed-host.js run <agent>` loop (visible-TUI model,
//     2026-05-31; replaced the hermes-channel.js api_server sidecar). That loop
//     claims channel/resident runs over HTTP by agentId as a standalone
//     channel-sidecar and submits into the visible TUI's gateway session — it
//     does NOT go through this controller. server.js excludes hermes from the
//     wrapper-child channel/resident claim (wrapperChildExecutionModes) so this
//     controller never races the loop. The retired tui_gateway WS-bind path
//     (HermesResidentController / aify.session.bind_transport) no longer exists.
//   - else (managed / default) → HermesManagedController
//     (ACP-backed persistent session, or gateway-backed if
//     AIFY_HERMES_MANAGED_USE_GATEWAY=1).
//
// File budget per 500-line rule: <=400 lines. Mode-specific implementations
// live in their own files (hermes-managed-controller.js,
// hermes-single-shot-controller.js).

import { BaseController } from "./base-controller.js";
import { HermesManagedController } from "./hermes-managed-controller.js";

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

// channel/resident hermes delivery is owned by the per-agent
// `hermes-managed-host.js run <agent>` delivery loop (visible-TUI model,
// 2026-05-31; it replaced the retired hermes-channel.js api_server sidecar). It
// claims those runs over HTTP by agentId out-of-band as a standalone
// bridgeKind="channel-sidecar" — never through launchRuntimeRun/controllerFor.
//
// CLAIM-RACE NOTE (2026-05-31): this controller must NEVER win the claim for a
// managed hermes channel run. server.js no longer lets the hermes wrapper child
// advertise channel/resident (wrapperChildExecutionModes excludes hermes), so a
// channel/resident hermes run should not reach this controller at all. The
// "delegated" resolution below is a defensive no-op kept only so a stray run
// doesn't crash the dispatcher; it does NOT deliver. (The earlier behavior —
// the wrapper child claiming the channel run, this controller resolving
// "delegated", and server.js then auto-mirroring a summary — is the bug that
// produced fabricated replies instead of real agent replies. Fixed at the claim
// layer; this comment documents why the path must stay dead.)
class ChannelDelegatedController extends BaseController {
  constructor(opts) {
    super(opts);
    this._capabilities = { interrupt: false, steer: false };
    this._promise = Promise.resolve({
      status: "delegated",
      summary: "channel/resident dispatch delegated to hermes-managed-host.js delivery loop",
      runtimeState: {},
      externalRefs: {},
    });
  }

  start() {
    this.markReady();
    return {
      capabilities: this._capabilities,
      interrupt: async () => {},
      steer: async () => {},
      promise: this._promise,
    };
  }
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

    // channel/resident dispatches are delivered by the hermes-channel.js
    // sidecar (api_server model), not by this controller. Return a delegated
    // no-op so nothing forks a hidden session here.
    if (executionMode === "channel" || executionMode === "resident") {
      return new ChannelDelegatedController(opts);
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
