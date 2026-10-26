// HermesManagedController — extracted from createHermesControllerManaged and
// createHermesControllerManagedGateway in runtimes.js as part of Plan 3 Task 10.
//
// ACP-backed: a persistent `hermes acp` JSON-RPC session, single-client. interrupt supported, steer rejected.
// (The opt-in gateway-backed sub-mode, AIFY_HERMES_MANAGED_USE_GATEWAY=1, went in 0.9.10: it started a hermes
// gateway from the comms bridge, which the launcher and aify-env own now.)
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

export class HermesManagedController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._session = null;
    this._promise = null;
    this._capabilities = controlCapabilitiesForRuntime("hermes");
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, callbacks } = this.opts;

    const onPoolEvent = (kind, payload) => {
      try {
        callbacks?.onEvent?.(
          "hermes",
          `${kind}: ${typeof payload === "string" ? payload : JSON.stringify(payload).slice(0, 200)}`,
        );
      } catch {}
    };

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

    return this._legacyShape();
  }

  _legacyShape() {
    return {
      capabilities: this._capabilities,
      interrupt: async () => this.interrupt(),
      steer: async (text) => this.steer(text),
      promise: this._promise,
    };
  }

  async injectMessage(_opts) {
    throw new Error("hermes managed does not support direct message injection; send a follow-up dispatch");
  }

  async interrupt(_opts) {
    try { if (this._session) await this._session.cancelActiveTurn(); } catch {}
  }

  async steer(_text) {
    throw new Error("Hermes ACP fallback does not support mid-turn steer; use wrapper/gateway delivery.");
  }
}
