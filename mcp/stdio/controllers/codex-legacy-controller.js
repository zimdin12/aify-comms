// CodexLegacyController — extracted from createCodexControllerLegacy in
// runtimes.js as part of Plan 3 Task 11. Per-dispatch codex turn via the
// JSON-RPC app-server protocol (either an existing WS app-server URL or a
// freshly-spawned `codex app-server` process).
//
// Used when:
//   - executionMode is "resident"/"channel" and a live app-server is reachable
//     (resident-via-WS path), OR
//   - the default fallback for legacy managed dispatches (kept for safety;
//     CodexManagedController is the preferred persistent-session path).
//
// File budget per 500-line rule: ≤400 lines. Larger blocks (timers, thread
// resume + heal) live in codex-legacy-helpers.js.

import { BaseController } from "./base-controller.js";
import {
  getRuntimeConfig,
  controlCapabilitiesForRuntime,
  buildSystemPrompt,
  buildUserPrompt,
  defaultCodexCommand,
  managedCodexEffort,
  managedCodexSandboxMode,
  hasCodexLiveAppServer,
  resolveCodexRequestCwd,
  codexSpawnCwd,
  prepareManagedCodexHome,
  codexTurnSandboxPolicy,
  createRpcClient,
  createWebSocketRpcClient,
  isAifyCommsMcpToolItem,
  isFatalCodexRuntimeLog,
  spawnProcess,
  terminateProcessTree,
  quoteForDisplay,
} from "../runtimes-helpers.js";
import {
  createCodexLegacyTimers,
  resolveActiveCodexThread,
  buildCodexNotificationHandler,
} from "./codex-legacy-helpers.js";
import { codexAifyReceiptFrame } from "../aify-console-markers.js";
import { managedCodexServerRequest } from "../runtimes-rpc.js";
import { AIFY_VERSION } from "../version.js";

export class CodexLegacyController extends BaseController {
  constructor(opts) {
    super(opts);
    this._started = false;
    this._capabilities = controlCapabilitiesForRuntime("codex");
    this._activeThreadId = null;
    this._rpc = null;
    this._proc = null;
    this._interrupted = false;
    this._rejectPromise = null;
    this._promise = null;
  }

