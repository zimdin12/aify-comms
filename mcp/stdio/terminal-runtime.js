import { spawn } from "child_process";
import { createRequire } from "module";
import { homedir } from "node:os";
import { normalizeRuntime, runtimeCommandWithoutResume, sessionEnvVarsForRuntime, terminateProcessTree } from "./runtimes.js";

// node-pty's pty.spawn calls native chdir(2) with the cwd verbatim. POSIX
// chdir does not expand "~" — operator-supplied workspaces like
// "~/projects/foo" therefore fail immediately with ENOENT and the terminal
// dies seconds after attaching. Expand here so any caller that hands us a
// shell-style path gets the right directory. Exported for unit testing.
export function expandUserHome(value) {
  const raw = String(value || "");
  if (!raw) return raw;
  if (raw === "~") return homedir();
  if (raw.startsWith("~/")) return `${homedir()}${raw.slice(1)}`;
  return raw;
}

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

function compactTerminalText(text = "") {
  return String(text || "")
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, " ")
    .replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function hermesResumeStillPending(text = "") {
  const lower = compactTerminalText(text).toLowerCase();
  if (!lower) return false;
  const resumeIdx = lower.lastIndexOf("resuming");
  if (resumeIdx < 0) return false;
  const readyIdx = lower.lastIndexOf("ready");
  return readyIdx < resumeIdx;
}

function hermesResumeStallHealMs() {
  const raw = Number(process.env.AIFY_HERMES_RESUME_STALL_HEAL_MS || "");
  if (Number.isFinite(raw) && raw > 0) return Math.max(25, raw);
  return 30000;
}

export function classifyTerminalRuntimeOutput(runtime = "", text = "") {
  const key = normalizeRuntime(runtime);
  const raw = String(text || "");
  const compact = compactTerminalText(raw);
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
  return runtimeCommandWithoutResume(runtime, command);
}

function terminalEnvWithoutResume(runtime = "", env = {}) {
  const next = { ...(env || {}) };
  delete next.AIFY_SESSION_HANDLE;
  for (const name of sessionEnvVarsForRuntime(runtime)) {
    delete next[name];
  }
  return next;
}

async function waitForExitOrTimeout(exitPromise, timeoutMs = 1000) {
  let timer = null;
  let timedOut = false;
  try {
    return await Promise.race([
      Promise.resolve(exitPromise).then(() => true),
      new Promise((resolve) => {
        timer = setTimeout(() => {
          timedOut = true;
          resolve(false);
        }, Math.max(1, Number(timeoutMs) || 1000));
      }),
    ]);
  } finally {
    if (!timedOut && timer) clearTimeout(timer);
  }
}


export class TerminalProcessManager {
  constructor({
    onOutput = async () => {},
    onExit = async () => {},
    onHeal = async () => {},
    idleFlushMs = 16,
    maxLatencyMs = 33,
    maxBatchChars = 16 * 1024,
  } = {}) {
    this.onOutput = onOutput;
    this.onExit = onExit;
    this.onHeal = onHeal;
    this.idleFlushMs = Math.max(1, Number(idleFlushMs) || 16);
    this.maxLatencyMs = Math.max(this.idleFlushMs, Number(maxLatencyMs) || 33);
    this.maxBatchChars = Math.max(1024, Number(maxBatchChars) || 16 * 1024);
    this.terminals = new Map();
    this.outputStates = new Map();
  }

  has(id) {
    return this.terminals.has(id);
  }

  stateFor(id) {
    const state = this.terminals.get(id);
    if (!state) return null;
    return {
      id: state.id,
      runtime: state.runtime,
      status: state.status,
      command: state.command,
      outputTail: state.outputTail || "",
    };
  }

  emitOutputForTest(id, text) {
    return this._handleOutput(id, { runtime: "" }, text);
  }

