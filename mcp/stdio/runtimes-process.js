// runtimes-process.js — shared process spawn/kill/env/cwd utilities for
// runtime launches. Extracted verbatim from runtimes.js (task #123, split
// into per-concern modules). runtimes.js re-exports the public surface.
import { spawn, spawnSync } from "child_process";
import os from "os";
import fs from "fs";
import path from "path";

export function userHomeDir() {
  return process.env.HOME || os.homedir();
}

function isWindowsNodeScript(command) {
  if (process.platform !== "win32") return false;
  const ext = path.extname(String(command || "")).toLowerCase();
  return [".js", ".mjs", ".cjs"].includes(ext);
}

function spawnRawProcess(command, args, options = {}, { forceNode = false } = {}) {
  const executable = forceNode ? process.execPath : command;
  const finalArgs = forceNode ? [command, ...args] : args;
  return spawn(executable, finalArgs, {
    cwd: options.cwd || process.cwd(),
    env: runtimeChildEnv(options.env || {}),
    stdio: ["pipe", "pipe", "pipe"],
    shell: false,
    detached: !forceNode && process.platform !== "win32",
    windowsHide: true,
  });
}

// Tokenize a command string with shell-style quoting so paths containing
// spaces survive an env-var override. Supports double-quote and single-
// quote groupings. Backslash escapes the next char ONLY when not inside
// quotes — inside double quotes backslash is literal (POSIX-ish but
// Windows-path-friendly: `"C:\Program Files\hermes\hermes.exe"` parses
// to the literal Windows path without losing backslashes).
// Returns { command, args }. Empty input → { command: "", args: [] }.
// Used by AIFY_HERMES_ACP_COMMAND, AIFY_CODEX_COMMAND, and any future
// shell-style env override (fixes I7 from 2026-05-23 code review).
export function tokenizeCommandString(raw) {
  const text = String(raw || "");
  const tokens = [];
  let current = "";
  let inDouble = false;
  let inSingle = false;
  let hasToken = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "\\" && i + 1 < text.length && !inDouble && !inSingle && /\s/.test(text[i + 1])) {
      // Outside quotes: backslash ONLY escapes whitespace (so `foo\ bar`
      // is a single token). Anywhere else outside quotes (including
      // Windows path separators like `C:\Program Files\…`), backslash is
      // literal — otherwise an unquoted Windows path would lose its
      // separators.
      current += text[i + 1];
      hasToken = true;
      i += 1;
      continue;
    }
    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      hasToken = true;
      continue;
    }
    if (ch === "'" && !inDouble) {
      inSingle = !inSingle;
      hasToken = true;
      continue;
    }
    if (/\s/.test(ch) && !inDouble && !inSingle) {
      if (hasToken) {
        tokens.push(current);
        current = "";
        hasToken = false;
      }
      continue;
    }
    current += ch;
    hasToken = true;
  }
  if (hasToken) tokens.push(current);
  return { command: tokens[0] || "", args: tokens.slice(1) };
}

export function spawnProcess(command, args, options = {}) {
  const cwd = options.cwd || process.cwd();
  assertLaunchCwd(cwd);
  const useNode = isWindowsNodeScript(command);
  const proc = spawnRawProcess(command, args, { ...options, cwd }, { forceNode: useNode });
  // ChildProcess emits "error" when the executable is missing or cannot be
  // started. Keep a listener attached at creation time so a runtime adapter
  // bug cannot crash the bridge process before the adapter wires rejection.
  proc.on("error", () => {});
  return proc;
}

export function launchCwdProblem(cwd) {
  const value = String(cwd || "").trim();
  if (!value) return null;
  try {
    const st = fs.statSync(value);
    if (!st.isDirectory()) return `Workspace "${value}" is not a directory.`;
    try {
      fs.accessSync(value, fs.constants.R_OK | fs.constants.X_OK);
    } catch {
      return `Workspace "${value}" is not readable/searchable by user ${os.userInfo().username}.`;
    }
    return null;
  } catch (error) {
    if (error?.code === "ENOENT") return `Workspace "${value}" does not exist on this bridge host.`;
    if (error?.code === "EACCES") return `Workspace "${value}" is not accessible by user ${os.userInfo().username}.`;
    return `Workspace "${value}" cannot be used as a runtime cwd: ${error?.message || String(error)}`;
  }
}

