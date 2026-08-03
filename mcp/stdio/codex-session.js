// Persistent `codex app-server` child per agent for managed codex
// dispatches. Mirror of `HermesSession` / `PiSession`: pool keyed by
// agentId; spawn + initialize + thread/start once; reuse across turns
// via `turn/start` on the cached thread; idle-timeout reaper; heal on
// app-server crash.
//
// Resident codex (the WebSocket app-server flavor) still goes through
// the legacy controller path — it's already pooled at the app-server
// process level. Only the managed (`spawn-fresh-codex-app-server`) path
// is moved into a pool here.
//
// Wire details and per-turn notification handling mirror what
// createCodexController did per-dispatch; this file consolidates the
// state machine in one place so the controller can stay thin. Items,
// agent-message deltas, quiet/MCP-tool stall detection, fatal runtime
// log detection — all preserved.

import {
  spawnProcess,
  terminateProcessTree,
  createRpcClient,
  defaultCodexCommand,
  managedCodexEffort,
  managedCodexSandboxMode,
  codexTurnSandboxPolicy,
  resolveCodexRequestCwd,
  codexSpawnCwd,
  prepareManagedCodexHome,
  importCodexThreadRollout,
  isFatalCodexRuntimeLog,
  isAifyCommsMcpToolItem,
  describeCodexItem,
  getRuntimeConfig,
  quoteForDisplay,
  buildSystemPrompt,
  buildUserPrompt,
} from "./runtimes.js";
import { detectCodexResumeFailure } from "./codex-errors.js";
import { codexAifyReceiptFrame } from "./aify-console-markers.js";
import { managedCodexServerRequest } from "./runtimes-rpc.js";
import { AIFY_VERSION } from "./version.js";

const codexSessionPool = new Map();

function createDeferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  // Attach a no-op .catch so a rejection on a Deferred that ends up with
  // no real awaiter doesn't become an unhandled-rejection. Real awaiters
  // sharing `promise` still see their own .catch handlers fire.
  promise.catch(() => {});
  return { promise, resolve, reject };
}

const CANCEL_GRACE_MS = 5000;

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 60000;
const DEFAULT_TURN_TIMEOUT_MS = 12 * 60 * 60 * 1000;
const DEFAULT_QUIET_TIMEOUT_MS = 30 * 60 * 1000;
const DEFAULT_AIFY_MCP_TOOL_TIMEOUT_MS = 90 * 1000;
const MAX_TERMINAL_FRAME_BUFFER_CHARS = 65536;

function idleTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.codexIdleTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_CODEX_IDLE_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_IDLE_TIMEOUT_MS;
}

function startupTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.startupTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_CODEX_STARTUP_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return STARTUP_TIMEOUT_DEFAULT_MS;
}

export class CodexSession {
  constructor({ agentId, agentInfo, onPoolEvent = null } = {}) {
    this.agentId = String(agentId || "").trim();
    this.agentInfo = agentInfo || {};
    this.threadId = "";
    this._state = "idle"; // idle | starting | ready | stopped | failed
    this._proc = null;
    this._rpc = null;
    this._idleTimer = null;
    this._activeTurn = null;
    this._turnQueue = Promise.resolve();
    this._terminalSink = null;
    this._terminalBuffer = [];
    this._terminalBufferChars = 0;
    this._flushing = false;
    this._terminalFlushChain = Promise.resolve();
    this._onPoolEvent = typeof onPoolEvent === "function" ? onPoolEvent : null;
    this._managedCodexHome = "";
    this._exitInfo = null;
    this._fatalRuntimeError = null;
    this._startupDeferred = null; // shared barrier for concurrent ensureStarted (fix I10)
  }

  _emit(kind, payload) {
    if (!this._onPoolEvent) return;
    try { this._onPoolEvent(kind, payload); } catch {}
  }

  // ------- terminal sink (mirror of PiSession/HermesSession) ---------------

  attachTerminalSink(sink) {
    this._terminalSink = typeof sink === "function" ? sink : null;
    if (this._terminalSink && this._terminalBuffer.length > 0) this._flushTerminalBuffer();
  }

  detachTerminalSink() { this._terminalSink = null; }

