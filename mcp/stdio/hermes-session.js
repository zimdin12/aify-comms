// Persistent `hermes acp` child per agent. Mirrors PiSession's lifecycle
// (mcp/stdio/pi-session.js): pool keyed by agentId, ensureStarted handshake
// (initialize → session/new), runTurn (session/prompt) reusing the same
// sessionId across turns, idle-timeout reaper, heal-on-failure, terminal
// sink. JSON-RPC framing + sessionUpdate → terminal-frame translation
// live in hermes-acp-protocol.js.
//
// Wire format details: docs/plans/notes/2026-05-23-hermes-acp-spike.md.
// Design rationale: docs/plans/2026-05-23-hermes-acp-persistent-session.md
// + the DECISIONS.md entry for 2026-05-23.

import { spawn } from "node:child_process";
import {
  encodeRequest,
  encodeResponse,
  encodeError,
  parseMessage,
  METHODS,
  formatSessionUpdateAsTerminalFrame,
} from "./hermes-acp-protocol.js";
import { terminateProcessTree, getRuntimeConfig, quoteForDisplay } from "./runtimes.js";

const hermesSessionPool = new Map();

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 45000;
const PROMPT_TIMEOUT_DEFAULT_MS = 12 * 60 * 60 * 1000;
const MAX_TERMINAL_FRAME_BUFFER_CHARS = 65536;
const MAX_ASSISTANT_CAPTURE_CHARS = 262144;
const STDERR_TAIL_CHARS = 32768;

function defaultHermesAcpLauncher() {
  const raw = String(
    process.env.AIFY_HERMES_ACP_COMMAND ||
      process.env.HERMES_ACP_COMMAND ||
      "hermes acp --accept-hooks",
  ).trim();
  // shell-style quoting isn't supported — single space separates tokens.
  // Operators with paths-containing-spaces must use an env-var wrapper.
  const tokens = raw.split(/\s+/).filter(Boolean);
  return { command: tokens[0], args: tokens.slice(1) };
}

function idleTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.hermesIdleTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_HERMES_IDLE_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_IDLE_TIMEOUT_MS;
}

function startupTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.startupTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_HERMES_STARTUP_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return STARTUP_TIMEOUT_DEFAULT_MS;
}

function promptTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const value = Number(cfg.timeoutMs);
  return Number.isFinite(value) && value > 0 ? value : PROMPT_TIMEOUT_DEFAULT_MS;
}

export class HermesSession {
  constructor({ agentId, agentInfo, onPoolEvent = null } = {}) {
    this.agentId = String(agentId || "").trim();
    this.agentInfo = agentInfo || {};
    this.sessionId = "";
    this._state = "idle"; // idle | starting | ready | stopped | failed
    this._proc = null;
    this._readBuffer = "";
    this._pendingResponses = new Map(); // id → { resolve, reject, method, timer }
    this._requestId = 1;
    this._idleTimer = null;
    this._activeTurn = null;
    this._turnQueue = Promise.resolve();
    this._terminalSink = null;
    this._terminalBuffer = [];
    this._terminalBufferChars = 0;
    this._flushing = false;
    this._terminalFlushChain = Promise.resolve();
    this._onPoolEvent = typeof onPoolEvent === "function" ? onPoolEvent : null;
    this._assistantCapture = "";
    this._stderrTail = "";
    this._exitInfo = null;
  }

  _emit(kind, payload) {
    if (!this._onPoolEvent) return;
    try { this._onPoolEvent(kind, payload); } catch {}
  }

  // -------- terminal sink (mirror of PiSession) ----------------------------

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
    // Single-flight drain — same shape as the fixed PiSession flusher
    // (bug-hunt audit B-C1). Chains don't accumulate, the `_terminalFlushChain`
    // promise is reset on each completion so tests/stop() can await drain.
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

  // -------- lifecycle ------------------------------------------------------

