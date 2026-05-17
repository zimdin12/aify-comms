import { spawn } from "child_process";
import { createRequire } from "module";
import { terminateProcessTree } from "./runtimes.js";

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

export class TerminalProcessManager {
  constructor({ onOutput = async () => {}, onExit = async () => {} } = {}) {
    this.onOutput = onOutput;
    this.onExit = onExit;
    this.terminals = new Map();
  }

  has(id) {
    return this.terminals.has(id);
  }

  async start({ id, command, cwd = process.cwd(), env = process.env, cols = 100, rows = 28 }) {
    if (!id) throw new Error("Terminal id is required");
    if (!command) throw new Error("Terminal command is required");
    if (this.terminals.has(id)) {
      await this.stop(id, "restarting terminal");
    }
    if (pty) {
      return this.startPty({ id, command, cwd, env, cols, rows });
    }
    return this.startPipeProcess({ id, command, cwd, env });
  }

  async startPty({ id, command, cwd, env, cols = 100, rows = 28 }) {
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
    const state = { id, command, cwd, term, status: "attached", kind: "pty", exitPromise };
    this.terminals.set(id, state);
    term.onData((text) => {
      if (text) this.onOutput(id, text).catch(() => {});
    });
    term.onExit(({ exitCode, signal }) => {
      this.terminals.delete(id);
      resolveExit?.({ code: exitCode, signal });
      this.onExit(id, { code: exitCode, signal }).catch(() => {});
    });
    return { pid: term.pid, status: "attached", pty: true };
  }

  async startPipeProcess({ id, command, cwd, env }) {
    const proc = spawn(command, {
      cwd: cwd || process.cwd(),
      env,
      shell: true,
      windowsHide: false,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const state = { id, command, cwd, proc, status: "attached" };
    this.terminals.set(id, state);
    const emit = (chunk) => {
      const text = chunk?.toString?.("utf8") || String(chunk || "");
      if (text) this.onOutput(id, text).catch(() => {});
    };
    proc.stdout?.on("data", emit);
    proc.stderr?.on("data", emit);
    proc.on("exit", (code, signal) => {
      this.terminals.delete(id);
      this.onExit(id, { code, signal }).catch(() => {});
    });
    proc.on("error", (error) => {
      this.terminals.delete(id);
      this.onExit(id, { error }).catch(() => {});
    });
    return { pid: proc.pid, status: "attached", pty: false };
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
