// PiController — extracted from createPiControllerManaged in runtimes.js
// as part of Plan 3 Task 8. Owns the pi runtime's persistent-session
// dispatch surface (acquirePiSession + runTurn).
//
// Per Plan 2 pi flip (2026-05-25, commit ec2134e+), only managed mode is
// supported here. Resident pi was removed in favor of managed-via-wrapper
// because `omp --mode rpc` is single-client stdio. PiAdapter.controllerFor
// returns null for resident; launchRuntimeRun rejects with a clear error.
//
// File budget per 500-line rule: <=400 lines. This file is a 1:1 translation
// of the factory function; only the form (closure -> instance properties)
// changed.
//
// The pi healing dance (attempt resume -> detect failure -> wipe handle ->
// fresh-start retry) is preserved verbatim, as is the wiring of the synth
// terminal sink via callbacks.terminalSinkProvider + session.attachTerminalSink.

import { BaseController } from "./base-controller.js";
import {
  controlCapabilitiesForRuntime,
  detectPiRuntimeFailure,
} from "../runtimes-helpers.js";

// PiSession + acquirePiSession live in pi-session.js — direct import is safe
// (not part of the runtimes.js <-> adapters/ cycle).
import { acquirePiSession } from "../pi-session.js";

export class PiController extends BaseController {
  constructor(opts) {
    super(opts);
    // Cheap constructor — store opts only; all session acquisition happens
    // in start() so smoke tests can instantiate without touching the pi pool.
    this._started = false;
    this._turnHandle = null;
    this._acquireError = null;
    this._promise = null;
    this._capabilities = null;
  }

  // Kick off the dispatch. Returns the legacy controller shape
  // ({ capabilities, interrupt, steer, promise }) that launchRuntimeRun
  // hands back to the runtime dispatcher.
  start() {
    if (this._started) {
      return this._legacyShape();
    }
    this._started = true;

    const { agentId, agentInfo, run, runtimeState, callbacks } = this.opts;
    const hintSession = String(runtimeState?.sessionId || runtimeState?.sessionFile || "").trim();
    const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();

    this._promise = (async () => {
      let session;
      let attemptSessionId = hintSession;
      let lastError;
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          session = await acquirePiSession({
            agentId,
            agentInfo,
            sessionId: attemptSessionId,
            cwd: agentInfo.cwd || process.cwd(),
            onPoolEvent: (level, message) => {
              try { callbacks.onEvent?.(level, message); } catch {}
            },
          });
          break;
        } catch (error) {
          lastError = error;
          const detected = error?.detected || detectPiRuntimeFailure(error?.message || "");
          if (
            attempt === 0 &&
            detected.shouldHeal &&
            attemptSessionId &&
            executionMode !== "resident"
          ) {
            try { callbacks.onEvent?.("thread", `Pi session "${attemptSessionId}" is not resumable (${detected.message}); starting fresh.`); } catch {}
            try { callbacks.onRuntimeState?.({}); } catch {}
            try { callbacks.onSessionHandleChange?.("", { reason: detected.healReason, previous: attemptSessionId }); } catch {}
            attemptSessionId = "";
            continue;
          }
          if (detected.missingSession && executionMode === "resident") {
            this._acquireError = new Error(
              `Resident Pi session "${attemptSessionId}" is not resumable: ${detected.message}. Clear the saved session handle or start a fresh managed Pi session.`,
            );
            throw this._acquireError;
          }
          this._acquireError = error;
          throw error;
        }
      }
      if (!session) throw lastError || new Error("Pi session not acquired");
      // Phase 2: wire up the synthesized terminal sink once per session lifetime.
      // The bridge resolves the virtual terminal id (creating it on first use)
      // and returns a POST-to-/terminals/{id}/output sink. Subsequent dispatches
      // re-attach idempotently — attachTerminalSink replaces the previous sink.
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo, session });
          if (typeof sink === "function") session.attachTerminalSink(sink);
        } catch (error) {
          try { callbacks.onEvent?.("pi", `Pi virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
        }
      }
      this._turnHandle = session.runTurn(run, callbacks);
      return this._turnHandle.promise;
    })();

    this._capabilities = controlCapabilitiesForRuntime("pi");
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
    // pi delivers via session.runTurn — there's no separate inject path.
    // Mid-session steering uses the steer() method instead.
    throw new Error("pi does not support mid-session message injection; use steer()");
  }

  async interrupt(_opts) {
    if (this._turnHandle) await this._turnHandle.interrupt();
  }

  async steer(text) {
    if (this._acquireError) throw this._acquireError;
    if (!this._turnHandle) throw new Error("No active Pi turn to steer");
    await this._turnHandle.steer(text);
  }
}
