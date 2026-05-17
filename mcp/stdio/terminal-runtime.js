import { spawn } from "child_process";
import { createRequire } from "module";
import { normalizeRuntime, terminateProcessTree } from "./runtimes.js";

const require = createRequire(import.meta.url);
let pty = null;
try {
  const loaded = require("node-pty");
  pty = loaded?.default || loaded;
} catch {
  pty = null;
}

export function bridgeTerminalSupported() {
  if (["0", "false", "no"].includes(String(process.env.AIFY_TERMINAL_BRIDGE || "1").toLowerCase())) return false;
  return !!pty;
}

function appendTail(current = "", chunk = "", limit = 8192) {
  const next = `${current || ""}${chunk || ""}`;
  return next.length > limit ? next.slice(-limit) : next;
}

export function classifyTerminalRuntimeOutput(runtime = "", text = "") {
  const key = normalizeRuntime(runtime);
  const raw = String(text || "");
  const compact = raw.replace(/\s+/g, " ").trim();
  const lower = compact.toLowerCase();
  if (!lower) return null;
  if (key === "pi") {
    if (
      /no api key/.test(lower) ||
      /api key (?:not found|missing|required)/.test(lower) ||
      /not authenticated|authentication (?:failed|required)|unauthori[sz]ed|\b401\b/.test(lower) ||
      /amazon-bedrock|bedrock/.test(lower) && /login|auth|credential|api key/.test(lower)
    ) {
      return {
        kind: "auth",
        status: "failed",
        message: `Pi authentication failed fast: ${compact || "missing or expired provider credentials"}`,
      };
    }
    const missingSession = compact.match(/session\s+["']?([^"'\s]+)["']?\s+(?:not found|does not exist|missing)/i);
    if (missingSession || /session .*not found|session .*does not exist|no such session/i.test(compact)) {
      return {
        kind: "missing_session",
        status: "failed",
        sessionHandle: missingSession?.[1] || "",
        message: `Pi saved session handle is not resumable: ${compact}`,
      };
    }
  }
  if (key === "hermes") {
    const missingSession = compact.match(/session\s+["']?([^"'\s]+)["']?\s+(?:not found|does not exist|missing)/i);
    if (missingSession || /session .*not found|session .*does not exist|no such session/i.test(compact)) {
      return {
        kind: "missing_session",
        status: "failed",
        sessionHandle: missingSession?.[1] || "",
        message: `Hermes saved session handle is not resumable: ${compact}`,
      };
    }
  }
  return null;
}

export function terminalCommandWithoutResume(runtime = "", command = "") {
  const key = normalizeRuntime(runtime);
  let text = String(command || "").trim();
  if (!text) return text;
  const token = String.raw`(?:"[^"]*"|'[^']*'|\S+)`;
  if (key === "pi" || key === "hermes" || key === "claude-code") {
    text = text.replace(new RegExp(String.raw`(^|\s)(?:--resume|--session-id|-r)(?:=|\s+)${token}`, "g"), "$1");
  }
  if (key === "codex") {
    text = text.replace(new RegExp(String.raw`(^|\s)resume(?:\s+--include-non-interactive)?\s+${token}`, "g"), "$1");
  }
  return text.replace(/\s+/g, " ").trim();
}

function terminalEnvWithoutResume(runtime = "", env = {}) {
  const key = normalizeRuntime(runtime);
  const next = { ...(env || {}) };
  delete next.AIFY_SESSION_HANDLE;
  if (key === "pi") {
    delete next.PI_SESSION_ID;
    delete next.OMP_SESSION_ID;
    delete next.AIFY_PI_SESSION_ID;
  } else if (key === "hermes") {
    delete next.HERMES_SESSION_ID;
    delete next.HERMES_SESSION;
  } else if (key === "claude-code") {
    delete next.CLAUDE_SESSION_ID;
  } else if (key === "codex") {
    delete next.CODEX_THREAD_ID;
  }
  return next;
}

export class TerminalProcessManager {
  constructor({ onOutput = async () => {}, onExit = async () => {}, onHeal = async () => {} } = {}) {
    this.onOutput = onOutput;
    this.onExit = onExit;
    this.onHeal = onHeal;
    this.terminals = new Map();
  }

  has(id) {
    return this.terminals.has(id);
  }

  async start({ id, command, cwd = process.cwd(), env = process.env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "" }) {
    if (!id) throw new Error("Terminal id is required");
    if (!command) throw new Error("Terminal command is required");
    if (this.terminals.has(id)) {
      await this.stop(id, "restarting terminal");
    }
    const spec = { id, command, cwd, env, cols, rows, runtime: normalizeRuntime(runtime), sessionHandle, healAttempted, agentId };
    if (pty) {
      return this.startPty(spec);
    }
    return this.startPipeProcess(spec);
  }

