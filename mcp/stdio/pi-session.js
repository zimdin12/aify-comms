// Persistent Oh My Pi (`omp --mode rpc`) child per agent. The child is reused
// across dispatches: one spawn powers many sequential turns. Lifecycle and 13
// gotchas are documented in docs/plans/pi-persistent-rpc.md.

import readline from "readline";
import {
  spawnProcess,
  terminateProcessTree,
  defaultPiCommand,
  runtimeLaunchAvailability,
  normalizePiModelOverride,
  getRuntimeConfig,
  detectPiRuntimeFailure,
  extractPiSessionState,
  extractPiAssistantText,
  buildSystemPrompt,
  buildUserPrompt,
  quoteForDisplay,
  diagnosticsFor,
} from "./runtimes.js";

const piSessionPool = new Map();

const MAX_PI_ASSISTANT_CAPTURE_CHARS = 262144;
const MAX_PI_ERROR_CAPTURE_CHARS = 65536;
const PI_TRUNCATION_MARKER = "\n...[aify truncated middle output]...\n";
const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 45000;
const INTERRUPT_GRACE_MS = 5000;
const MAX_TERMINAL_FRAME_BUFFER_CHARS = 65536;
const MAX_TOOL_INPUT_BRIEF_CHARS = 240;
const MAX_TOOL_RESULT_BRIEF_CHARS = 320;

function boundText(value, limit, { preserveEdges = false } = {}) {
  const text = String(value || "");
  if (text.length <= limit) return text;
  if (!preserveEdges) return text.slice(text.length - limit);
  const payloadLimit = Math.max(0, limit - PI_TRUNCATION_MARKER.length);
  const headLength = Math.ceil(payloadLimit / 2);
  const tailLength = Math.floor(payloadLimit / 2);
  return `${text.slice(0, headLength)}${PI_TRUNCATION_MARKER}${text.slice(text.length - tailLength)}`;
}

function appendBounded(current, chunk, options = {}) {
  const limit = options.limit || MAX_PI_ERROR_CAPTURE_CHARS;
  return boundText(`${String(current || "")}${String(chunk || "")}`, limit, options);
}