  flushOutputForTest(id) {
    return this._flushOutput(id);
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
    const resolvedCwd = expandUserHome(cwd) || process.cwd();
    const term = pty.spawn(shell, args, {
      name: "xterm-256color",
      cols: Math.max(20, Number(cols || 100)),
      rows: Math.max(6, Number(rows || 28)),
      cwd: resolvedCwd,
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
      resumeHealTimer: null,
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
    const resolvedCwd = expandUserHome(cwd) || process.cwd();
    const proc = spawn(command, {
      cwd: resolvedCwd,
      env,
      shell: true,
      windowsHide: false,
      stdio: ["pipe", "pipe", "pipe"],
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
      resumeHealTimer: null,
      exitPromise,
      resolveExit,
    };
    this.terminals.set(id, state);
    const emit = (chunk) => {
      const text = chunk?.toString?.("utf8") || String(chunk || "");
      this._handleOutput(id, state, text).catch(() => {});
    };
    proc.stdout?.on("data", emit);
    proc.stderr?.on("data", emit);
    proc.on("close", (code, signal) => {
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
    const classification = classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    await this._enqueueOutput(id, text);
    if (classification?.kind === "auth" && !state.classification) {
      state.classification = classification;
      await this._enqueueOutput(id, `\n[aify-comms] ${classification.message}\n`, { flushNow: true });
      if (state.kind === "pty") {
        try { terminateProcessTree(state.term, "SIGTERM"); } catch { try { state.term?.kill(); } catch {} }
      }
      else terminateProcessTree(state.proc, "SIGTERM");
    }
    this._armHermesResumeStallHeal(id, state);
  }

  _armHermesResumeStallHeal(id, state) {
    if (!state || state.runtime !== "hermes" || !state.sessionHandle || state.healAttempted || state.stopping) return;
    if (!hermesResumeStillPending(state.outputTail)) {
      if (state.resumeHealTimer) {
        clearTimeout(state.resumeHealTimer);
        state.resumeHealTimer = null;
      }
      return;
    }
    if (state.resumeHealTimer) return;
    state.resumeHealTimer = setTimeout(() => {
      state.resumeHealTimer = null;
      if (!this.terminals.has(id) || state.stopping || state.healAttempted) return;
      if (!hermesResumeStillPending(state.outputTail)) return;
      const message = `Hermes saved session handle did not become ready: ${state.sessionHandle}`;
      state.classification = {
        kind: "missing_session",
        status: "failed",
        sessionHandle: state.sessionHandle,
        message,
      };
      if (state.kind === "pty") {
        try { terminateProcessTree(state.term, "SIGTERM"); } catch { try { state.term?.kill(); } catch {} }
      }
      else terminateProcessTree(state.proc, "SIGTERM");
    }, hermesResumeStallHealMs());
    if (typeof state.resumeHealTimer.unref === "function") state.resumeHealTimer.unref();
  }

  async _enqueueOutput(id, text, { flushNow = false } = {}) {
    const chunk = String(text || "");
    if (!id || !chunk) return;
    let state = this.outputStates.get(id);
    if (!state) {
      state = { chunks: [], chars: 0, idleTimer: null, maxTimer: null, chain: Promise.resolve() };
      this.outputStates.set(id, state);
    }
    state.chunks.push(chunk);
    state.chars += chunk.length;
    if (!state.maxTimer) {
      state.maxTimer = setTimeout(() => {
        this._flushOutput(id).catch(() => {});
      }, this.maxLatencyMs);
    }
    if (state.idleTimer) clearTimeout(state.idleTimer);
    state.idleTimer = setTimeout(() => {
      this._flushOutput(id).catch(() => {});
    }, this.idleFlushMs);
    if (flushNow || state.chars >= this.maxBatchChars) await this._flushOutput(id);
  }

  async _flushOutput(id) {
    const state = this.outputStates.get(id);
    if (!state || !state.chunks.length) return state?.chain || Promise.resolve();
    if (state.idleTimer) clearTimeout(state.idleTimer);
    if (state.maxTimer) clearTimeout(state.maxTimer);
    const output = state.chunks.join("");
    state.chunks = [];
    state.chars = 0;
    state.idleTimer = null;
    state.maxTimer = null;
    const deliver = state.chain.then(() => this.onOutput(id, output));
    state.chain = deliver.catch(() => {});
    await deliver;
    if (!state.chunks.length && !state.idleTimer && !state.maxTimer) this.outputStates.delete(id);
  }

  async _handleExit(id, state, detail = {}) {
    if (state.finalized) return;
    state.finalized = true;
    if (state.resumeHealTimer) {
      clearTimeout(state.resumeHealTimer);
      state.resumeHealTimer = null;
    }
    if (this.terminals.get(id) === state) this.terminals.delete(id);
    state.resolveExit?.(detail);
    const classification = state.classification || classifyTerminalRuntimeOutput(state.runtime, state.outputTail);
    try {
      await this._flushOutput(id);
    } catch {
      // Exit status is still authoritative; do not let an output backfill
      // failure prevent the terminal from reaching stopped/failed.
    }
    if (
      classification?.kind === "missing_session" &&
      state.sessionHandle &&
      !state.healAttempted &&
      !state.stopping
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
    terminal.stopping = true;
    this.terminals.delete(id);
    if (terminal.kind === "pty") {
      // term.kill() sends a single SIGHUP to the wrapper bash, which the wrapper
      // traps do not catch and which never reaches its sibling/child processes.
      // Kill the whole process group instead.
      try { terminateProcessTree(terminal.term, "SIGTERM"); }
      catch { try { terminal.term.kill(); } catch {} }
      await waitForExitOrTimeout(terminal.exitPromise, 1500);
      return { stopped: true };
    }
    try {
      terminal.proc.stdin?.end();
    } catch {
      // Best effort.
    }
    terminateProcessTree(terminal.proc, "SIGTERM");
    const exited = await waitForExitOrTimeout(terminal.exitPromise, 3000);
    if (!exited) {
      await this._handleExit(id, terminal, { signal: "SIGTERM" });
    }
    return { stopped: true };
  }

  async stopAll(reason = "terminal manager shutdown") {
    const ids = Array.from(this.terminals.keys());
    for (const id of ids) {
      await this.stop(id, reason);
    }
  }
}
