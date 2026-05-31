#!/usr/bin/env node
// Reap orphaned managed-claude instances for one agent.
//
// ROOT CAUSE (2026-05-31): managed claude churns terminal_sessions — each
// dispatch / recover / restart spawns a new `claude-aify` PTY and marks the
// prior terminal `failed`. A terminal marked `failed` SERVER-side leaves the
// bridge with no live node-pty handle, so `terminateProcessTree`'s
// `taskkill /t` never runs on it and the native `claude.exe` is orphaned. Over
// time N instances all launched with the SAME `--resume <handle>` accumulate.
// Every instance runs claude-channel.js polling /dispatch/claim with the same
// machine-keyed channel-sidecar bridge id, so a dispatch is claimed + delivered
// by a RANDOM instance (not the dashboard-console one) → split delivery, the
// operator-reported "console never got the message".
//
// FIX (mirrors the hermes kill-prior teardown): before launching a managed
// claude for an agent, reap every claude process bound to that agent's stable
// resume handle (except an optional keepPid). The resume handle is the agent's
// unique Claude session id, so this targets exactly that agent's instances and
// never touches other agents. Result: at most ONE managed claude per agent.
//
// Process listing + kill are injectable so tests never touch real processes.

import { spawnSync as nodeSpawnSync } from "node:child_process";

// Enumerate running claude processes as [{ pid, commandLine }]. Cross-platform.
//   - win32: PowerShell Get-CimInstance Win32_Process (CommandLine carries args).
//   - posix: `ps -eo pid=,args=` filtered to claude.
export function defaultListClaudeProcs(spawnSync = nodeSpawnSync) {
  try {
    if (process.platform === "win32") {
      const ps =
        "Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | " +
        "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }";
      const res = spawnSync(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", ps],
        { encoding: "utf8", windowsHide: true, timeout: 8000 },
      );
      return parseProcLines(String(res.stdout || ""));
    }
    // POSIX: pid<TAB>args. `claude` may be a node script; match the args column.
    const res = spawnSync("ps", ["-eo", "pid=,args="], {
      encoding: "utf8",
      timeout: 8000,
    });
    return String(res.stdout || "")
      .split(/\r?\n/)
      .map((line) => {
        const m = line.match(/^\s*(\d+)\s+(.*)$/);
        if (!m) return null;
        return { pid: Number(m[1]), commandLine: m[2] };
      })
      .filter((p) => p && p.commandLine.includes("claude"));
  } catch {
    return [];
  }
}

// Parse "PID\tCOMMANDLINE" lines (win path). Exported for tests.
export function parseProcLines(stdout) {
  return String(stdout || "")
    .split(/\r?\n/)
    .map((line) => {
      const tab = line.indexOf("\t");
      if (tab < 0) return null;
      const pid = Number(line.slice(0, tab).trim());
      const commandLine = line.slice(tab + 1);
      if (!Number.isInteger(pid) || pid <= 0) return null;
      return { pid, commandLine };
    })
    .filter(Boolean);
}

// Kill one pid + its process tree. win32: taskkill /t /f; posix: SIGTERM→SIGKILL.
// Returns true on a clean kill. Injectable.
export function defaultKillPid(pid, spawnSync = nodeSpawnSync) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    if (process.platform === "win32") {
      const res = spawnSync("taskkill", ["/pid", String(n), "/t", "/f"], {
        stdio: "ignore",
        windowsHide: true,
        timeout: 5000,
      });
      return res.status === 0;
    }
    process.kill(n, "SIGTERM");
    return true;
  } catch {
    return false;
  }
}

// Which pids are managed-claude instances for `handle`? Matches the stable
// `--resume <handle>` token in the command line. The handle is the agent's
// unique Claude session id, so this never matches another agent.
export function pidsForResumeHandle(procs, handle) {
  const h = String(handle || "").trim();
  if (!h) return [];
  // Match `--resume <handle>` (the wrapper always passes it space-separated).
  // Use a boundary so a longer id that merely contains `h` is not matched.
  const re = new RegExp(`--resume[=\\s]+${escapeRegExp(h)}(?![\\w-])`);
  return (procs || [])
    .filter((p) => p && typeof p.commandLine === "string" && re.test(p.commandLine))
    .map((p) => Number(p.pid))
    .filter((pid) => Number.isInteger(pid) && pid > 0);
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Reap every managed-claude instance for `handle` except keepPid.
// Returns { candidates:[pid...], killed:[pid...] }. NEVER throws.
//   - handle: the agent's stable Claude resume id (required; empty → no-op).
//   - keepPid: the one instance to preserve (0/undefined → reap all).
//   - list/kill: injectable for tests.
export function reapPriorManagedClaude(
  handle,
  { keepPid = 0, list = defaultListClaudeProcs, kill = defaultKillPid } = {},
) {
  const keep = Number(keepPid) || 0;
  let procs = [];
  try {
    procs = list() || [];
  } catch {
    procs = [];
  }
  const candidates = pidsForResumeHandle(procs, handle).filter((pid) => pid !== keep);
  const killed = [];
  for (const pid of candidates) {
    try {
      if (kill(pid)) killed.push(pid);
    } catch {
      /* best-effort */
    }
  }
  return { candidates, killed };
}

// CLI: `node reap-managed-claude.js <resume-handle> [keepPid]`
// Prints the reap result as JSON. Used by the claude-aify managed launch.
function runCli(argv) {
  const handle = String(argv[0] || "").trim();
  const keepPid = Number(argv[1] || 0) || 0;
  if (!handle) {
    process.stderr.write("usage: reap-managed-claude.js <resume-handle> [keepPid]\n");
    process.exit(2);
    return;
  }
  const result = reapPriorManagedClaude(handle, { keepPid });
  process.stdout.write(JSON.stringify(result) + "\n");
}

// ESM entrypoint guard.
const _invokedDirectly =
  import.meta.url === `file://${process.argv[1]}` ||
  (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/")));
if (_invokedDirectly) {
  runCli(process.argv.slice(2));
}