function briefJsonInline(value, limit) {
  if (value === undefined || value === null) return "";
  let text;
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  }
  text = text.replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}…`;
}

function formatToolInputBrief(input) {
  return briefJsonInline(input, MAX_TOOL_INPUT_BRIEF_CHARS);
}

function formatToolResultBrief(result) {
  if (result === undefined || result === null) return "";
  if (typeof result === "string") return briefJsonInline(result, MAX_TOOL_RESULT_BRIEF_CHARS);
  if (Array.isArray(result)) {
    const text = result
      .map((part) => {
        if (part && typeof part === "object" && typeof part.text === "string") return part.text;
        return part;
      })
      .join("\n");
    return briefJsonInline(text, MAX_TOOL_RESULT_BRIEF_CHARS);
  }
  if (typeof result === "object") {
    if (typeof result.text === "string") return briefJsonInline(result.text, MAX_TOOL_RESULT_BRIEF_CHARS);
    if (Array.isArray(result.content)) return formatToolResultBrief(result.content);
    return briefJsonInline(result, MAX_TOOL_RESULT_BRIEF_CHARS);
  }
  return briefJsonInline(String(result), MAX_TOOL_RESULT_BRIEF_CHARS);
}

// ANSI color helpers. xterm.js's WebGL renderer (current dashboard build)
// handles standard 16-color + bright variants and bold/dim. We use a small
// palette consistently so the synthesized terminal feels distinguishable
// from raw assistant text without being a circus.
const ANSI = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  blue: "\x1b[34m",
  magenta: "\x1b[35m",
  cyan: "\x1b[36m",
  brightGreen: "\x1b[92m",
  brightYellow: "\x1b[93m",
  brightCyan: "\x1b[96m",
};

function colorize(color, text) {
  if (!text) return "";
  return `${color}${text}${ANSI.reset}`;
}

function formatTokenUsage(usage) {
  if (!usage || typeof usage !== "object") return "";
  const input = Number(usage.input_tokens ?? usage.inputTokens ?? usage.prompt_tokens ?? usage.promptTokens);
  const output = Number(usage.output_tokens ?? usage.outputTokens ?? usage.completion_tokens ?? usage.completionTokens);
  const cached = Number(usage.cached_tokens ?? usage.cacheReadInputTokens ?? usage.cache_read_input_tokens);
  const parts = [];
  if (Number.isFinite(input) && input > 0) parts.push(`in=${input}`);
  if (Number.isFinite(output) && output > 0) parts.push(`out=${output}`);
  if (Number.isFinite(cached) && cached > 0) parts.push(`cached=${cached}`);
  return parts.length ? parts.join(" ") : "";
}

export function formatPiEventAsTerminalFrame(event) {
  if (!event || typeof event !== "object") return "";
  const type = String(event.type || "");
  switch (type) {
    case "ready":
      // The banner with model/effort/session is emitted separately by
      // _emitReadyBanner so we can use PiSession context. The "ready" event
      // itself produces no synthesized frame here; the banner replaces it.
      return "";
    case "agent_start":
      return `\r\n${colorize(ANSI.brightCyan + ANSI.bold, "▶ turn started")}\r\n`;
    case "agent_end": {
      const usage = formatTokenUsage(event.usage ?? event.message?.usage ?? event.data?.usage);
      const suffix = usage ? colorize(ANSI.dim, `  (${usage})`) : "";
      return `\r\n${colorize(ANSI.cyan + ANSI.bold, "■ turn ended")}${suffix}\r\n`;
    }
    case "error": {
      const msg = String(event.error || event.message || "Pi runtime error");
      return `\r\n${colorize(ANSI.red + ANSI.bold, "✗ error")} ${colorize(ANSI.red, msg)}\r\n`;
    }
    case "tool_execution_start": {
      const name = String(event.tool?.name || event.toolName || event.name || "tool");
      const brief = formatToolInputBrief(event.tool?.input ?? event.input ?? event.arguments);
      const head = colorize(ANSI.yellow, `→ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `\r\n${head}${detail}\r\n`;
    }
    case "tool_execution_end": {
      const name = String(event.tool?.name || event.toolName || event.name || "tool");
      const ok = event.success !== false && !event.error;
      const brief = ok
        ? formatToolResultBrief(event.tool?.result ?? event.result ?? event.output)
        : briefJsonInline(event.error || "", MAX_TOOL_RESULT_BRIEF_CHARS);
      const marker = ok
        ? colorize(ANSI.green, `✓ ${name}`)
        : colorize(ANSI.red, `✗ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `${marker}${detail}\r\n`;
    }
    case "RpcExtensionUIRequest": {
      const req = event.request || event;
      const kind = String(req.kind || req.type || "input");
      const question = String(req.question || req.prompt || req.message || "");
      const options = Array.isArray(req.options) ? req.options.join(" | ") : "";
      const detail = options ? colorize(ANSI.dim, ` (${options})`) : "";
      return `\r\n${colorize(ANSI.magenta + ANSI.bold, `? ${kind}`)} ${question}${detail}\r\n`;
    }
    case "message_update": {
      const inner = event.assistantMessageEvent || {};
      if (inner.type === "text_delta") return String(inner.delta || "");
      if (inner.type === "text_end") return "\r\n";
      return "";
    }
    case "usage":
    case "token_usage": {
      const usage = formatTokenUsage(event.usage ?? event.data ?? event);
      return usage ? `${colorize(ANSI.dim, `  ${usage}`)}\r\n` : "";
    }
    default:
      return "";
  }
}

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function idleTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.piIdleTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_PI_IDLE_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_IDLE_TIMEOUT_MS;
}

function startupTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.startupTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_PI_STARTUP_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return STARTUP_TIMEOUT_DEFAULT_MS;
}

function timeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const value = Number(cfg.timeoutMs);
  return Number.isFinite(value) && value > 0 ? value : 12 * 60 * 60 * 1000;
}

export class PiSession {
  constructor({ agentId, agentInfo, sessionId = "", onPoolEvent = null } = {}) {
    this.agentId = String(agentId || "").trim();
    this.agentInfo = agentInfo || {};
    this._state = "idle";
    this.sessionId = String(sessionId || "").trim();
    this.sessionFile = "";
    this._onPoolEvent = typeof onPoolEvent === "function" ? onPoolEvent : null;
    this._idleTimeoutMs = idleTimeoutFor(agentInfo);
    this._idleTimer = null;
    this._proc = null;
    this._startupTimer = null;
    this._startupDeferred = null;
    this._activeTurn = null;
    this._turnQueue = Promise.resolve();
    this._requestCounter = 1;
    this._pendingCommandAcks = new Map();
    this._spawnModelKey = "";
    this._spawnThinkingKey = "";
    this._spawnCwdKey = "";
    this._stdoutInterface = null;
    this._stderrInterface = null;
    this._launcher = null;
    this._model = "";
    this._thinking = "";
    this._cwd = "";
    this._sessionStderr = "";
    this._healAttempted = false;
    this._lastError = null;
    this._terminalSink = null;
    this._terminalBuffer = [];
    this._terminalBufferChars = 0;
    this._terminalFlushChain = Promise.resolve();
  }

  attachTerminalSink(sink) {
    this._terminalSink = typeof sink === "function" ? sink : null;
    if (this._terminalSink && this._terminalBuffer.length > 0) {
      this._flushTerminalBuffer();
    }
  }

  detachTerminalSink() {
    this._terminalSink = null;
  }

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
    this._terminalFlushChain = this._terminalFlushChain.then(async () => {
      while (this._terminalSink && this._terminalBuffer.length > 0) {
        const frame = this._terminalBuffer.shift();
        this._terminalBufferChars -= frame.text.length;
        if (this._terminalBufferChars < 0) this._terminalBufferChars = 0;
        try {
          await this._terminalSink(frame.text, frame.status);
        } catch {
          // sink failure is best-effort; drop the frame to keep the queue moving
        }
      }
    });
  }

  __terminalBufferForTests() {
    return [...this._terminalBuffer];
  }

  _emitReadyBanner(event) {
    // Operator-visible "what just spun up" panel. Renders on every ready
    // transition (initial spawn + heal-respawn) so the operator sees the
    // current model/effort/session-id without having to inspect anything.
    // We pull model/effort from the PiSession's stored launch params (set
    // by ensureStarted via runtimeConfig) — agentInfo is the source of
    // truth at spawn time.
    const cfg = getRuntimeConfig(this.agentInfo);
    const model = String(this._model || this.agentInfo?.model || cfg.model || "").trim();
    const effort = String(this._thinking || cfg.thinking || cfg.effort || "").trim();
    const sessionId = String(event?.data?.sessionId || event?.sessionId || this.sessionId || "").trim();
    const lines = [];
    lines.push(colorize(ANSI.brightGreen + ANSI.bold, "● pi rpc ready"));
    const meta = [];
    if (model) meta.push(`${colorize(ANSI.dim, "model")} ${colorize(ANSI.cyan, model)}`);
    if (effort) meta.push(`${colorize(ANSI.dim, "effort")} ${colorize(ANSI.cyan, effort)}`);
    if (sessionId) meta.push(`${colorize(ANSI.dim, "session")} ${colorize(ANSI.cyan, sessionId)}`);
    if (meta.length) lines.push(`  ${meta.join("  ")}`);
    this._pushTerminalFrame(`${lines.join("\r\n")}\r\n`, "running");
  }

  get state() {
    return this._state;
  }

  get processAlive() {
    return Boolean(this._proc && !this._proc.killed && this._proc.exitCode === null);
  }

  _nextRequestId(prefix) {
    return `aify-${prefix}-${this._requestCounter++}`;
  }

  _emit(level, message) {
    const turn = this._activeTurn;
    if (turn?.callbacks?.onEvent) {
      try {
        turn.callbacks.onEvent(level, message);
      } catch {
        // swallow callback errors
      }
      return;
    }
    if (this._onPoolEvent) {
      try {
        this._onPoolEvent(level, message);
      } catch {
        // swallow callback errors
      }
    }
  }

  _send(payload) {
    if (!this._proc || !this._proc.stdin?.writable || this._proc.stdin.destroyed) return false;
    try {
      this._proc.stdin.write(`${JSON.stringify(payload)}\n`);
      return true;
    } catch {
      return false;
    }
  }

  _buildArgs() {
    const args = [...this._launcher.args, "--mode", "rpc"];
    if (this.sessionId) args.push("--resume", this.sessionId);
    if (this._model) args.push("--model", this._model);
    if (this._thinking) args.push("--thinking", this._thinking);
    return args;
  }

  _rejectAcks(scope, error) {
    for (const [id, pending] of [...this._pendingCommandAcks.entries()]) {
      if (scope !== "all" && pending.scope !== scope) continue;
      clearTimeout(pending.timer);
      this._pendingCommandAcks.delete(id);
      try {
        pending.reject(error);
      } catch {
        // swallow
      }
    }
  }

  _sendCommandWithAck(payload, { scope = "session", prefix = payload?.type || "command", timeoutMs = 30000 } = {}) {
    const id = this._nextRequestId(prefix);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pendingCommandAcks.delete(id);
        reject(new Error(`Pi ${String(payload?.type || "command")} acknowledgement timed out`));
      }, timeoutMs);
      this._pendingCommandAcks.set(id, {
        resolve,
        reject,
        timer,
        command: String(payload?.type || "command"),
        scope,
      });
      if (!this._send({ id, ...payload })) {
        clearTimeout(timer);
        this._pendingCommandAcks.delete(id);
        reject(new Error(`Pi ${String(payload?.type || "command")} could not be sent because the runtime stdin is closed`));
      }
    });
  }

  _runtimeStateSnapshot() {
    return {
      ...(this.sessionId ? { sessionId: this.sessionId } : {}),
      ...(this.sessionFile ? { sessionFile: this.sessionFile } : {}),
    };
  }

  _publishSessionState(event) {
    const next = extractPiSessionState(event);
    let changed = false;
    if (next.sessionId && next.sessionId !== this.sessionId) {
      this.sessionId = next.sessionId;
      changed = true;
    }
    if (next.sessionFile && next.sessionFile !== this.sessionFile) {
      this.sessionFile = next.sessionFile;
      changed = true;
    }
    if (changed || next.sessionId || next.sessionFile) {
      const turn = this._activeTurn;
      if (turn?.callbacks?.onRuntimeState) {
        try {
          turn.callbacks.onRuntimeState(this._runtimeStateSnapshot());
        } catch {
          // swallow
        }
      }
      const handle = this.sessionId || this.sessionFile;
      if (handle && turn?.callbacks?.onRefs) {
        try {
          turn.callbacks.onRefs({ threadId: handle });
        } catch {
          // swallow
        }
      }
    }
  }

  _spawnChild() {
    this._spawnCwdKey = this._cwd;
    this._spawnModelKey = this._model;
    this._spawnThinkingKey = this._thinking;
    const args = this._buildArgs();
    this._proc = spawnProcess(this._launcher.command, args, {
      cwd: this._cwd,
      env: { AIFY_BRIDGE_DISABLED: "1", AIFY_AGENT_ID: "" },
    });
    this._proc.stdin?.on?.("error", () => {});
    this._sessionStderr = "";

    const ownProc = this._proc;
    this._stdoutInterface = readline.createInterface({ input: ownProc.stdout });
    this._stdoutInterface.on("line", (line) => {
      if (this._proc !== ownProc) return;
      this._onStdoutLine(line);
    });
    this._stderrInterface = readline.createInterface({ input: ownProc.stderr });
    this._stderrInterface.on("line", (line) => {
      if (this._proc !== ownProc) return;
      this._onStderrLine(line);
    });

    ownProc.on("error", (error) => {
      if (this._proc !== ownProc) return;
      this._onChildError(error);
    });
    ownProc.on("close", (code, signal) => {
      // Only act on close for OUR child. A stopping session is evicted from
      // the pool and sets _proc=null synchronously, so a stale close on the
      // old proc finds _proc !== ownProc and returns. Synchronous teardown
      // paths (stop, _teardownChild) do their own state reset.
      if (this._proc !== ownProc) return;
      this._onChildExit(code, signal);
    });
  }

  _onStderrLine(line) {
    const text = quoteForDisplay(line);
    if (!text) return;
    this._sessionStderr = appendBounded(this._sessionStderr, `${text}\n`);
    const turn = this._activeTurn;
    if (turn) {
      turn.stderrText = appendBounded(turn.stderrText, `${text}\n`);
      this._emit("stderr", text);
    }
    const detected = detectPiRuntimeFailure(text);
    if (detected.authFailure) {
      this._failTurnAndChild(`Pi authentication failed fast: ${detected.message}`);
    } else if (detected.fatalRuntime) {
      this._failTurnAndChild(`Pi runtime crashed: ${detected.message}`);
    }
  }

  _onStdoutLine(rawLine) {
    const text = String(rawLine || "").trim();
    if (!text) return;
    let event;
    try {
      event = JSON.parse(text);
    } catch {
      const turn = this._activeTurn;
      if (turn) {
        turn.finalText = appendBounded(turn.finalText, `${text}\n`, {
          limit: MAX_PI_ASSISTANT_CAPTURE_CHARS,
          preserveEdges: true,
        });
      }
      const detected = detectPiRuntimeFailure(text);
      if (detected.authFailure) {
        this._failTurnAndChild(`Pi authentication failed fast: ${detected.message}`);
      }
      return;
    }

    this._publishSessionState(event);

    if (event.type === "ready") {
      this._emitReadyBanner(event);
      this._onReady();
      return;
    }

    if (event.type === "response") {
      this._onResponseEvent(event);
      return;
    }

    const synthesizedFrame = formatPiEventAsTerminalFrame(event);
    if (synthesizedFrame) this._pushTerminalFrame(synthesizedFrame);

    const turn = this._activeTurn;

    if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
      if (turn) {
        turn.finalText = appendBounded(turn.finalText, String(event.assistantMessageEvent.delta || ""), {
          limit: MAX_PI_ASSISTANT_CAPTURE_CHARS,
          preserveEdges: true,
        });
      }
      return;
    }

    if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_end") {
      if (turn) {
        turn.finalSnapshotText = boundText(
          String(event.assistantMessageEvent.content || turn.finalSnapshotText || ""),
          MAX_PI_ASSISTANT_CAPTURE_CHARS,
          { preserveEdges: true },
        );
      }
      return;
    }

    if (event.type === "message_end" || event.type === "turn_end") {
      const messageText = extractPiAssistantText(event.message);
      if (turn && messageText) {
        turn.finalSnapshotText = boundText(messageText, MAX_PI_ASSISTANT_CAPTURE_CHARS, { preserveEdges: true });
      }
      return;
    }

    if (event.type === "agent_start") {
      this._emit("pi", "Started Pi agent turn");
      return;
    }

    if (event.type === "agent_end") {
      this._onAgentEnd(event);
      return;
    }

    if (event.type === "error") {
      const message = String(event.error || event.message || "Pi runtime error");
      if (turn) turn.finalError = appendBounded("", message);
      const detected = detectPiRuntimeFailure(message);
      if (detected.authFailure) {
        this._failTurnAndChild(`Pi authentication failed fast: ${detected.message}`);
      } else if (detected.fatalRuntime) {
        this._failTurnAndChild(`Pi runtime crashed: ${detected.message}`);
      }
    }
  }

  _onResponseEvent(event) {
    const pending = this._pendingCommandAcks.get(event.id);
    if (pending) {
      this._pendingCommandAcks.delete(event.id);
      clearTimeout(pending.timer);
      if (event.success === false) {
        pending.reject(new Error(String(event.error || `Pi ${pending.command} failed`)));
      } else {
        this._publishSessionState(event);
        pending.resolve(event);
      }
      return;
    }
    const turn = this._activeTurn;
    if (event.command === "prompt" && turn) {
      turn.promptAcked = event.success !== false;
      if (event.success === false) {
        turn.finalError = appendBounded("", String(event.error || "Pi prompt failed"));
        const detected = detectPiRuntimeFailure(turn.finalError);
        if (detected.authFailure) {
          this._failTurnAndChild(`Pi authentication failed fast: ${detected.message}`);
        } else if (detected.fatalRuntime) {
          this._failTurnAndChild(`Pi runtime crashed: ${detected.message}`);
        }
      }
    }
  }

  _onReady() {
    if (this._startupTimer) {
      clearTimeout(this._startupTimer);
      this._startupTimer = null;
    }
    const startup = this._startupDeferred;
    this._state = "ready";
    // Query canonical session state once on every ready transition (initial
    // + after heal-respawn). Skip on per-turn reuse — we'd already have it.
    this._sendCommandWithAck({ type: "get_state" }, { scope: "session", prefix: "get-state", timeoutMs: 2500 })
      .then((stateEvent) => this._publishSessionState(stateEvent))
      .catch((error) => this._emit("pi", `Pi get_state unavailable: ${quoteForDisplay(error?.message || error)}`))
      .finally(() => {
        if (startup === this._startupDeferred && startup) {
          this._startupDeferred = null;
          startup.resolve();
        }
      });
  }

  _onAgentEnd(event) {
    const turn = this._activeTurn;
    if (!turn) return;
    const text = extractPiAssistantText(event.messages);
    if (text) {
      turn.finalSnapshotText = boundText(text, MAX_PI_ASSISTANT_CAPTURE_CHARS, { preserveEdges: true });
    }
    this._rejectAcks("turn", new Error("Pi turn ended before command acknowledgement"));
    if (turn.attemptTimer) {
      clearTimeout(turn.attemptTimer);
      turn.attemptTimer = null;
    }
    if (turn.callbacks?.onRuntimeState) {
      try {
        turn.callbacks.onRuntimeState(this._runtimeStateSnapshot());
      } catch {
        // swallow
      }
    }
    const handle = this.sessionId || this.sessionFile;
    if (handle && turn.callbacks?.onRefs) {
      try {
        turn.callbacks.onRefs({ threadId: handle });
      } catch {
        // swallow
      }
    }
    const resolvedText = (turn.finalText.trim() || turn.finalSnapshotText.trim()) || "(no output)";
    this._activeTurn = null;
    this._state = this.processAlive ? "ready" : "dead";
    this._startIdleTimer();
    turn.resolve({
      status: turn.interrupted ? "cancelled" : "completed",
      summary: resolvedText,
      runtimeState: this._runtimeStateSnapshot(),
      externalRefs: { threadId: handle, turnId: String(event.id || "") },
    });
  }

  _failTurnAndChild(message) {
    const error = new Error(message);
    this._lastError = error;
    const turn = this._activeTurn;
    if (turn) {
      if (turn.attemptTimer) {
        clearTimeout(turn.attemptTimer);
        turn.attemptTimer = null;
      }
      this._rejectAcks("turn", error);
      this._activeTurn = null;
      this._state = "dead";
      try {
        turn.reject(error);
      } catch {
        // swallow
      }
    }
    this._teardownChild(error);
  }

  _teardownChild(error) {
    const header = colorize(ANSI.dim + ANSI.red, "○ pi rpc exited");
    if (error) {
      const msg = String(error?.message || error || "").trim();
      this._pushTerminalFrame(`\r\n${header}${msg ? ` ${colorize(ANSI.dim, msg)}` : ""}\r\n`, "stopped");
    } else {
      this._pushTerminalFrame(`\r\n${header}\r\n`, "stopped");
    }
    this._clearIdleTimer();
    if (this._startupTimer) {
      clearTimeout(this._startupTimer);
      this._startupTimer = null;
    }
    if (this._startupDeferred) {
      const deferred = this._startupDeferred;
      this._startupDeferred = null;
      try {
        deferred.reject(error || new Error("Pi child exited before ready"));
      } catch {
        // swallow
      }
    }
    this._rejectAcks("all", error || new Error("Pi runtime tearing down"));
    if (this._proc) {
      try {
        terminateProcessTree(this._proc);
      } catch {
        // swallow
      }
    }
    this._proc = null;
    this._state = "dead";
    if (piSessionPool.get(this.agentId) === this) {
      piSessionPool.delete(this.agentId);
    }
  }

  _onChildError(error) {
    if (error && error.code === "ENOENT") {
      const piTarget = String(process.env.AIFY_PI_COMMAND || process.env.PI_COMMAND || "omp").trim();
      const enriched = new Error(
        `spawn "${this._launcher.command}" ENOENT — this bridge resolved Oh My Pi to "${this._launcher.command}" ` +
          `but Node could not execute it. Common causes: missing exec bit, broken shebang interpreter ` +
          `(e.g., the script's #!/usr/bin/env node points at a node that isn't on the bridge's PATH), ` +
          `or a stale symlink. Also verify the runtime cwd exists: "${this._cwd}". ` +
          `Fix: set AIFY_PI_COMMAND to an absolute path to a real "omp" binary and ` +
          `restart aify-comms. Diagnostic: ${diagnosticsFor(piTarget)}`,
      );
      enriched.code = error.code;
      enriched.originalError = error.message;
      this._failTurnAndChild(enriched.message);
      return;
    }
    this._failTurnAndChild(String(error?.message || error || "Pi child error"));
  }

  _onChildExit(code) {
    const turn = this._activeTurn;
    if (turn) {
      if (this._maybeHealMissingSessionForTurn(turn)) return;
      if (turn.attemptTimer) {
        clearTimeout(turn.attemptTimer);
        turn.attemptTimer = null;
      }
      this._rejectAcks(
        "turn",
        new Error(
          turn.finalError ||
            turn.finalText.trim() ||
            turn.stderrText.trim() ||
            `Pi exited with code ${code}`,
        ),
      );
      if (turn.interrupted) {
        this._activeTurn = null;
        turn.resolve({
          status: "cancelled",
          summary: (turn.finalText.trim() || turn.finalSnapshotText.trim()) || turn.finalError || "Run interrupted",
          runtimeState: this._runtimeStateSnapshot(),
          externalRefs: { threadId: this.sessionId || this.sessionFile },
        });
      } else if (code === 0 && turn.promptAcked && !turn.finalError) {
        this._activeTurn = null;
        turn.resolve({
          status: "completed",
          summary: (turn.finalText.trim() || turn.finalSnapshotText.trim()) || "(no output)",
          runtimeState: this._runtimeStateSnapshot(),
          externalRefs: { threadId: this.sessionId || this.sessionFile },
        });
      } else {
        const failureText = [turn.finalError, this._sessionStderr, turn.stderrText].filter(Boolean).join("\n").trim();
        const detected = detectPiRuntimeFailure(failureText);
        let err;
        if (detected.authFailure) {
          err = new Error(`Pi authentication failed fast: ${detected.message}`);
        } else if (detected.fatalRuntime) {
          err = new Error(`Pi runtime crashed: ${detected.message}`);
        } else if (detected.missingSession && turn.executionMode === "resident") {
          err = new Error(
            `Resident Pi session "${this.sessionId}" is not resumable: ${detected.message}. Clear the saved session handle or start a fresh managed Pi session.`,
          );
        } else {
          err = new Error(
            turn.finalError ||
              turn.finalText.trim() ||
              turn.stderrText.trim() ||
              `Pi exited with code ${code}`,
          );
        }
        this._activeTurn = null;
        turn.reject(err);
      }
    }
    let exitError = this._lastError;
    if (!exitError) {
      const detected = detectPiRuntimeFailure(this._sessionStderr);
      if (detected.authFailure) {
        exitError = new Error(`Pi authentication failed fast: ${detected.message}`);
        exitError.detected = detected;
      } else if (detected.fatalRuntime) {
        exitError = new Error(`Pi runtime crashed: ${detected.message}`);
        exitError.detected = detected;
      } else if (detected.shouldHeal) {
        exitError = new Error(detected.message);
        exitError.detected = detected;
      } else {
        exitError = new Error(this._sessionStderr.trim() || `Pi child exited with code ${code}`);
      }
    }
    this._teardownChild(exitError);
  }

  _maybeHealMissingSessionForTurn(turn) {
    const failureText = [turn.finalError, this._sessionStderr, turn.stderrText].filter(Boolean).join("\n").trim();
    const detected = detectPiRuntimeFailure(failureText);
    if (!detected.shouldHeal || !this.sessionId || this._healAttempted || turn.executionMode === "resident") {
      return false;
    }
    const previous = this.sessionId;
    this._healAttempted = true;
    this._emit("thread", `Pi session "${previous}" is not resumable (${detected.message}); starting fresh.`);
    turn.finalText = "";
    turn.finalSnapshotText = "";
    turn.finalError = "";
    turn.stderrText = "";
    turn.promptAcked = false;
    turn.initialPromptSent = false;
    this.sessionId = "";
    this.sessionFile = "";
    this._sessionStderr = "";
    if (turn.callbacks?.onRuntimeState) {
      try {
        turn.callbacks.onRuntimeState({});
      } catch {
        // swallow
      }
    }
    if (turn.callbacks?.onSessionHandleChange) {
      try {
        turn.callbacks.onSessionHandleChange("", { reason: detected.healReason, previous });
      } catch {
        // swallow
      }
    }
    this._proc = null;
    this._state = "starting";
    this._pendingCommandAcks.clear();
    this._startupDeferred = createDeferred();
    this._spawnChild();
    this._armStartupTimer();
    this._startupDeferred.promise
      .then(() => this._sendTurnPrompt(turn))
      .catch((err) => {
        if (this._activeTurn === turn) {
          this._activeTurn = null;
          turn.reject(err);
        }
      });
    return true;
  }

  _startIdleTimer() {
    this._clearIdleTimer();
    if (!Number.isFinite(this._idleTimeoutMs) || this._idleTimeoutMs <= 0) return;
    this._idleTimer = setTimeout(() => {
      if (this._activeTurn || !this.processAlive) return;
      this._emit("pi", `Pi RPC idle for ${this._idleTimeoutMs}ms; releasing child.`);
      this.stop("idle").catch(() => {});
    }, this._idleTimeoutMs);
    if (typeof this._idleTimer.unref === "function") this._idleTimer.unref();
  }

  _clearIdleTimer() {
    if (this._idleTimer) clearTimeout(this._idleTimer);
    this._idleTimer = null;
  }

  _armStartupTimer() {
    const startupTimeoutMs = startupTimeoutFor(this.agentInfo);
    this._startupTimer = setTimeout(() => {
      if (this._state !== "starting") return;
      const detected = detectPiRuntimeFailure(this._sessionStderr);
      const startup = this._startupDeferred;
      this._startupDeferred = null;
      this._startupTimer = null;
      let err;
      if (detected.authFailure) {
        err = new Error(`Pi authentication failed fast: ${detected.message}`);
      } else {
        err = new Error(
          `Pi did not become ready within ${startupTimeoutMs}ms. Check Oh My Pi authentication/provider configuration and run "omp" manually in this environment.`,
        );
      }
      if (startup) {
        try {
          startup.reject(err);
        } catch {
          // swallow
        }
      }
      this._teardownChild(err);
    }, Math.max(250, startupTimeoutMs));
    if (typeof this._startupTimer.unref === "function") this._startupTimer.unref();
  }

  async ensureStarted({ launcher, cwd, model = "", thinking = "", sessionId, agentInfo } = {}) {
    if (launcher) this._launcher = launcher;
    if (cwd) this._cwd = cwd;
    if (!this._cwd) this._cwd = process.cwd();
    this._model = String(model || "").trim();
    this._thinking = String(thinking || "").trim();
    if (agentInfo) {
      this.agentInfo = agentInfo;
      this._idleTimeoutMs = idleTimeoutFor(agentInfo);
    }

    if (this.processAlive) {
      const modelChanged = this._model !== this._spawnModelKey;
      const thinkingChanged = this._thinking !== this._spawnThinkingKey;
      const cwdChanged = this._cwd !== this._spawnCwdKey;
      const requested = String(sessionId || "").trim();
      const sessionMismatch = requested && requested !== this.sessionId;
      if (modelChanged || thinkingChanged || cwdChanged || sessionMismatch) {
        await this.stop("respawn-params-changed");
      }
    }

    if (!this.processAlive) {
      this._clearIdleTimer();
      this._activeTurn = null;
      this._pendingCommandAcks.clear();
      this._healAttempted = false;
      this._sessionStderr = "";
      this._lastError = null;
      if (sessionId !== undefined) {
        this.sessionId = String(sessionId || "").trim();
      }
      this._state = "starting";
      this._startupDeferred = createDeferred();
      this._spawnChild();
      this._armStartupTimer();
      try {
        await this._startupDeferred.promise;
      } finally {
        this._startupDeferred = null;
        if (this._startupTimer) {
          clearTimeout(this._startupTimer);
          this._startupTimer = null;
        }
      }
    }
  }

  _sendTurnPrompt(turn) {
    if (turn.initialPromptSent) return;
    turn.initialPromptSent = true;
    const userPrompt = buildUserPrompt(turn.run);
    const echo = String(userPrompt || "").replace(/\r?\n$/, "");
    if (echo) {
      const prefixed = echo
        .split(/\r?\n/)
        .map((line) => `${colorize(ANSI.brightGreen, ">")} ${line}`)
        .join("\r\n");
      this._pushTerminalFrame(`\r\n${prefixed}\r\n`);
    }
    this._send({
      id: this._nextRequestId("prompt"),
      type: "prompt",
      message: `${buildSystemPrompt(this.agentId, this.agentInfo, turn.run)}\n\n${buildUserPrompt(turn.run)}`,
    });
  }

  runTurn(run, callbacks) {
    const executionMode = String(run.executionMode || this.agentInfo.sessionMode || "managed").trim().toLowerCase();
    const timeoutMs = timeoutFor(this.agentInfo);
    const turn = {
      run,
      callbacks: callbacks || {},
      executionMode,
      promptAcked: false,
      finalText: "",
      finalSnapshotText: "",
      finalError: "",
      stderrText: "",
      interrupted: false,
      initialPromptSent: false,
      attemptTimer: null,
      resolve: null,
      reject: null,
    };
    const promise = new Promise((resolve, reject) => {
      turn.resolve = resolve;
      turn.reject = reject;
    });

    this._turnQueue = this._turnQueue.then(() =>
      this._runTurnImpl(turn, timeoutMs).catch(() => {
        // turn.reject already invoked inside _runTurnImpl on failure
      }),
    );

    return {
      promise,
      interrupt: async () => this._interruptTurn(turn),
      steer: async (text) => this._steerTurn(turn, text),
    };
  }

  async _runTurnImpl(turn, timeoutMs) {
    if (!this.processAlive) {
      try {
        await this.ensureStarted({
          launcher: this._launcher,
          cwd: this._cwd,
          model: this._model,
          thinking: this._thinking,
          sessionId: this.sessionId,
          agentInfo: this.agentInfo,
        });
      } catch (error) {
        try {
          turn.reject(error);
        } catch {
          // swallow
        }
        return;
      }
    }
    this._clearIdleTimer();
    this._activeTurn = turn;
    this._state = "busy";
    turn.attemptTimer = setTimeout(() => {
      if (this._activeTurn !== turn) return;
      turn.interrupted = true;
      try {
        this._send({ id: this._nextRequestId("abort"), type: "abort" });
      } catch {
        // swallow
      }
      try {
        terminateProcessTree(this._proc);
      } catch {
        // swallow
      }
      const err = new Error(`Pi run timed out after ${timeoutMs}ms`);
      this._failTurnAndChild(err.message);
    }, timeoutMs);
    if (typeof turn.attemptTimer.unref === "function") turn.attemptTimer.unref();
    this._sendTurnPrompt(turn);
  }

  async _interruptTurn(turn) {
    if (this._activeTurn !== turn) return;
    turn.interrupted = true;
    this._pushTerminalFrame(`\r\n${colorize(ANSI.brightYellow + ANSI.bold, "⏸ interrupt requested")}\r\n`);
    try {
      this._send({ id: this._nextRequestId("abort"), type: "abort" });
    } catch {
      // swallow
    }
    const grace = setTimeout(() => {
      if (this._activeTurn === turn) {
        try {
          terminateProcessTree(this._proc);
        } catch {
          // swallow
        }
      }
    }, INTERRUPT_GRACE_MS);
    if (typeof grace.unref === "function") grace.unref();
  }

  async _steerTurn(turn, text) {
    const message = String(text || "");
    if (!message.trim()) {
      throw new Error("Steer body is required");
    }
    if (this._activeTurn !== turn || !this.processAlive) {
      throw new Error("No active Pi turn to steer");
    }
    const echo = message.replace(/\r?\n$/, "");
    const prefixed = echo
      .split(/\r?\n/)
      .map((line) => `${colorize(ANSI.brightYellow + ANSI.bold, ">> steer:")} ${line}`)
      .join("\r\n");
    this._pushTerminalFrame(`\r\n${prefixed}\r\n`);
    await this._sendCommandWithAck({ type: "steer", message }, { scope: "turn", prefix: "steer" });
    this._emit("steer", "Steer sent to active Pi RPC run");
  }

  async stop(reason = "stop") {
    this._clearIdleTimer();
    const proc = this._proc;
    // Evict from the pool FIRST, synchronously. A concurrent acquire on the
    // same agentId would otherwise retrieve this still-dying session and
    // reuse the just-killed child (whose exitCode/killed flags lag the
    // signal), then dispatch a turn that gets rejected when the kill
    // actually lands. The pool's only purpose is reuse, and a stopping
    // session is no longer reusable.
    if (piSessionPool.get(this.agentId) === this) {
      piSessionPool.delete(this.agentId);
    }
    this._proc = null;
    this._state = "dead";
    if (proc) {
      try {
        terminateProcessTree(proc);
      } catch {
        // swallow
      }
      await new Promise((resolve) => {
        const t = setTimeout(resolve, 50);
        if (typeof t.unref === "function") t.unref();
        try {
          proc.once("close", resolve);
        } catch {
          // swallow
        }
      });
    }
    this._rejectAcks("all", new Error(`Pi runtime stopped (${reason})`));
  }
}