function assertLaunchCwd(cwd) {
  const problem = launchCwdProblem(cwd);
  if (!problem) return;
  const error = new Error(
    `${problem} Runtime launch aborted before spawn(). Check that the agent workspace is valid for ` +
    `environment "${process.env.AIFY_ENVIRONMENT_ID || "unknown"}" on ${os.hostname()}, then update the agent/session workspace or environment roots.`,
  );
  error.code = "AIFY_INVALID_RUNTIME_CWD";
  error.cwd = cwd;
  throw error;
}

export function descendantPids(pid) {
  const rootPid = Number(pid);
  if (!Number.isInteger(rootPid) || rootPid <= 0 || process.platform === "win32") return [];
  let result;
  try {
    result = spawnSync("ps", ["-eo", "pid=,ppid="], {
      encoding: "utf8",
      timeout: 3000,
    });
  } catch {
    return [];
  }
  if (result.status !== 0) return [];
  const childrenByParent = new Map();
  for (const line of String(result.stdout || "").split(/\r?\n/)) {
    const [childText, parentText] = line.trim().split(/\s+/);
    const child = Number(childText);
    const parent = Number(parentText);
    if (!Number.isInteger(child) || !Number.isInteger(parent)) continue;
    if (!childrenByParent.has(parent)) childrenByParent.set(parent, []);
    childrenByParent.get(parent).push(child);
  }
  const descendants = [];
  const stack = [...(childrenByParent.get(rootPid) || [])];
  while (stack.length) {
    const child = stack.pop();
    if (!child || descendants.includes(child)) continue;
    descendants.push(child);
    stack.push(...(childrenByParent.get(child) || []));
  }
  return descendants;
}

// ── Reaper self-protection (2026-07-03) ──────────────────────────────────────
// The boot survivor sweep + terminateProcessTree kill orphaned managed
// processes by pid, by descendant pid, and — crucially — by NEGATIVE pid
// (`process.kill(-pid)` = signal the whole process GROUP). None of these
// validated that the target wasn't the bridge itself, its process group, or its
// launching shell. Consequences observed on a busy host running the env bridge
// from `/`: during a large boot sweep, a group-kill (`kill(-pid)`) whose pgid
// collided with the bridge's session — or a recycled pid that now mapped to the
// bridge/parent shell — delivered SIGTERM to the bridge, which then ran its
// graceful teardown and exited (non-zero). It reproduced on the 1st/2nd launch
// (big orphan backlog to kill through) and stopped once the backlog drained.
//
// Also lethal without a guard: `process.kill(0, sig)` signals the CALLER's
// entire process group, and `process.kill(1, sig)` targets init. Capture our
// identity ONCE at load — ppid at load is the launching shell, before any
// reparenting to init hides it.
const OWN_PID = process.pid;
const OWN_PPID = typeof process.ppid === "number" ? process.ppid : 0;
const OWN_PGID = readOwnProcessGroupId();

// Linux: /proc/self/stat field 5 (pgrp). comm (field 2) may contain spaces and
// parens, so parse the fields AFTER the last ')'. Returns null off-Linux (the
// negative-pid group path is posix-only and win32 uses taskkill), where the
// pid/ppid/0/1 guards still apply.
function readOwnProcessGroupId() {
  try {
    const stat = fs.readFileSync("/proc/self/stat", "utf8");
    const afterComm = stat.slice(stat.lastIndexOf(")") + 2).trim().split(/\s+/);
    // afterComm: [state, ppid, pgrp, ...]
    const pgrp = Number(afterComm[2]);
    return Number.isInteger(pgrp) && pgrp > 0 ? pgrp : null;
  } catch {
    return null;
  }
}