  async startPty({ id, command, cwd, env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "" }) {
    const windows = process.platform === "win32";
    const shell = windows
      ? (process.env.COMSPEC || "cmd.exe")
      : (process.env.SHELL || "bash");
    const trimmedCommand = String(command || "").trim();
    const lowerCommand = trimmedCommand.toLowerCase();
    const shellName = shell.split(/[\\/]/).pop().toLowerCase();
    const args = windows
      ? (lowerCommand === "cmd" || lowerCommand === "cmd.exe" || lowerCommand === shellName ? [] : ["/d", "/s", "/c", trimmedCommand])
      : ["-lc", command];
    const term = pty.spawn(shell, args, {
      name: "xterm-256color",
      cols: Math.max(20, Number(cols || 100)),
      rows: Math.max(6, Number(rows || 28)),
      cwd: cwd || process.cwd(),
      env,
    });
    let resolveExit = null;
    const exitPromise = new Promise((resolve) => {
      resolveExit = resolve;
    });
    const state = {
      id,
      command,
      cwd,
      env,
      cols: Math.max(20, Number(cols || 100)),
      rows: Math.max(6, Number(rows || 28)),
      runtime: normalizeRuntime(runtime),
      sessionHandle: String(sessionHandle || "").trim(),
      healAttempted: !!healAttempted,
      agentId: String(agentId || "").trim(),
      term,
      status: "attached",
      kind: "pty",
      outputTail: "",
      classification: null,
      exitPromise,
      resolveExit,
    };
    this.terminals.set(id, state);
    term.onData((text) => {
      this._handleOutput(id, state, text).catch(() => {});
    });
    term.onExit(({ exitCode, signal }) => {
      this._handleExit(id, state, { code: exitCode, signal }).catch(() => {});
    });
    return { pid: term.pid, status: "attached", pty: true };
  }

  async startPipeProcess({ id, command, cwd, env, cols = 100, rows = 28, runtime = "", sessionHandle = "", healAttempted = false, agentId = "" }) {
    const proc = spawn(command, {
      cwd: cwd || process.cwd(),
      env,
      shell: true,
      windowsHide: false,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const state = {
      id,
      command,
      cwd,
      env,
      cols,
      rows,
      runtime: normalizeRuntime(runtime),
      sessionHandle: String(sessionHandle || "").trim(),
      healAttempted: !!healAttempted,
      agentId: String(agentId || "").trim(),
      proc,
      status: "attached",
      kind: "pipe",
      outputTail: "",
      classification: null,
    };
    this.terminals.set(id, state);
    const emit = (chunk) => {
      const text = chunk?.toString?.("utf8") || String(chunk || "");
      this._handleOutput(id, state, text).catch(() => {});
    };
    proc.stdout?.on("data", emit);
    proc.stderr?.on("data", emit);
    proc.on("exit", (code, signal) => {
      this._handleExit(id, state, { code, signal }).catch(() => {});
    });
    proc.on("error", (error) => {
      this._handleExit(id, state, { error }).catch(() => {});
    });
    return { pid: proc.pid, status: "attached", pty: false };
  }

  async _handleOutput(id, state, text) {
    if (!text) return;
    state.outputTail = appendTail(state.outputTail, text);
    await this.onOutput(id, text);
    const classification = classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    if (classification?.kind === "auth" && !state.classification) {
      state.classification = classification;
      await this.onOutput(id, `\n[aify-comms] ${classification.message}\n`);
      if (state.kind === "pty") state.term?.kill();
      else terminateProcessTree(state.proc, "SIGTERM");
    }
  }

  async _handleExit(id, state, detail = {}) {
    if (this.terminals.get(id) === state) this.terminals.delete(id);
    state.resolveExit?.(detail);
    const classification = state.classification || classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    if (
      classification?.kind === "missing_session" &&
      state.sessionHandle &&
      !state.healAttempted
    ) {
      const freshCommand = terminalCommandWithoutResume(state.runtime, state.command);
      if (freshCommand && freshCommand !== state.command) {
        await this.onHeal(id, {
          runtime: state.runtime,
          agentId: state.agentId,
          previousSessionHandle: state.sessionHandle,
          reason: classification.kind,
          message: classification.message,
        });
        await this.onOutput(
          id,
          `\n[aify-comms] ${classification.message}; starting a fresh ${state.runtime || "runtime"} session without --resume.\n`,
        );
        await this.start({
          id,
          command: freshCommand,
          cwd: state.cwd,
          env: terminalEnvWithoutResume(state.runtime, state.env),
          cols: state.cols,
          rows: state.rows,
          runtime: state.runtime,
          sessionHandle: "",
          healAttempted: true,
          agentId: state.agentId,
        });
        return;
      }
    }
    const nextDetail = { ...detail };
    if (classification) {
      nextDetail.classification = classification;
      if (classification.status === "failed" && !nextDetail.error) nextDetail.error = new Error(classification.message);
    }
    await this.onExit(id, nextDetail);
  }

  input(id, body = "") {
    const terminal = this.terminals.get(id);
    if (!terminal) throw new Error(`Terminal "${id}" is not running`);
    if (terminal.kind === "pty") {
      terminal.term.write(String(body || ""));
      return;
    }
    terminal.proc?.stdin?.write(String(body || ""));
  }

  resize(id, cols = 0, rows = 0) {
    const terminal = this.terminals.get(id);
    if (!terminal) throw new Error(`Terminal "${id}" is not running`);
    if (terminal.kind === "pty") {
      terminal.term.resize(Math.max(20, Number(cols || 100)), Math.max(6, Number(rows || 28)));
    }
    return { status: "attached" };
  }

  async stop(id, reason = "terminal stop requested") {
    const terminal = this.terminals.get(id);
    if (!terminal) return { stopped: false };
    this.terminals.delete(id);
    if (terminal.kind === "pty") {
      terminal.term.kill();
      await Promise.race([
        terminal.exitPromise,
        new Promise((resolve) => setTimeout(resolve, 1000)),
      ]);
      return { stopped: true };
    }
    try {
      terminal.proc.stdin?.end();
    } catch {
      // Best effort.
    }
    terminateProcessTree(terminal.proc, "SIGTERM");
    return { stopped: true };
  }

  async stopAll(reason = "terminal manager shutdown") {
    const ids = Array.from(this.terminals.keys());
    for (const id of ids) {
      await this.stop(id, reason);
    }
  }
}