  async ensureStarted() {
    if (this._state === "ready") return;
    if (this._state === "stopped" || this._state === "failed") {
      throw new Error(`HermesSession ${this.agentId} is ${this._state}; create a new session`);
    }
    if (this._state === "starting") {
      // Wait for the in-flight start to finish.
      while (this._state === "starting") {
        await new Promise((r) => setTimeout(r, 20));
      }
      if (this._state === "ready") return;
      throw new Error(`HermesSession ${this.agentId} failed to start (state=${this._state})`);
    }
    this._state = "starting";

    const launcher = defaultHermesAcpLauncher();
    const cwd = this.agentInfo.cwd || process.cwd();

    try {
      this._proc = spawn(launcher.command, launcher.args, {
        cwd,
        env: { ...process.env, AIFY_BRIDGE_DISABLED: "1", AIFY_AGENT_ID: "" },
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (error) {
      this._state = "failed";
      throw new Error(`failed to spawn ${launcher.command}: ${error?.message || error}`);
    }

    this._proc.stdout.on("data", (chunk) => this._onStdout(chunk));
    this._proc.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      this._stderrTail = (this._stderrTail + text).slice(-STDERR_TAIL_CHARS);
      this._emit("stderr", quoteForDisplay(text).slice(0, 200));
    });
    this._proc.on("exit", (code, signal) => this._onExit(code, signal));
    this._proc.on("error", (err) => this._onSpawnError(err));
    this._proc.stdin?.on?.("error", () => {});

    const timeout = startupTimeoutFor(this.agentInfo);
    try {
      await Promise.race([
        this._handshake(cwd),
        new Promise((_, rej) => setTimeout(
          () => rej(new Error(`hermes acp handshake timeout (${timeout}ms). stderr tail: ${this._stderrTail.slice(-200)}`)),
          timeout,
        )),
      ]);
      this._state = "ready";
      this._armIdleTimer();
      this._emit("ready", { agentId: this.agentId, sessionId: this.sessionId });
    } catch (error) {
      this._state = "failed";
      try { terminateProcessTree(this._proc); } catch {}
      throw error;
    }
  }

  async _handshake(cwd) {
    const initResult = await this._request(METHODS.INITIALIZE, {
      protocolVersion: 1,
      clientCapabilities: {
        fs: { readTextFile: true, writeTextFile: true },
        terminal: true,
      },
      clientInfo: { name: "aify-comms-bridge", version: "4.0.0" },
    });
    this._emit("initialize", { agentInfo: initResult?.agentInfo });

    const newResult = await this._request(METHODS.SESSION_NEW, {
      cwd,
      mcpServers: [],
    });
    if (!newResult?.sessionId) {
      throw new Error("hermes session/new did not return sessionId");
    }
    this.sessionId = String(newResult.sessionId);
    this._emit("session-new", { sessionId: this.sessionId });
  }

  // -------- JSON-RPC plumbing ----------------------------------------------

  _onStdout(chunk) {
    this._readBuffer += chunk.toString();
    const { messages, remainder } = parseMessage(this._readBuffer);
    this._readBuffer = remainder;
    for (const msg of messages) this._dispatchInbound(msg);
  }

  _dispatchInbound(msg) {
    // Response to one of OUR requests?
    if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
      const pending = this._pendingResponses.get(msg.id);
      if (!pending) return; // late/duplicate response — ignore
      this._pendingResponses.delete(msg.id);
      clearTimeout(pending.timer);
      if (msg.error) {
        pending.reject(new Error(`hermes ${pending.method}: ${msg.error.message || `code ${msg.error.code}`}`));
      } else {
        pending.resolve(msg.result);
      }
      return;
    }
    // session/update is a notification (no id field)
    if (msg.method === METHODS.SESSION_UPDATE && msg.id === undefined) {
      this._handleSessionUpdate(msg.params?.update);
      return;
    }
    // Client-method REQUEST from the agent (fs/*, terminal/*, session/request_permission)
    if (msg.method && msg.id !== undefined) {
      this._handleClientRequest(msg).catch((err) => this._emit("client-handler-error", { method: msg.method, message: err?.message || String(err) }));
      return;
    }
  }

