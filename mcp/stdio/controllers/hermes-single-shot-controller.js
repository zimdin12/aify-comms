// HermesSingleShotController — extracted from createHermesControllerSingleShot
// in runtimes.js as part of Plan 3 Task 10.
//
// Legacy per-dispatch native controller — kept for resident-mode hermes
// (operator-typed agents whose dispatch goes through this path rather than
// the persistent ACP session). Conversation context is carried in the wire
// prompt because `hermes chat -q` is single-shot.
//
// File budget per 500-line rule: <=400 lines.

import { BaseController } from "./base-controller.js";
import {
  getRuntimeConfig,
  controlCapabilitiesForRuntime,
  buildSystemPrompt,
  buildUserPrompt,
  defaultHermesCommand,
  spawnProcess,
  terminateProcessTree,
  quoteForDisplay,
  diagnosticsFor,
} from "../runtimes-helpers.js";

export class HermesSingleShotController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._proc = null;
    this._interrupted = false;
    this._settled = false;
    this._promise = null;
    this._timeoutTimer = null;
    this._capabilities = controlCapabilitiesForRuntime("hermes");
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, callbacks } = this.opts;
    const config = getRuntimeConfig(agentInfo);
    const launcher = defaultHermesCommand();
    const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
    const hostCwd = agentInfo.cwd || process.cwd();
    const model = String(agentInfo.model || config.model || "").trim();
    const provider = String(config.provider || "").trim();
    const skipApprovals = config.yolo !== false; // default on for managed (no operator at the wheel)

    const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
    const userPrompt = buildUserPrompt(run);
    const fullPrompt = `${systemPrompt}\n\n${userPrompt}`;

    const args = [...launcher.args, "chat", "-Q", "-q", fullPrompt];
    if (model) args.push("-m", model);
    if (provider) args.push("--provider", provider);
    if (skipApprovals) args.push("--yolo");

    let stdoutBuf = "";
    let stderrBuf = "";
    let terminalSink = null;
    let sinkChain = Promise.resolve();

    const pushTerminalFrame = (text, status = "") => {
      // Defensive: parity with codex/opencode (b6d403c). Called from
      // child process stdout/exit callbacks; uncaught throws can crash
      // the bridge process. Guard everything.
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
        const header = subject ? `\r\n> [${from}] ${subject}\r\n` : `\r\n> [${from}]\r\n`;
        const prefixed = body
          .split(/\r?\n/)
          .map((line) => `> ${line}`)
          .join("\r\n");
        pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      } catch {
        // best-effort
      }
    };

    this._promise = (async () => {
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
          if (typeof sink === "function") terminalSink = sink;
        } catch (error) {
          try { callbacks.onEvent?.("hermes", `Hermes virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
        }
      }
      echoPromptToTerminal();
      pushTerminalFrame("[hermes] thinking...\r\n");

      return new Promise((resolve, reject) => {
        this._proc = spawnProcess(launcher.command, args, {
          cwd: hostCwd,
          env: { AIFY_BRIDGE_DISABLED: "1", AIFY_AGENT_ID: "" },
        });
        const proc = this._proc;
        proc.stdin?.on?.("error", () => {});
        try { proc.stdin?.end?.(); } catch {}

        proc.stdout?.on?.("data", (chunk) => {
          const text = chunk.toString();
          stdoutBuf += text;
          try { callbacks.onEvent?.("hermes", quoteForDisplay(text).slice(0, 200)); } catch {}
        });
        proc.stderr?.on?.("data", (chunk) => {
          const text = chunk.toString();
          stderrBuf += text;
          try { callbacks.onEvent?.("stderr", quoteForDisplay(text).slice(0, 200)); } catch {}
        });

        this._timeoutTimer = setTimeout(() => {
          if (this._settled) return;
          this._interrupted = true;
          try { terminateProcessTree(proc); } catch {}
        }, timeoutMs);
        if (typeof this._timeoutTimer.unref === "function") this._timeoutTimer.unref();

        proc.on("error", (error) => {
          if (this._settled) return;
          this._settled = true;
          clearTimeout(this._timeoutTimer);
          if (error?.code === "ENOENT") {
            const target = String(process.env.AIFY_HERMES_COMMAND || process.env.HERMES_COMMAND || "hermes").trim();
            const enriched = new Error(
              `spawn "${launcher.command}" ENOENT — the bridge resolved Hermes to "${launcher.command}" but Node could not execute it. ` +
                `Set AIFY_HERMES_COMMAND to an absolute path to a real "hermes" binary and restart aify-comms. ` +
                `Diagnostic: ${diagnosticsFor(target)}`,
            );
            pushTerminalFrame(`\r\n[error] ${enriched.message}\r\n`, "failed");
            reject(enriched);
            return;
          }
          const msg = error?.message || String(error || "Hermes spawn error");
          pushTerminalFrame(`\r\n[error] ${msg}\r\n`, "failed");
          reject(new Error(msg));
        });

        proc.on("close", (code) => {
          if (this._settled) return;
          this._settled = true;
          clearTimeout(this._timeoutTimer);

          if (this._interrupted) {
            pushTerminalFrame("\r\n[interrupted]\r\n", "running");
            resolve({
              status: "cancelled",
              summary: stdoutBuf.trim() || "Run interrupted",
              runtimeState: {},
              externalRefs: {},
            });
            return;
          }

          if (code !== 0) {
            const errMsg = stderrBuf.trim() || stdoutBuf.trim() || `Hermes exited with code ${code}`;
            pushTerminalFrame(`\r\n[error] ${errMsg}\r\n`, "failed");
            reject(new Error(errMsg));
            return;
          }

          const reply = stdoutBuf.trim() || "(no output)";
          pushTerminalFrame(`\r\n${reply}\r\n`, "running");
          resolve({
            status: "completed",
            summary: reply,
            runtimeState: {},
            externalRefs: {},
          });
        });
      });
    })();

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
    throw new Error("hermes single-shot does not support message injection (hermes chat -q is single-shot)");
  }

  async interrupt(_opts) {
    if (this._settled || !this._proc) return;
    this._interrupted = true;
    try { terminateProcessTree(this._proc); } catch {}
  }

  async steer(_opts) {
    // Hermes `chat -q` is single-shot — no mid-turn steering surface.
    // Use a follow-up dispatch instead.
    throw new Error("Hermes managed runs do not support mid-turn steer (hermes chat -q is single-shot). Send a follow-up dispatch instead.");
  }
}
