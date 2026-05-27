// HermesManagedController — extracted from createHermesControllerManaged and
// createHermesControllerManagedGateway in runtimes.js as part of Plan 3 Task 10.
//
// Two sub-modes share this controller:
//   - ACP-backed (default): persistent `hermes acp` JSON-RPC session,
//     single-client. interrupt supported, steer rejected.
//   - Gateway-backed (AIFY_HERMES_MANAGED_USE_GATEWAY=1): multi-client
//     tui_gateway path; Dashboard Console can attach as a second WS client
//     because TeeTransport fans events. Supports interrupt + steer-via-resend.
//
// File budget per 500-line rule: <=400 lines.

import { BaseController } from "./base-controller.js";
import {
  controlCapabilitiesForRuntime,
  buildSystemPrompt,
  buildUserPrompt,
} from "../runtimes-helpers.js";

// Hermes session modules are not in the runtimes.js <-> adapters/ cycle,
// so direct imports are safe.
import { getOrCreateHermesSession } from "../hermes-session.js";
import {
  getOrCreateHermesGatewaySession,
  managedHermesUsesGateway,
} from "../hermes-managed-gateway-session.js";

export class HermesManagedController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._useGateway = managedHermesUsesGateway();
    this._session = null;
    this._promise = null;
    this._capabilities = this._useGateway
      ? { interrupt: true, steer: true }
      : controlCapabilitiesForRuntime("hermes");
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, runtimeState, callbacks } = this.opts;

    const onPoolEvent = (kind, payload) => {
      try {
        callbacks?.onEvent?.(
          "hermes",
          `${kind}: ${typeof payload === "string" ? payload : JSON.stringify(payload).slice(0, 200)}`,
        );
      } catch {}
    };

    if (this._useGateway) {
      // AIFY_HERMES_MANAGED_USE_GATEWAY=1 routes managed hermes through the
      // multi-client tui_gateway path instead of the single-client ACP path.
      // Bridge spawns `hermes dashboard --tui` per agent (mirror of how
      // codex-aify spawns app-server), then attaches via WS for prompt.submit
      // injection. Dashboard Console can also attach as a second WS client
      // because TeeTransport fans events to all attached clients. Off by
      // default until operator-validated.
      this._session = getOrCreateHermesGatewaySession({
        agentId,
        agentInfo,
        onPoolEvent,
      });
      // Plan 4 ready: gateway session pool acquired (or reused). The pool
      // will lazily spawn `hermes dashboard --tui` and connect WS on first
      // runTurn() if not already up.
      this.markReady();

      this._promise = (async () => {
        if (typeof callbacks?.terminalSinkProvider === "function") {
          try {
            const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo, session: this._session });
            if (typeof sink === "function") this._session.attachTerminalSink(sink);
          } catch (error) {
            try { callbacks.onEvent?.("hermes", `Hermes gateway virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
          }
        }
        const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
        const userPrompt = buildUserPrompt(run);
        return this._session.runTurn({
          promptText: `${systemPrompt}\n\n${userPrompt}`,
          run,
          callbacks,
          runtimeState,
        });
      })();
    } else {
      // Default: persistent `hermes acp` JSON-RPC session.
      this._session = getOrCreateHermesSession({
        agentId,
        agentInfo,
        onPoolEvent,
      });
      // Plan 4 ready: persistent ACP session pool acquired (or reused).
      // The pool lazily spawns `hermes acp` and initializes on first
      // runTurn() if not already up.
      this.markReady();

      this._promise = (async () => {
        if (typeof callbacks?.terminalSinkProvider === "function") {
          try {
            const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo, session: this._session });
            if (typeof sink === "function") this._session.attachTerminalSink(sink);
          } catch (error) {
            try { callbacks.onEvent?.("hermes", `Hermes virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
          }
        }
        const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
        const userPrompt = buildUserPrompt(run);
        return this._session.runTurn({ promptText: `${systemPrompt}\n\n${userPrompt}`, run });
      })();
    }

    return this._legacyShape();
  }

  _legacyShape() {
    return {
      capabilities: this._capabilities,
      interrupt: async () => this.interrupt(),
      steer: async () => this.steer(),
      promise: this._promise,
    };
  }

  async injectMessage(_opts) {
    throw new Error("hermes managed does not support direct message injection; send a follow-up dispatch");
  }

  async interrupt(_opts) {
    try { if (this._session) await this._session.cancelActiveTurn(); } catch {}
  }

  async steer(_opts) {
    if (this._useGateway) {
      throw new Error("Direct steer not implemented; send another comms_send and the controller will route via session.steer if the turn is still running.");
    }
    throw new Error("Hermes managed runs do not support mid-turn steer; send a follow-up dispatch instead.");
  }
}