  _request(method, params, { timeoutMs = 30000 } = {}) {
    return new Promise((resolve, reject) => {
      const id = this._requestId++;
      const timer = setTimeout(() => {
        this._pendingResponses.delete(id);
        reject(new Error(`hermes ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this._pendingResponses.set(id, { resolve, reject, method, timer });
      try {
        if (!this._proc || !this._proc.stdin || this._proc.stdin.destroyed) {
          throw new Error(`hermes child not writable (state=${this._state})`);
        }
        this._proc.stdin.write(encodeRequest(id, method, params));
      } catch (err) {
        clearTimeout(timer);
        this._pendingResponses.delete(id);
        reject(err);
      }
    });
  }

  _handleSessionUpdate(update) {
    if (!update) return;
    const kind = String(update.sessionUpdate || "");
    if (kind === "agent_message_chunk") {
      const text = (update.content && update.content.text) || "";
      this._assistantCapture = (this._assistantCapture + text).slice(-MAX_ASSISTANT_CAPTURE_CHARS);
    }
    const frame = formatSessionUpdateAsTerminalFrame(update);
    if (frame) this._pushTerminalFrame(frame, "running");
  }

  async _handleClientRequest(msg) {
    const id = msg.id;
    const method = String(msg.method || "");
    const respond = (result) => this._writeRaw(encodeResponse(id, result));
    const respondError = (code, message) => this._writeRaw(encodeError(id, code, message));
    try {
      if (method === METHODS.FS_READ_TEXT_FILE) {
        const fsMod = await import("node:fs/promises");
        const filePath = String(msg.params?.path || "");
        const content = await fsMod.readFile(filePath, "utf-8");
        respond({ content });
        return;
      }
      if (method === METHODS.FS_WRITE_TEXT_FILE) {
        const fsMod = await import("node:fs/promises");
        const filePath = String(msg.params?.path || "");
        const content = String(msg.params?.content ?? "");
        await fsMod.writeFile(filePath, content, "utf-8");
        respond(null);
        return;
      }
      if (method === METHODS.SESSION_REQUEST_PERMISSION) {
        // Managed-mode dispatches run YOLO — there is no operator at the
        // wheel to answer prompts. Mirror of the `--yolo` flag we used to
        // pass to `hermes chat -q`. If we ever want per-tool gating, this
        // is the hook.
        const options = Array.isArray(msg.params?.options) ? msg.params.options : [];
        const allow =
          options.find((o) => o && (o.kind === "allow_always" || o.kind === "allow_once")) ||
          options[0];
        respond({
          outcome: {
            outcome: "selected",
            optionId: allow?.optionId || allow?.id || "allow",
          },
        });
        return;
      }
      if (
        method === METHODS.TERMINAL_CREATE ||
        method === METHODS.TERMINAL_KILL ||
        method === METHODS.TERMINAL_OUTPUT ||
        method === METHODS.TERMINAL_RELEASE ||
        method === METHODS.TERMINAL_WAIT_FOR_EXIT
      ) {
        respondError(
          -32601,
          `${method}: bridge does not host hermes child terminals; configure hermes to use its own sandbox.`,
        );
        return;
      }
      respondError(-32601, `unknown client method: ${method}`);
    } catch (error) {
      respondError(-32000, error?.message || String(error));
    }
  }

  _writeRaw(line) {
    try {
      if (this._proc && this._proc.stdin && !this._proc.stdin.destroyed) {
        this._proc.stdin.write(line);
      }
    } catch {}
  }

  _onExit(code, signal) {
    this._exitInfo = { code, signal };
    const wasReady = this._state === "ready";
    // Don't downgrade a `failed` state to `stopped`. Race: handshake
    // failure → ensureStarted catch sets state=failed and calls
    // terminateProcessTree; the resulting exit event would otherwise
    // clobber `failed` back to `stopped`.
    if (this._state !== "failed") this._state = "stopped";
    for (const [, pending] of this._pendingResponses) {
      clearTimeout(pending.timer);
      pending.reject(new Error(
        `hermes acp child exited (code=${code} signal=${signal}). stderr tail: ${this._stderrTail.slice(-200)}`,
      ));
    }
    this._pendingResponses.clear();
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    if (wasReady) this._emit("exit", { code, signal });
    hermesSessionPool.delete(this.agentId);
  }

  _onSpawnError(err) {
    this._emit("spawn-error", { message: err?.message || String(err) });
    // The exit handler will fire next and finalize state.
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
      // Already terminal. Clean up the pool entry (in case it lingers) and
      // make sure the OS process is gone. Preserve `failed` so callers can
      // still distinguish a handshake-failure death from a normal stop.
      if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
      try { terminateProcessTree(this._proc); } catch {}
      hermesSessionPool.delete(this.agentId);
      return;
    }
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    if (this.sessionId && this._state === "ready") {
      try {
        await this._request(METHODS.SESSION_CLOSE, { sessionId: this.sessionId }, { timeoutMs: 2000 });
      } catch {}
    }
    try { terminateProcessTree(this._proc); } catch {}
    this._state = "stopped";
    hermesSessionPool.delete(this.agentId);
  }

  // -------- turn execution -------------------------------------------------

  async runTurn({ promptText, run }) {
    await this.ensureStarted();
    // Serialize per-session prompts — hermes acp rejects concurrent
    // session/prompt against the same sessionId.
    return (this._turnQueue = this._turnQueue.then(
      () => this._runTurnInner({ promptText, run }),
      () => this._runTurnInner({ promptText, run }), // queue keeps moving on prior failure
    ));
  }

  async _runTurnInner({ promptText, run }) {
    if (this._state !== "ready") {
      throw new Error(`HermesSession ${this.agentId} not ready (state=${this._state})`);
    }
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    this._assistantCapture = "";
    const turn = { id: run?.id || "", cancelled: false };
    this._activeTurn = turn;

    try {
      const body = String(run?.body || "").trim();
      if (body) {
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const header = subject ? `\r\n> [${from}] ${subject}\r\n` : `\r\n> [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((l) => `> ${l}`).join("\r\n");
        this._pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      }
    } catch {}
    this._pushTerminalFrame("[hermes] thinking...\r\n", "running");

    try {
      const result = await this._request(
        METHODS.SESSION_PROMPT,
        { sessionId: this.sessionId, prompt: [{ type: "text", text: String(promptText || "") }] },
        { timeoutMs: promptTimeoutFor(this.agentInfo) },
      );
      const stopReason = String(result?.stopReason || "end_turn");
      const summary = this._assistantCapture.trim() || "(no output)";

      if (turn.cancelled || stopReason === "cancelled") {
        this._pushTerminalFrame("\r\n[interrupted]\r\n", "running");
        return { status: "cancelled", summary, runtimeState: {}, externalRefs: {} };
      }
      if (stopReason === "refusal") {
        this._pushTerminalFrame(`\r\n[refusal] ${summary}\r\n`, "failed");
        return { status: "failed", summary: `Hermes refused the turn: ${summary}`, runtimeState: {}, externalRefs: {} };
      }
      this._pushTerminalFrame(`\r\n[${stopReason}]\r\n`, "running");
      return { status: "completed", summary, runtimeState: {}, externalRefs: {} };
    } catch (error) {
      this._pushTerminalFrame(`\r\n[error] ${error?.message || error}\r\n`, "failed");
      throw error;
    } finally {
      this._activeTurn = null;
      if (this._state === "ready") this._armIdleTimer();
    }
  }

  async cancelActiveTurn() {
    const turn = this._activeTurn;
    if (!turn) return;
    turn.cancelled = true;
    try {
      await this._request(METHODS.SESSION_CANCEL, { sessionId: this.sessionId }, { timeoutMs: 5000 });
    } catch {}
  }
}

export function getOrCreateHermesSession({ agentId, agentInfo, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("agentId required for HermesSession pool");
  const existing = hermesSessionPool.get(key);
  if (existing && existing._state !== "stopped" && existing._state !== "failed") return existing;
  const sess = new HermesSession({ agentId: key, agentInfo, onPoolEvent });
  hermesSessionPool.set(key, sess);
  return sess;
}

export function _resetHermesSessionPoolForTests() {
  for (const [, sess] of hermesSessionPool) {
    try { sess.stop(); } catch {}
  }
  hermesSessionPool.clear();
}