function resolvePiLauncher() {
  const availability = runtimeLaunchAvailability("pi");
  if (!availability.available) throw new Error(availability.message);
  return defaultPiCommand();
}

export async function acquirePiSession({ agentId, agentInfo, sessionId = "", cwd, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("acquirePiSession requires an agentId");
  let session = piSessionPool.get(key);
  if (!session) {
    session = new PiSession({ agentId: key, agentInfo, sessionId, onPoolEvent });
    piSessionPool.set(key, session);
  } else {
    if (agentInfo) session.agentInfo = agentInfo;
    if (onPoolEvent) session._onPoolEvent = onPoolEvent;
  }
  const launcher = resolvePiLauncher();
  const config = getRuntimeConfig(agentInfo);
  const model = normalizePiModelOverride(agentInfo?.model || config.model || "");
  const thinking = String(config.thinking || config.effort || "").trim();
  await session.ensureStarted({
    launcher,
    cwd: cwd || agentInfo?.cwd || process.cwd(),
    model,
    thinking,
    sessionId,
    agentInfo,
  });
  return session;
}

export function getPiSession(agentId) {
  const key = String(agentId || "").trim();
  if (!key) return null;
  return piSessionPool.get(key) || null;
}

export async function shutdownAllPiSessions(reason = "shutdown") {
  const sessions = [...piSessionPool.values()];
  piSessionPool.clear();
  await Promise.all(sessions.map((s) => s.stop(reason).catch(() => {})));
}

export function __resetPiSessionPoolForTests() {
  for (const session of piSessionPool.values()) {
    try {
      if (session._proc) terminateProcessTree(session._proc);
    } catch {
      // swallow
    }
    session._proc = null;
    session._state = "dead";
    if (session._idleTimer) clearTimeout(session._idleTimer);
    if (session._startupTimer) clearTimeout(session._startupTimer);
    session._idleTimer = null;
    session._startupTimer = null;
    session._activeTurn = null;
    session._pendingCommandAcks.clear();
  }
  piSessionPool.clear();
}

export function __piSessionPoolSize() {
  return piSessionPool.size;
}

export function __piSessionPoolEntriesForTests() {
  return [...piSessionPool.values()];
}