  start() {
    if (this._started) return this._legacyShape();
    this._started = true;

    const { agentId, agentInfo, run, runtimeState, callbacks } = this.opts;
    const config = getRuntimeConfig(agentInfo);
    const launcher = defaultCodexCommand();
    const resumePolicy = String(runtimeState?.resumePolicy || agentInfo?.runtimeState?.resumePolicy || "native_first").trim().toLowerCase();
    const allowFreshContext = resumePolicy === "fresh_context";
    const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
    const configuredQuietTimeout = Number(config.quietTimeoutMs ?? config.silenceTimeoutMs ?? 30 * 60 * 1000);
    const quietTimeoutMs = configuredQuietTimeout <= 0 ? 0 : Math.max(10 * 60 * 1000, configuredQuietTimeout);
    const configuredAifyMcpToolTimeout = Number(config.mcpToolTimeoutMs ?? config.commsToolTimeoutMs ?? 300 * 1000);
    const aifyMcpToolTimeoutMs = configuredAifyMcpToolTimeout <= 0 ? 0 : Math.max(10 * 1000, configuredAifyMcpToolTimeout);
    const hostCwd = agentInfo.cwd || process.cwd();
    const model = String(agentInfo.model || config.model || "").trim();
    const effort = managedCodexEffort(config);
    const summaryMode = config.summary || "concise";
    const approvalPolicy = config.approvalPolicy || "never";
    const networkAccess = config.networkAccess !== false;
    const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
    const sandboxMode = managedCodexSandboxMode(config, executionMode);
    const residentThreadId = String(agentInfo.sessionHandle || "").trim();
    // Plan 5 (2026-05-25): channel-mode is the new server-side route for
    // wrapper-backed managed dispatches (then api_v2.py; the route domains have since moved
    // out of it). When the in-
    // process bridge inside codex-aify delivers a channel-mode run for its
    // own agent, runtimeConfig.appServerUrl is set by server.js
    // (read from AIFY_CODEX_APP_SERVER_URL env). Without 'channel' here,
    // appServerUrl is dropped and CodexLegacyController falls back to
    // spawning a fresh codex app-server — defeating the wrapper-backed
    // delivery shape.
    const appServerUrl =
      (executionMode === "resident" || executionMode === "channel") && hasCodexLiveAppServer(config)
        ? String(config.appServerUrl || "").trim()
        : "";
    const cwd = resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl });
    const spawnCwd = codexSpawnCwd(launcher, hostCwd);
    const managedCodexHome =
      executionMode === "managed"
        ? prepareManagedCodexHome({ workspace: cwd, model, effort })
        : "";
    const remoteAuthTokenEnv = String(config.remoteAuthTokenEnv || "").trim();
    const remoteAuthToken = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";

    // Plan 5 (2026-05-25): channel-mode (wrapper-backed managed) carries the
    // same session-handle semantic as resident-mode — the wrapper child's
    // agent registration sets sessionHandle from CODEX_THREAD_ID. Use it as
    // the active thread so resume-by-handle works on the first turn.
    this._activeThreadId =
      (executionMode === "resident" || executionMode === "channel")
        ? (residentThreadId || runtimeState?.threadId || null)
        : (runtimeState?.threadId || null);

    // eslint-disable-next-line consistent-this
    const self = this;

    // Mutable shared context for timers + notification handler. All
    // turn-scoped state lives here so helpers in codex-legacy-helpers.js
    // can read/write without long parameter lists.
    // Saved on the instance so interrupt()/steer() can read activeTurnId.
    const ctx = this._ctx = {
      finalText: "",
      finalStatus: "failed",
      finalError: "",
      activeTurnId: null,
      settled: false,
      lastActivityAt: Date.now(),
      activityLabel: "runtime launch",
      activeItems: new Map(),
      callbacks,
      isAifyCommsMcpToolItem,
      get rpc() { return self._rpc; },
      get proc() { return self._proc; },
    };

    // Synthesized-terminal feed (Phase 5 intermediate — codex stays per-
    // dispatch but operators see Console activity for each turn). Mirror of
    // the hermes/pi pattern: terminalSink resolves async at start;
    // pushTerminalFrame serializes via sinkChain.
    let terminalSink = null;
    let sinkChain = Promise.resolve();
    const pushTerminalFrame = (text, status = "") => {
      // Defensive: called from RPC notification handlers at high frequency.
      // Any throw here would propagate up through RPC dispatch.
      try {
        if (!terminalSink || (!text && !status)) return;
        const frame = { text: String(text || ""), status: String(status || "") };
        sinkChain = sinkChain.then(async () => {
          try { await terminalSink(frame.text, frame.status); } catch {}
        });
      } catch {}
    };
    const echoPromptToTerminal = () => {
      try {
        const body = String(run?.body || "").trim();
        if (!body) return;
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        pushTerminalFrame(codexAifyReceiptFrame(), "running");
        const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((line) => `\x1b[92m>\x1b[0m ${line}`).join("\r\n");
        pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      } catch {}
    };

    const markActivity = (label = "runtime event") => {
      ctx.lastActivityAt = Date.now();
      ctx.activityLabel = label;
    };

    const handleNotification = buildCodexNotificationHandler({ ctx, pushTerminalFrame, markActivity });

    const handleRuntimeLog = (line) => {
      const text = quoteForDisplay(line);
      if (text) {
        markActivity("stderr");
        callbacks.onEvent?.("stderr", text);
      }
      if (text && isFatalCodexRuntimeLog(text) && !ctx.settled) {
        ctx.finalStatus = "failed";
        ctx.finalError = `Codex runtime fatal error: ${text}`;
        ctx.settled = true;
        try { terminateProcessTree(self._proc); } catch {}
        try { self._rpc?.close?.(); } catch {}
        if (self._rejectPromise) self._rejectPromise(new Error(ctx.finalError));
      }
    };

    this._promise = new Promise(async (resolve, reject) => {
      self._rejectPromise = reject;
      // Resolve the synthesized-terminal sink once for this dispatch.
      // Wrap broadly so a synchronous helper error (provider lookup, etc.)
      // can't crash the controller — operator-reported 2026-05-22
      // "running codex crashes aify-comms" possibly traces here.
      if (typeof callbacks?.terminalSinkProvider === "function") {
        try {
          const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
          if (typeof sink === "function") terminalSink = sink;
        } catch (error) {
          try { callbacks.onEvent?.("codex", `Codex virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
        }
      }
      try { echoPromptToTerminal(); } catch {}
      try { pushTerminalFrame("\x1b[2m[codex] connecting...\x1b[0m\r\n"); } catch {}

      const timers = createCodexLegacyTimers({
        ctx,
        timeoutMs,
        quietTimeoutMs,
        aifyMcpToolTimeoutMs,
        fail: reject,
      });

      try {
        if (appServerUrl) {
          callbacks.onEvent?.("runtime", `Connecting to shared Codex app-server ${appServerUrl}`);
          self._rpc = await createWebSocketRpcClient(appServerUrl, {
            token: remoteAuthToken || undefined,
            onNotification: handleNotification,
            onStderr: handleRuntimeLog,
            onRequest: managedCodexServerRequest,
          });
        } else {
          self._proc = spawnProcess(launcher.command, launcher.args, {
            cwd: spawnCwd,
            env: managedCodexHome ? { CODEX_HOME: managedCodexHome } : {},
          });
          self._rpc = createRpcClient(self._proc, {
            onNotification: handleNotification,
            onStderr: handleRuntimeLog,
            onRequest: managedCodexServerRequest,
          });
        }

        await self._rpc.request("initialize", {
          clientInfo: {
            name: "aify-comms",
            title: "aify-comms dispatch bridge",
            version: AIFY_VERSION,
          },
        });
        markActivity("initialize");
        self._rpc.notify("initialized", {});
        // Plan 4 ready: app-server handshake (initialize + initialized) is
        // complete — controller can accept work. thread/start + turn/start
        // are dispatch-specific and follow.
        self.markReady();

        const startThread = async () => {
          const threadStartParams = {
            cwd,
            approvalPolicy,
            personality: "friendly",
            serviceName: "aify-comms",
          };
          if (model) threadStartParams.model = model;
          let started;
          try {
            started = await self._rpc.request("thread/start", {
              ...threadStartParams,
              sandbox: sandboxMode,
            }, 60000);
          } catch (error) {
            const message = error?.message || "";
            if (sandboxMode !== "workspace-write" || !message.includes("unknown variant `workspace-write`")) {
              throw error;
            }
            started = await self._rpc.request("thread/start", {
              ...threadStartParams,
              sandbox: "workspaceWrite",
            }, 60000);
          }
          return started.thread?.id;
        };

        self._activeThreadId = await resolveActiveCodexThread({
          rpc: self._rpc,
          startThread,
          initialThreadId: self._activeThreadId,
          executionMode,
          agentId,
          allowFreshContext,
          managedCodexHome,
          callbacks,
          markActivity,
        });

        callbacks.onRuntimeState?.({ threadId: self._activeThreadId });
        callbacks.onRefs?.({ threadId: self._activeThreadId });
        callbacks.onEvent?.("thread", `Using ${executionMode} thread ${self._activeThreadId}`);
        markActivity("thread ready");

        callbacks.onEvent?.("turn", `Calling turn/start on thread ${self._activeThreadId} with cwd="${cwd}", writableRoots=["${cwd}"]`);
        let turn;
        try {
          const turnStartParams = {
            threadId: self._activeThreadId,
            input: [{ type: "text", text: `${buildSystemPrompt(agentId, agentInfo, run)}\n\n${buildUserPrompt(run)}` }],
            cwd,
            approvalPolicy,
            sandboxPolicy: codexTurnSandboxPolicy(sandboxMode, cwd, networkAccess),
            effort,
            summary: summaryMode,
            personality: "friendly",
          };
          if (model) turnStartParams.model = model;
          turn = await self._rpc.request("turn/start", turnStartParams, 60000);
        } catch (error) {
          // turn/start sends cwd + writableRoots — if AbsolutePathBuf fires
          // here, it's one of those two fields. Label the error so the run
          // log shows us unambiguously which RPC tripped.
          throw new Error(
            `Codex turn/start failed for thread ${self._activeThreadId} (cwd="${cwd}"): ${error?.message || error}`,
            { cause: error },
          );
        }

        ctx.activeTurnId = turn.turn?.id || ctx.activeTurnId;
        callbacks.onRefs?.({ threadId: self._activeThreadId, turnId: ctx.activeTurnId });
        markActivity("turn/start");

        const poll = setInterval(() => {
          if (!ctx.settled) return;
          clearInterval(poll);
          timers.clearAll();
          const cleanup = () => {
            try { terminateProcessTree(self._proc); } catch {}
            try { self._rpc?.close?.(); } catch {}
          };
          if (ctx.finalStatus === "completed") {
            resolve({
              status: "completed",
              summary: ctx.finalText.trim() || "(no output)",
              runtimeState: { threadId: self._activeThreadId },
              externalRefs: { threadId: self._activeThreadId, turnId: ctx.activeTurnId },
            });
            cleanup();
            return;
          }
          if (ctx.finalStatus === "interrupted" || self._interrupted) {
            resolve({
              status: "cancelled",
              summary: ctx.finalText.trim() || ctx.finalError || "Run interrupted",
              runtimeState: { threadId: self._activeThreadId },
              externalRefs: { threadId: self._activeThreadId, turnId: ctx.activeTurnId },
            });
            cleanup();
            return;
          }
          const detail = ctx.finalError || ctx.finalText || `Codex turn finished with status ${ctx.finalStatus}`;
          reject(new Error(detail));
          cleanup();
        }, 250);
      } catch (error) {
        timers.clearAll();
        reject(error);
        try { terminateProcessTree(self._proc); } catch {}
        try { self._rpc?.close?.(); } catch {}
      }
    });

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
    throw new Error("codex legacy does not support direct message injection; send a follow-up dispatch");
  }

  async interrupt(_opts) {
    this._interrupted = true;
    const turnId = this._ctx?.activeTurnId || null;
    if (!this._activeThreadId || !turnId) {
      try { terminateProcessTree(this._proc); } catch {}
      return;
    }
    try {
      await this._rpc.request("turn/interrupt", {
        threadId: this._activeThreadId,
        turnId,
      }, 30000);
    } catch (error) {
      if (this._rejectPromise) this._rejectPromise(error);
    }
  }

  async steer(text) {
    const turnId = this._ctx?.activeTurnId || null;
    if (!this._activeThreadId || !turnId) {
      throw new Error("No active Codex turn to steer");
    }
    if (!text || !String(text).trim()) {
      throw new Error("Steer body is required");
    }
    await this._rpc.request("turn/steer", {
      threadId: this._activeThreadId,
      input: [{ type: "text", text: String(text) }],
      expectedTurnId: turnId,
    }, 30000);
    this.opts?.callbacks?.onEvent?.("steer", `Steer applied to ${turnId}`);
  }
}
