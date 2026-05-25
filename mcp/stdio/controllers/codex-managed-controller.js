// CodexManagedController — extracted from createCodexControllerPooled in
// runtimes.js as part of Plan 3 Task 11. Persistent CodexSession pool keyed
// by agentId: spawn `codex app-server` once, initialize + thread/start, reuse
// across turns. Mirror of HermesSession/PiSession persistent pool pattern.
//
// See DECISIONS.md 2026-05-23 and the codex follow-up in
// docs/plans/2026-05-23-hermes-acp-persistent-session.md.
//
// File budget per 500-line rule: ≤400 lines.

import { BaseController } from "./base-controller.js";
import {
  controlCapabilitiesForRuntime,
  buildSystemPrompt,
  buildUserPrompt,
} from "../runtimes-helpers.js";
import { getOrCreateCodexSession } from "../codex-session.js";

export class CodexManagedController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._session = null;
    this._promise = null;
    this._capabilities = controlCapabilitiesForRuntime("codex");
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, runtimeState, callbacks } = this.opts;

    const onPoolEvent = (kind, payload) => {
      try {
        callbacks?.onEvent?.(
          "codex",
          `${kind}: ${typeof payload === "string" ? payload : JSON.stringify(payload).slice(0, 200)}`,
        );
      } catch {}
    };

    this._session = getOrCreateCodexSession({
      agentId,
      agentInfo,
      onPoolEvent,
    });
    // Plan 4 ready: persistent CodexSession is acquired; the pool will
    // lazily initialize the app-server before runTurn() if not already up.
    // From the bridge's perspective the controller is ready to accept
    // dispatch. See DECISIONS.md.
    this.markReady();

    this._promise = (async () => {
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo, session: this._session });
          if (typeof sink === "function") this._session.attachTerminalSink(sink);
        } catch (error) {
          try { callbacks.onEvent?.("codex", `Codex virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
        }
      }
      const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
      const userPrompt = buildUserPrompt(run);
      const result = await this._session.runTurn({
        promptText: `${systemPrompt}\n\n${userPrompt}`,
        run,
        callbacks,
        runtimeState,
      });
      // Propagate thread id back to caller so server can persist it.
      if (result?.runtimeState?.threadId) {
        try { callbacks?.onRuntimeState?.({ threadId: result.runtimeState.threadId }); } catch {}
      }
      return result;
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
    throw new Error("codex managed does not support direct message injection; send a follow-up dispatch");
  }

  async interrupt(_opts) {
    try { if (this._session) await this._session.cancelActiveTurn(); } catch {}
  }

  async steer(text) {
    if (!this._session) {
      throw new Error("No active Codex session to steer");
    }
    await this._session.steer(text);
  }
}