  _pushTerminalFrame(text, status = "") {
    const body = String(text || "");
    const stat = String(status || "");
    if (!body && !stat) return;
    this._terminalBuffer.push({ text: body, status: stat });
    this._terminalBufferChars += body.length;
    while (
      this._terminalBufferChars > MAX_TERMINAL_FRAME_BUFFER_CHARS &&
      this._terminalBuffer.length > 1
    ) {
      const removed = this._terminalBuffer.shift();
      this._terminalBufferChars -= removed.text.length;
    }
    this._flushTerminalBuffer();
  }

  _flushTerminalBuffer() {
    if (!this._terminalSink || this._terminalBuffer.length === 0) return;
    if (this._flushing) return;
    this._flushing = true;
    this._terminalFlushChain = (async () => {
      try {
        while (this._terminalSink && this._terminalBuffer.length > 0) {
          const frame = this._terminalBuffer.shift();
          this._terminalBufferChars = Math.max(0, this._terminalBufferChars - frame.text.length);
          try { await this._terminalSink(frame.text, frame.status); } catch {}
        }
      } finally {
        this._flushing = false;
      }
    })();
  }

  // ------- lifecycle -------------------------------------------------------

  async ensureStarted({ runtimeState = {}, callbacks = {} } = {}) {
    if (this._state === "ready") return;
    if (this._state === "stopped" || this._state === "failed") {
      throw new Error(`CodexSession ${this.agentId} is ${this._state}; create a new session`);
    }
    if (this._state === "starting") {
      // Concurrent ensureStarted callers wait on the same Deferred (fix
      // I10: avoids busy-poll and spin-forever if state gets stuck).
      if (this._startupDeferred) return this._startupDeferred.promise;
      throw new Error(`CodexSession ${this.agentId} starting without barrier`);
    }
    this._state = "starting";
    this._startupDeferred = createDeferred();

    const config = getRuntimeConfig(this.agentInfo);
    const launcher = defaultCodexCommand();
    const hostCwd = this.agentInfo.cwd || process.cwd();
    const model = String(this.agentInfo.model || config.model || "").trim();
    const effort = managedCodexEffort(config);
    const approvalPolicy = config.approvalPolicy || "never";
    const sandboxMode = managedCodexSandboxMode(config, "managed");
    const cwd = resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl: "" });
    const spawnCwd = codexSpawnCwd(launcher, hostCwd);
    this._managedCodexHome = prepareManagedCodexHome({ workspace: cwd, model, effort });

    try {
      // Env merge (fix I8): inherit parent env + overlay CODEX_HOME if set.
      // Previously the sparse `{}` (or `{CODEX_HOME: …}` alone) relied on
      // spawnProcess to merge, but the asymmetry with PiSession/HermesSession
      // (which pass {...process.env, …}) was a latent foot-gun for any future
      // spawn helper that does NOT merge by default.
      const childEnv = { ...process.env };
      if (this._managedCodexHome) childEnv.CODEX_HOME = this._managedCodexHome;
      this._proc = spawnProcess(launcher.command, launcher.args, {
        cwd: spawnCwd,
        env: childEnv,
      });
    } catch (error) {
      this._state = "failed";
      const wrapped = new Error(`failed to spawn ${launcher.command}: ${error?.message || error}`);
      const d = this._startupDeferred; this._startupDeferred = null;
      d?.reject(wrapped);
      try { await d?.promise; } catch {}
      throw wrapped;
    }

    this._rpc = createRpcClient(this._proc, {
      onNotification: (msg) => this._dispatchNotification(msg),
      onStderr: (line) => this._dispatchRuntimeLog(line, callbacks),
      onRequest: managedCodexServerRequest,
    });
    this._proc.on("exit", (code, signal) => this._onExit(code, signal));
    this._proc.on("error", (err) => this._onSpawnError(err));

    const startupMs = startupTimeoutFor(this.agentInfo);
    const deferred = this._startupDeferred;
    let startupTimer = null;
    try {
      await Promise.race([
        this._handshake({ cwd, model, approvalPolicy, sandboxMode, runtimeState, callbacks }),
        new Promise((_, rej) => {
          startupTimer = setTimeout(() => rej(new Error(`codex handshake timeout (${startupMs}ms)`)), startupMs);
        }),
      ]);
      if (startupTimer) {
        clearTimeout(startupTimer);
        startupTimer = null;
      }
      this._state = "ready";
      this._armIdleTimer();
      this._startupDeferred = null;
      deferred.resolve();
      this._emit("ready", { agentId: this.agentId, threadId: this.threadId });
    } catch (error) {
      if (startupTimer) {
        clearTimeout(startupTimer);
        startupTimer = null;
      }
      this._state = "failed";
      try { this._rpc?.close?.(); } catch {}
      try { terminateProcessTree(this._proc); } catch {}
      this._startupDeferred = null;
      deferred.reject(error);
      throw error;
    }
  }

  async _handshake({ cwd, model, approvalPolicy, sandboxMode, runtimeState, callbacks }) {
    await this._rpc.request("initialize", {
      clientInfo: { name: "aify-comms", title: "aify-comms dispatch bridge", version: AIFY_VERSION },
    });
    this._rpc.notify("initialized", {});

    const hintThreadId = String(runtimeState?.threadId || this.agentInfo?.sessionHandle || "").trim();
    if (hintThreadId) {
      try {
        const resumed = await this._rpc.request("thread/resume", {
          threadId: hintThreadId,
          personality: "friendly",
        }, 60000);
        this.threadId = resumed?.thread?.id || hintThreadId;
        this._emit("thread-resumed", { threadId: this.threadId });
        return;
      } catch (error) {
        const failure = detectCodexResumeFailure(error);
        const allowFreshContext = String(runtimeState?.resumePolicy || "native_first").toLowerCase() === "fresh_context";

        if (failure.noRollout && this._managedCodexHome) {
          const imported = importCodexThreadRollout({ threadId: hintThreadId, targetHome: this._managedCodexHome });
          if (imported.imported) {
            try {
              const resumed = await this._rpc.request("thread/resume", { threadId: hintThreadId, personality: "friendly" }, 60000);
              this.threadId = resumed?.thread?.id || hintThreadId;
              this._emit("thread-resumed-after-import", { threadId: this.threadId, sourceHome: imported.sourceHome });
              return;
            } catch (retryError) {
              throw new Error(
                `Codex thread/resume failed for ${hintThreadId} after importing rollout from ${imported.sourceHome}: ${retryError?.message || retryError}`,
                { cause: retryError },
              );
            }
          }
        }
        if (!allowFreshContext && failure.shouldHeal) {
          throw new Error(
            `Codex thread/resume failed for saved thread ${hintThreadId} (${failure.healReason}: ${error?.message || error}). ` +
            `Bridge did not create a fresh thread because that would discard native chat memory. ` +
            `Use Dashboard -> Sessions -> Recreate if you intentionally want a new context.`,
            { cause: error },
          );
        }
        if (!failure.shouldHeal) {
          throw new Error(
            `Codex thread/resume failed for thread ${hintThreadId} with unhandled error: ${error?.message || error}`,
            { cause: error },
          );
        }
        // allowFreshContext + shouldHeal: fall through to thread/start.
        const previousThreadId = hintThreadId;
        await this._startFreshThread({ cwd, model, approvalPolicy, sandboxMode });
        if (this.threadId && this.threadId !== previousThreadId) {
          try { await callbacks?.onSessionHandleChange?.(this.threadId, { previous: previousThreadId, reason: failure.healReason }); } catch {}
        }
        return;
      }
    }
    await this._startFreshThread({ cwd, model, approvalPolicy, sandboxMode });
    // Bug #2.6 (operator-reported 2026-05-24): without this notification,
    // the agent's session_handle in the service DB stays empty after the
    // first managed run — subsequent Console PTY launches read an empty
    // handle and the bridge can't recover the threadId after a restart.
    // Mirror of the heal-path notification above (line 280) but for the
    // no-hint initial start.
    if (this.threadId) {
      try {
        await callbacks?.onSessionHandleChange?.(this.threadId, { reason: "initial_thread_start" });
      } catch {}
    }
  }

  async _startFreshThread({ cwd, model, approvalPolicy, sandboxMode }) {
    const threadStartParams = { cwd, approvalPolicy, personality: "friendly", serviceName: "aify-comms" };
    if (model) threadStartParams.model = model;
    let started;
    try {
      started = await this._rpc.request("thread/start", { ...threadStartParams, sandbox: sandboxMode }, 60000);
    } catch (error) {
      const message = error?.message || "";
      if (sandboxMode !== "workspace-write" || !message.includes("unknown variant `workspace-write`")) throw error;
      started = await this._rpc.request("thread/start", { ...threadStartParams, sandbox: "workspaceWrite" }, 60000);
    }
    this.threadId = started?.thread?.id || "";
    if (!this.threadId) throw new Error("codex thread/start did not return a thread id");
    this._emit("thread-started", { threadId: this.threadId });
  }

  // ------- notification routing --------------------------------------------

  _dispatchNotification(message) {
    const turn = this._activeTurn;
    if (!turn) {
      // No active turn — agent emitted something between turns (rare).
      // Emit as a pool event so operator-visible logs capture it (fix I9
      // from 2026-05-23 review: pre-fix the comment said "push to
      // terminal as dim text" but the code just returned, silently
      // dropping potentially-important late notifications like
      // turn/completed arrivals or error/* events from a hung child).
      try {
        const method = String(message?.method || "<unknown>");
        const brief = JSON.stringify(message?.params || {}).slice(0, 200);
        this._emit("notification-no-active-turn", { method, paramsBrief: brief });
      } catch {}
      return;
    }
    const params = message.params || {};
    // STALE-TURN GUARD (bughunt 2026-07-03): a timed-out/stalled turn is left
    // running app-server-side (see the interrupt added to the stall timers), so its
    // late deltas / turn/completed can arrive AFTER a NEW dispatch became the active
    // turn — routing purely off _activeTurn then settled the wrong turn with the old
    // turn's output/status. Drop any notification stamped with a DIFFERENT turn id
    // than the one currently active (notifications without a turn id pass through).
    const msgTurnId = params.turn?.id;
    if (msgTurnId && turn.activeTurnId && msgTurnId !== turn.activeTurnId) {
      try {
        this._emit("notification-stale-turn", { method: String(message?.method || "<unknown>"), msgTurnId, activeTurnId: turn.activeTurnId });
      } catch {}
      return;
    }
    turn.markActivity(message.method || "runtime notification");
    if (message.method === "turn/started" && params.turn?.id) {
      turn.activeTurnId = params.turn.id;
      turn.callbacks?.onRefs?.({ threadId: this.threadId, turnId: turn.activeTurnId });
      turn.callbacks?.onEvent?.("turn", `Started turn ${turn.activeTurnId}`);
      // TERTIARY pure-event (2026-06-19): the codex app-server's native turn/started is the
      // event-EXACT working signal — cleaner than the 5s rollout-tail poll (no FS-flush latency,
      // no schema-drift, no #136 uuid-mismatch no-op). Fire it so the caller can set working the
      // instant codex begins generating. Additive + idempotent with the dispatch-boundary set.
      try { turn.callbacks?.onTurnStart?.(); } catch {}
      this._pushTerminalFrame(`\r\n\x1b[96m\x1b[1m▶ turn started\x1b[0m\r\n`);
    } else if (message.method === "turn/completed") {
      turn.finalStatus = params.turn?.status || "completed";
      if (params.turn?.error?.message) turn.finalError = params.turn.error.message;
      const usage = params.turn?.usage || params.usage;
      const usageStr = usage && (usage.input_tokens || usage.output_tokens)
        ? ` \x1b[2m(in=${usage.input_tokens || 0} out=${usage.output_tokens || 0})\x1b[0m`
        : "";
      this._pushTerminalFrame(`\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m${usageStr}\r\n`);
      if (turn.finalStatus === "completed" || turn.finalStatus === "interrupted" || turn.finalStatus === "failed") {
        turn.settled = true;
        // TERTIARY pure-event (2026-06-19): turn/completed is the real, event-exact turn-end —
        // fire it so the caller clears `working` immediately on the native signal rather than
        // waiting for the rollout-tail detector or the dispatch settle. Additive + idempotent.
        try { turn.callbacks?.onTurnEnd?.(); } catch {}
      }
    } else if (message.method === "item/agentMessage/delta") {
      const delta = params.delta || "";
      if (delta) {
        turn.finalText += delta;
        this._pushTerminalFrame(String(delta));
      }
    } else if (message.method === "item/completed" && params.item?.type === "agentMessage") {
      turn.finalText = params.item.text || turn.finalText;
      if (params.item?.id) turn.activeItems.delete(params.item.id);
    } else if (message.method === "item/started" && params.item?.id) {
      const itemType = describeCodexItem(params.item);
      turn.activeItems.set(params.item.id, { label: itemType, startedAt: Date.now() });
      turn.callbacks?.onEvent?.("codex", `Started ${itemType}`);
      this._pushTerminalFrame(`\r\n\x1b[33m→ ${itemType}\x1b[0m\r\n`);
    } else if (message.method === "item/completed" && params.item?.id) {
      const itemType = turn.activeItems.get(params.item.id)?.label || describeCodexItem(params.item);
      turn.activeItems.delete(params.item.id);
      turn.callbacks?.onEvent?.("codex", `Completed ${itemType}`);
      this._pushTerminalFrame(`\x1b[32m✓ ${itemType}\x1b[0m\r\n`);
    } else if (message.method === "error" && params.error?.message) {
      turn.finalError = params.error.message;
      this._pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m \x1b[31m${params.error.message}\x1b[0m\r\n`);
    }
  }

  _dispatchRuntimeLog(line, callbacks) {
    const text = quoteForDisplay(line);
    if (!text) return;
    callbacks?.onEvent?.("stderr", text);
    if (isFatalCodexRuntimeLog(text)) {
      this._fatalRuntimeError = `Codex runtime fatal error: ${text}`;
      const turn = this._activeTurn;
      if (turn && !turn.settled) {
        turn.finalStatus = "failed";
        turn.finalError = this._fatalRuntimeError;
        turn.settled = true;
      }
      try { terminateProcessTree(this._proc); } catch {}
    }
  }

  _onExit(code, signal) {
    this._exitInfo = { code, signal };
    if (this._state !== "failed") this._state = "stopped";
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    const turn = this._activeTurn;
    if (turn && !turn.settled) {
      turn.finalStatus = "failed";
      turn.finalError = this._fatalRuntimeError || `codex app-server exited (code=${code} signal=${signal})`;
      turn.settled = true;
    }
    codexSessionPool.delete(this.agentId);
    this._emit("exit", { code, signal });
  }

  _onSpawnError(err) {
    this._emit("spawn-error", { message: err?.message || String(err) });
    // Mirror PiSession: don't wait for _onExit to flip state — spawn
    // errors are terminal. Reject any pending startup so concurrent
    // ensureStarted callers fail fast instead of hanging on the deferred
    // until the handshake-timeout (fix Minor #16 from 2026-05-23 review).
    if (this._state === "starting" || this._state === "ready") {
      this._state = "failed";
      const deferred = this._startupDeferred;
      this._startupDeferred = null;
      if (deferred) {
        try { deferred.reject(new Error(`codex spawn error: ${err?.message || err}`)); } catch {}
      }
    }
  }

  _armIdleTimer() {
    if (this._idleTimer) clearTimeout(this._idleTimer);
    const ms = idleTimeoutFor(this.agentInfo);
    this._idleTimer = setTimeout(() => {
      this._emit("idle-reap", { agentId: this.agentId });
      this.stop().catch(() => {});
    }, ms);
    if (typeof this._idleTimer.unref === "function") this._idleTimer.unref();
  }

  async stop() {
    if (this._state === "stopped" || this._state === "failed") {
      if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
      // Drain in-flight terminal frames before teardown (Minor #17 from
      // 2026-05-23 review) so the last bits of operator-visible output
      // land before the process is killed.
      try { await this._terminalFlushChain; } catch {}
      try { this._rpc?.close?.(); } catch {}
      try { terminateProcessTree(this._proc); } catch {}
      codexSessionPool.delete(this.agentId);
      return;
    }
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    // Set state BEFORE tearing down RPC/process so any queued turns
    // chained behind the current one see _state='stopped' on their
    // _runTurnInner entry and fail-fast with "not ready" instead of
    // racing into RPC-close errors (Minor #12 from 2026-05-23 review).
    this._state = "stopped";
    try { await this._terminalFlushChain; } catch {}
    try { this._rpc?.close?.(); } catch {}
    try { terminateProcessTree(this._proc); } catch {}
    codexSessionPool.delete(this.agentId);
  }

  // ------- turn execution --------------------------------------------------

  async runTurn({ promptText, run, callbacks = {}, runtimeState = {} }) {
    // Detect threadId-hint mismatch (fix I11 from 2026-05-23 review).
    // If the caller passed a runtimeState.threadId that's different from
    // the session's cached this.threadId, the pre-fix code silently
    // ignored the hint — only the first turn's hint mattered, all
    // subsequent calls were forced to keep using the cached threadId.
    // That broke "fresh context" requests (Dashboard → Sessions →
    // Recreate writes a new agent.sessionHandle; the next dispatch's
    // runtimeState would carry the new id but the persistent CodexSession
    // wouldn't honor it). Fix: detect mismatch, stop+evict the session
    // so the next ensureStarted reads the new hint and spawns fresh.
    const hint = String(runtimeState?.threadId || "").trim();
    if (
      hint &&
      this.threadId &&
      hint !== this.threadId &&
      (this._state === "ready" || this._state === "starting")
    ) {
      this._emit("threadid-mismatch", { sessionThreadId: this.threadId, hintedThreadId: hint });
      try { callbacks?.onEvent?.("codex", `threadId hint mismatch (cached=${this.threadId}, hinted=${hint}); evicting session for a fresh start`); } catch {}
      try { await this.stop(); } catch {}
      throw new Error(
        `Codex threadId hint mismatch: session was on thread ${this.threadId}, caller hinted ${hint}. ` +
        `Session has been torn down; the next dispatch will spawn a fresh codex app-server on the new thread.`,
      );
    }
    await this.ensureStarted({ runtimeState, callbacks });
    // Per-caller Deferred isolates each caller's promise from prior-turn
    // rejections; the queue itself absorbs rejections via .catch so the
    // chain keeps advancing (fix C3).
    const deferred = createDeferred();
    this._turnQueue = this._turnQueue
      .catch(() => {})
      .then(() => this._runTurnInner({ promptText, run, callbacks, runtimeState }))
      .then(deferred.resolve, deferred.reject);
    return deferred.promise;
  }

  async _runTurnInner({ promptText, run, callbacks, runtimeState }) {
    if (this._state !== "ready") {
      throw new Error(`CodexSession ${this.agentId} not ready (state=${this._state})`);
    }
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }

    const config = getRuntimeConfig(this.agentInfo);
    const timeoutMs = Number(config.timeoutMs || DEFAULT_TURN_TIMEOUT_MS);
    const configuredQuietTimeout = Number(config.quietTimeoutMs ?? config.silenceTimeoutMs ?? DEFAULT_QUIET_TIMEOUT_MS);
    const quietTimeoutMs = configuredQuietTimeout <= 0 ? 0 : Math.max(10 * 60 * 1000, configuredQuietTimeout);
    const configuredAifyMcpToolTimeout = Number(config.mcpToolTimeoutMs ?? config.commsToolTimeoutMs ?? DEFAULT_AIFY_MCP_TOOL_TIMEOUT_MS);
    const aifyMcpToolTimeoutMs = configuredAifyMcpToolTimeout <= 0 ? 0 : Math.max(10 * 1000, configuredAifyMcpToolTimeout);
    const model = String(this.agentInfo.model || config.model || "").trim();
    const effort = managedCodexEffort(config);
    const approvalPolicy = config.approvalPolicy || "never";
    const sandboxMode = managedCodexSandboxMode(config, "managed");
    const summaryMode = config.summary || "concise";
    const networkAccess = config.networkAccess !== false;
    const hostCwd = this.agentInfo.cwd || process.cwd();
    const launcher = defaultCodexCommand();
    const cwd = resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl: "" });

    const turn = {
      id: run?.id || "",
      activeTurnId: null,
      finalText: "",
      finalStatus: "failed",
      finalError: "",
      settled: false,
      cancelled: false,
      activeItems: new Map(),
      lastActivityAt: Date.now(),
      activityLabel: "turn launch",
      callbacks,
      markActivity(label = "runtime event") {
        this.lastActivityAt = Date.now();
        this.activityLabel = label;
      },
    };
    this._activeTurn = turn;

    try {
      const body = String(run?.body || "").trim();
      if (body) {
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        this._pushTerminalFrame(codexAifyReceiptFrame(), "running");
        const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((line) => `\x1b[92m>\x1b[0m ${line}`).join("\r\n");
        this._pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      }
      this._pushTerminalFrame("\x1b[2m[codex] working...\x1b[0m\r\n", "running");
    } catch {}

    // Deferred + sequential awaits replaces the prior
    // `new Promise(async (resolve, reject) => ...)` antipattern (fix C4):
    // any synchronous throw inside an async Promise constructor body is
    // unhandled. Using a Deferred + a normal async function keeps the
    // surrounding try/finally semantics clean.
    const deferred = createDeferred();
    let timer, quietTimer, mcpToolTimer, poll;
    const clearAllTimers = () => {
      clearTimeout(timer);
      clearInterval(quietTimer);
      clearInterval(mcpToolTimer);
      clearInterval(poll);
    };

    // Best-effort: tell the app-server to actually STOP the abandoned turn when a
    // stall/timeout forces settlement (bughunt 2026-07-03). Without this the turn
    // keeps running server-side and its late notifications contaminate the next
    // dispatch (now also guarded by the stale-turn id check in _dispatchNotification).
    const interruptAbandonedTurn = () => {
      const id = turn.activeTurnId;
      if (!id) return;
      try {
        Promise.resolve(this._rpc.request("turn/interrupt", { threadId: this.threadId, turnId: id }, 10000)).catch(() => {});
      } catch {}
    };
    timer = setTimeout(() => {
      if (!turn.settled) {
        turn.finalStatus = "failed";
        turn.finalError = `Codex run timed out after ${timeoutMs}ms`;
        turn.settled = true;
        interruptAbandonedTurn();
      }
    }, timeoutMs);
    if (quietTimeoutMs > 0) {
      quietTimer = setInterval(() => {
        try {
          if (turn.settled) return;
          const idleFor = Date.now() - turn.lastActivityAt;
          if (idleFor < quietTimeoutMs) return;
          const activeLabel = turn.activeItems.size
            ? ` Active Codex item(s): ${[...new Set([...turn.activeItems.values()].map((item) => item.label))].join(", ")}.`
            : "";
          turn.finalStatus = "failed";
          turn.finalError =
            `Codex run produced no runtime activity for ${quietTimeoutMs}ms after ${turn.activityLabel}.` +
            activeLabel +
            ` The turn was treated as stalled and terminated. Retry the message, or restart the session if this repeats.`;
          turn.settled = true;
          try { callbacks.onEvent?.("stalled", turn.finalError); } catch {}
          interruptAbandonedTurn();
        } catch {}
      }, Math.min(60 * 1000, Math.max(10 * 1000, Math.floor(quietTimeoutMs / 6))));
    }
    if (aifyMcpToolTimeoutMs > 0) {
      mcpToolTimer = setInterval(() => {
        try {
          if (turn.settled) return;
          const now = Date.now();
          const stuck = [...turn.activeItems.values()].find((item) =>
            isAifyCommsMcpToolItem(item.label) && now - item.startedAt >= aifyMcpToolTimeoutMs,
          );
          if (!stuck) return;
          turn.finalStatus = "failed";
          turn.finalError =
            `Codex aify-comms MCP tool call produced no completion for ${aifyMcpToolTimeoutMs}ms. ` +
            `The turn was terminated before the general quiet-stall timeout.`;
          turn.settled = true;
          try { callbacks.onEvent?.("mcp_tool_stalled", turn.finalError); } catch {}
          interruptAbandonedTurn();
        } catch {}
      }, Math.min(10 * 1000, Math.max(2 * 1000, Math.floor(aifyMcpToolTimeoutMs / 6))));
    }

    try {
      callbacks.onEvent?.("turn", `Calling turn/start on thread ${this.threadId} with cwd="${cwd}"`);
      let turnResp;
      try {
        turnResp = await this._rpc.request("turn/start", {
          threadId: this.threadId,
          input: [{ type: "text", text: String(promptText || "") }],
          cwd,
          approvalPolicy,
          sandboxPolicy: codexTurnSandboxPolicy(sandboxMode, cwd, networkAccess),
          effort,
          summary: summaryMode,
          personality: "friendly",
          ...(model ? { model } : {}),
        }, 60000);
      } catch (error) {
        throw new Error(
          `Codex turn/start failed for thread ${this.threadId} (cwd="${cwd}"): ${error?.message || error}`,
          { cause: error },
        );
      }
      turn.activeTurnId = turnResp?.turn?.id || turn.activeTurnId;
      callbacks.onRefs?.({ threadId: this.threadId, turnId: turn.activeTurnId });
      turn.markActivity("turn/start");

      poll = setInterval(() => {
        if (!turn.settled) return;
        clearAllTimers();
        if (turn.finalStatus === "completed") {
          deferred.resolve({
            status: "completed",
            summary: turn.finalText.trim() || "(no output)",
            runtimeState: { threadId: this.threadId },
            externalRefs: { threadId: this.threadId, turnId: turn.activeTurnId },
          });
          return;
        }
        if (turn.finalStatus === "interrupted" || turn.cancelled) {
          deferred.resolve({
            status: "cancelled",
            summary: turn.finalText.trim() || turn.finalError || "Run interrupted",
            runtimeState: { threadId: this.threadId },
            externalRefs: { threadId: this.threadId, turnId: turn.activeTurnId },
          });
          return;
        }
        const detail = turn.finalError || turn.finalText || `Codex turn finished with status ${turn.finalStatus}`;
        deferred.reject(new Error(detail));
      }, 250);
    } catch (error) {
      clearAllTimers();
      deferred.reject(error);
    }

    try {
      return await deferred.promise;
    } finally {
      clearAllTimers();
      this._activeTurn = null;
      // After the turn settles, the codex app-server stays alive in the pool.
      // Idle timer rearms; next dispatch reuses the same RPC + threadId.
      if (this._state === "ready") this._armIdleTimer();
    }
  }

  async cancelActiveTurn() {
    const turn = this._activeTurn;
    if (!turn) return;
    turn.cancelled = true;
    if (!turn.activeTurnId) {
      // No turn id yet; force settlement immediately.
      turn.finalStatus = "interrupted";
      turn.settled = true;
      return;
    }
    try {
      await this._rpc.request("turn/interrupt", {
        threadId: this.threadId,
        turnId: turn.activeTurnId,
      }, 30000);
    } catch {}
    // Grace fallback (fix C2): if codex doesn't emit turn/completed
    // within CANCEL_GRACE_MS, force the turn to settle so the poll loop
    // exits and the dispatcher doesn't hang until timeoutMs. The codex
    // app-server is expected to honor turn/interrupt with a turn/completed
    // (status=interrupted) but we can't depend on it under a stalled
    // condition — which is exactly when cancel is most needed.
    setTimeout(() => {
      if (turn.settled) return;
      turn.finalStatus = "interrupted";
      turn.finalError = turn.finalError || `Codex did not honor turn/interrupt within ${CANCEL_GRACE_MS}ms; forcing settle`;
      turn.settled = true;
    }, CANCEL_GRACE_MS).unref?.();
  }

  async steer(text) {
    const turn = this._activeTurn;
    if (!turn || !turn.activeTurnId) throw new Error("No active Codex turn to steer");
    if (!text || !String(text).trim()) throw new Error("Steer body is required");
    await this._rpc.request("turn/steer", {
      threadId: this.threadId,
      input: [{ type: "text", text: String(text) }],
      expectedTurnId: turn.activeTurnId,
    }, 30000);
  }
}

export function getOrCreateCodexSession({ agentId, agentInfo, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("agentId required for CodexSession pool");
  const existing = codexSessionPool.get(key);
  if (existing) {
    if (existing._state === "stopped" || existing._state === "failed") {
      // Heal-on-lookup: terminal-state entry would force the caller into
      // an "is failed; create a new session" error path. Instead, evict
      // and spawn a fresh one transparently (fix C1: pool-heal race).
      codexSessionPool.delete(key);
    } else {
      return existing;
    }
  }
  const sess = new CodexSession({ agentId: key, agentInfo, onPoolEvent });
  codexSessionPool.set(key, sess);
  return sess;
}

export function _resetCodexSessionPoolForTests() {
  for (const [, sess] of codexSessionPool) {
    try { sess.stop(); } catch {}
  }
  codexSessionPool.clear();
}

export async function shutdownAllCodexSessions(reason = "shutdown") {
  const sessions = [...codexSessionPool.values()];
  codexSessionPool.clear();
  await Promise.all(sessions.map((s) => s.stop(reason).catch(() => {})));
}

export function __injectCodexSessionForTests(agentId, session) {
  codexSessionPool.set(agentId, session);
}

export function __codexSessionPoolSize() {
  return codexSessionPool.size;
}
