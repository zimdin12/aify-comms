// OpencodeController — extracted from createOpenCodeController in runtimes.js
// as part of Plan 3 Task 7. Owns the opencode runtime's spawn/lifecycle/
// delivery. Behavior is a 1:1 translation of the factory function; only the
// form (closure -> instance properties) changed.
//
// File budget per 500-line rule: <=400 lines.

import { createOpencode } from "@opencode-ai/sdk";
import { BaseController } from "./base-controller.js";
import {
  getRuntimeConfig,
  controlCapabilitiesForRuntime,
  buildSystemPrompt,
  buildUserPrompt,
  opencodePermissionConfig,
  splitProviderModel,
  summarizeOpenCodeParts,
  requireOpenCodeData,
} from "../runtimes-helpers.js";

export class OpencodeController extends BaseController {
  constructor(opts) {
    super(opts);
    // Cheap constructor — only store opts. All session/config wiring happens
    // in start() so smoke tests can instantiate without triggering opencode
    // SDK setup or resident-session validation.
    this._started = false;
    this._interrupted = false;
    this._open = null;
    this._sessionId = "";
    this._cwd = "";
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
    const config = getRuntimeConfig(agentInfo);
    const executionMode = String(run?.executionMode || agentInfo?.sessionMode || "managed")
      .trim()
      .toLowerCase();
    const residentSessionId = String(agentInfo?.sessionHandle || "").trim();
    const cwd = agentInfo?.cwd || process.cwd();
    const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
    const model = splitProviderModel(agentInfo?.model || config.model || "");
    const permission = opencodePermissionConfig(config, executionMode);
    const selectedAgent = String(config.agent || "").trim() || undefined;
    let sessionId =
      executionMode === "resident"
        ? residentSessionId
        : String(runtimeState?.sessionId || residentSessionId || "").trim();

    if (executionMode === "resident" && !sessionId) {
      throw new Error(
        `Resident OpenCode session "${agentId}" has no bound session ID. ` +
          "Re-register with sessionHandle explicitly or create a persistent environment-managed agent with comms_spawn.",
      );
    }

    this._cwd = cwd;
    this._sessionId = sessionId;

    // Synthesized-terminal feed for opencode (Phase 6 intermediate).
    // Per-dispatch like hermes; full persistent worker is deferred.
    let terminalSink = null;
    let sinkChain = Promise.resolve();
    const pushTerminalFrame = (text, status = "") => {
      // Defensive: parity with codex (b6d403c). Called from SDK delta
      // callbacks; an uncaught throw in this synchronous path can crash
      // the bridge process. Belt-and-suspenders: guard everything.
      try {
        if (!terminalSink || (!text && !status)) return;
        const body = String(text || "");
        const stat = String(status || "");
        sinkChain = sinkChain.then(async () => {
          try { await terminalSink(body, stat); } catch {}
        });
      } catch {
        // best-effort: don't propagate frame-push failures
      }
    };
    const echoPromptToTerminal = () => {
      try {
        const body = String(run?.body || "").trim();
        if (!body) return;
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((line) => `\x1b[92m>\x1b[0m ${line}`).join("\r\n");
        pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      } catch {
        // best-effort
      }
    };

    this._promise = new Promise(async (resolve, reject) => {
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
          if (typeof sink === "function") terminalSink = sink;
        } catch (error) {
          try { callbacks.onEvent?.("opencode", `OpenCode virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
        }
      }
      echoPromptToTerminal();
      pushTerminalFrame("\x1b[2m[opencode] connecting...\x1b[0m\r\n");

      const timer = setTimeout(async () => {
        this._interrupted = true;
        try {
          if (this._open?.client && this._sessionId) {
            await this._open.client.session.abort({
              path: { id: this._sessionId },
              query: { directory: this._cwd },
            });
          }
        } catch {
          // best effort
        }
        reject(new Error(`OpenCode run timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      try {
        this._open = await createOpencode({
          port: 0,
          config: permission ? { permission } : undefined,
        });
        const client = this._open.client;

        if (!this._sessionId) {
          const created = await client.session.create({
            query: { directory: cwd },
            body: { title: run?.subject || `aify:${agentId}` },
          });
          this._sessionId = requireOpenCodeData(created, "Failed to create OpenCode session").id;
        } else {
          requireOpenCodeData(await client.session.get({
            path: { id: this._sessionId },
            query: { directory: cwd },
          }), `OpenCode session "${this._sessionId}" was not found`);
        }

        callbacks?.onRuntimeState?.({ sessionId: this._sessionId });
        callbacks?.onRefs?.({ threadId: this._sessionId });
        callbacks?.onEvent?.("thread", `Using ${executionMode} OpenCode session ${this._sessionId}`);

        const response = await client.session.prompt({
          path: { id: this._sessionId },
          query: { directory: cwd },
          body: {
            ...(model ? { model } : {}),
            ...(selectedAgent ? { agent: selectedAgent } : {}),
            system: buildSystemPrompt(agentId, agentInfo, run),
            parts: [{ type: "text", text: buildUserPrompt(run) }],
          },
        });

        clearTimeout(timer);
        const data = requireOpenCodeData(response, "OpenCode prompt failed");
        const info = data.info || {};
        const parts = data.parts || [];
        const summary = summarizeOpenCodeParts(parts);
        const errorMessage =
          info?.error?.data?.message ||
          info?.error?.message ||
          info?.error?.name ||
          "";

        if (this._interrupted || /aborted/i.test(errorMessage || "")) {
          pushTerminalFrame(`\r\n\x1b[93m\x1b[1m⏸ interrupted\x1b[0m\r\n`);
          resolve({
            status: "cancelled",
            summary: summary || errorMessage || "Run interrupted",
            runtimeState: { sessionId: this._sessionId },
            externalRefs: { threadId: this._sessionId, turnId: info.id || "" },
          });
          return;
        }

        if (errorMessage) {
          pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m \x1b[31m${errorMessage}\x1b[0m\r\n`, "failed");
          reject(new Error(errorMessage));
          return;
        }

        const reply = summary || "(no output)";
        pushTerminalFrame(`\r\n${reply}\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m\r\n`, "running");
        resolve({
          status: "completed",
          summary: reply,
          runtimeState: { sessionId: this._sessionId },
          externalRefs: { threadId: this._sessionId, turnId: info.id || "" },
        });
      } catch (error) {
        pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m \x1b[31m${error?.message || error}\x1b[0m\r\n`, "failed");
        clearTimeout(timer);
        reject(error);
      } finally {
        try {
          this._open?.server?.close?.();
        } catch {
          // ignore close errors
        }
      }
    });

    this._capabilities = controlCapabilitiesForRuntime("opencode");
    return this._legacyShape();
  }

  _legacyShape() {
    return {
      capabilities: this._capabilities,
      interrupt: () => this.interrupt(),
      steer: () => this.steer(),
      promise: this._promise,
    };
  }

  async injectMessage(_opts) {
    // Opencode is single-shot per dispatch — no live message injection.
    throw new Error("opencode does not support mid-session message injection");
  }

  async interrupt(_opts) {
    this._interrupted = true;
    if (!this._open?.client || !this._sessionId) return;
    await this._open.client.session.abort({
      path: { id: this._sessionId },
      query: { directory: this._cwd },
    });
  }

  async steer(_opts) {
    throw new Error('Runtime "opencode" does not support steer');
  }
}