// True when signalling `pid` could hit the bridge itself, its process group,
// its launching shell, or a system-critical target (0 = caller's whole group,
// 1 = init). The reaper MUST skip these — killing a leaked agent is never worth
// taking the bridge (or the operator's shell) down with it. Accepts negative
// pids (group form) and compares on the magnitude.
export function pidIsSelfProtected(pid) {
  const n = Number(pid);
  if (!Number.isInteger(n)) return true;            // never signal garbage
  const pos = Math.abs(n);
  if (pos <= 1) return true;                         // 0 = our group, 1 = init
  if (pos === OWN_PID) return true;                  // ourselves (and -OWN_PID group)
  if (OWN_PPID > 1 && pos === OWN_PPID) return true; // launching shell — killing it HUPs us
  if (OWN_PGID && pos === OWN_PGID) return true;     // our process group — kill(-pgid) hits the bridge
  return false;
}

function killPid(pid, signal) {
  if (pidIsSelfProtected(pid)) {
    try {
      console.error(
        `[aify] reaper self-protect: refused to signal pid=${pid} ` +
        `(matches bridge pid/ppid/pgid or init) — not killing the bridge`,
      );
    } catch { /* best effort */ }
    return false;
  }
  try {
    process.kill(pid, signal);
    return true;
  } catch {
    return false;
  }
}

function pidIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

export function terminateProcessTree(proc, signal = "SIGTERM") {
  if (!proc || !proc.pid) return;
  if (process.platform === "win32") {
    try {
      const result = spawnSync("taskkill", ["/pid", String(proc.pid), "/t", "/f"], {
        stdio: "ignore",
        windowsHide: true,
        timeout: 5000,
      });
      if (result.status === 0) return;
    } catch {
      // Fall through to proc.kill below.
    }
  }
  if (process.platform !== "win32") {
    const pid = Number(proc.pid);
    if (Number.isInteger(pid) && pid > 0) {
      // Managed Codex/OpenCode spawn long-lived MCP children. Kill the
      // process group and descendants. Capture descendants before killing the
      // parent, otherwise escaped children can be reparented before we see them.
      const descendants = descendantPids(pid).reverse();
      killPid(-pid, signal);
      for (const childPid of descendants) {
        killPid(childPid, signal);
      }
      if (signal !== "SIGKILL") {
        setTimeout(() => {
          for (const childPid of descendants) {
            if (pidIsAlive(childPid)) killPid(childPid, "SIGKILL");
          }
          if (pidIsAlive(pid)) killPid(pid, "SIGKILL");
        }, 150);
      }
    }
  }
  try {
    proc.kill(signal);
  } catch {
    // Best-effort cleanup.
  }
}

const ENVIRONMENT_BRIDGE_ENV_KEYS = [
  "AIFY_ENVIRONMENT_BRIDGE",
  "AIFY_ENVIRONMENT_ID",
  "AIFY_ENVIRONMENT_LABEL",
  "AIFY_ENVIRONMENT_KIND",
  "AIFY_CWD_ROOTS",
];

export function runtimeChildEnv(extraEnv = {}) {
  // Do NOT default AIFY_BRIDGE_DISABLED or clear AIFY_AGENT_ID here.
  // Wrapper children (claude-aify → claude → mcp/stdio/server.js,
  // codex/hermes/opencode equivalents) legitimately host MCP servers
  // that need the aify env. Applying the disabled flag globally was
  // a real regression (broke claude-code permissions/MCP integration).
  // Only specific RPC-child spawn sites that are KNOWN to accidentally
  // nest mcp/stdio/server.js (e.g. pi `omp --mode rpc`) pass the
  // disabled flag explicitly via extraEnv.
  const env = { ...process.env, ...(extraEnv || {}) };
  for (const key of ENVIRONMENT_BRIDGE_ENV_KEYS) {
    delete env[key];
  }
  return env;
}
