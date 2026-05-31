#!/usr/bin/env node
// Reap orphaned managed-claude instances for ONE agent — AGENT-SCOPED & SAFE.
//
// ROOT CAUSE of the leak: managed claude churns terminal_sessions (each
// dispatch/recover/restart spawns a fresh claude-aify PTY and marks the prior
// 'failed'). A server-marked-'failed' terminal leaves the bridge with no live
// node-pty handle, so terminateProcessTree's taskkill never runs on it and the
// native claude.exe is orphaned. Siblings accumulate, each polling
// /dispatch/claim under the same channel-sidecar bridge id → split delivery.
//
// SAFETY INCIDENT (2026-05-31): the first version keyed kill-prior on
// `--resume <handle>` ALONE. That is only unique if Claude session ids never
// collide across agents. They CAN collide (cross-contamination, #138): a
// managed agent (graph-tech-lead) was bound to a RESIDENT operator session's
// live id (651b895f). Reaping by handle then `taskkill /f`-ed the operator's
// own comms-tech-lead session. NEVER AGAIN.
//
// FIX — agent-scoped: a candidate claude.exe is reaped ONLY when its parent
// wrapper process is `claude-aify --aify-agent <THIS agent>`. The agent id is
// unique; a different agent's claude (or a resident operator session, which
// has a different --aify-agent) can never be killed by this agent's launch,
// even under a handle collision. Fail-safe: no agentId, or a parent we can't
// confirm belongs to this agent → DO NOT kill (leaking a process is acceptable;
// killing the wrong session is not).
//
// Process listing + kill are injectable so tests never touch real processes.

import { spawnSync as nodeSpawnSync } from "node:child_process";

// Enumerate running claude processes as [{ pid, ppid, commandLine }].
//   - win32: PowerShell Get-CimInstance Win32_Process (ParentProcessId+CommandLine).
//   - posix: `ps -eo pid=,ppid=,args=` filtered to claude.
export function defaultListClaudeProcs(spawnSync = nodeSpawnSync) {
  try {
    if (process.platform === "win32") {
      const ps =
        "Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\" | " +
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }";
      const res = spawnSync(
        "powershell.exe",
        ["-NoProfile", "-NonInteractive", "-Command", ps],
        { encoding: "utf8", windowsHide: true, timeout: 8000 },
      );
      return parseProcLines(String(res.stdout || ""));
    }
    const res = spawnSync("ps", ["-eo", "pid=,ppid=,args="], {
      encoding: "utf8",
      timeout: 8000,
    });
    return String(res.stdout || "")
      .split(/\r?\n/)
      .map((line) => {
        const m = line.match(/^\s*(\d+)\s+(\d+)\s+(.*)$/);
        if (!m) return null;
        return { pid: Number(m[1]), ppid: Number(m[2]), commandLine: m[3] };
      })
      .filter((p) => p && p.commandLine.includes("claude"));
  } catch {
    return [];
  }
}

// Parse "PID\tPPID\tCOMMANDLINE" lines (win path). Exported for tests.
export function parseProcLines(stdout) {
  return String(stdout || "")
    .split(/\r?\n/)
    .map((line) => {
      const parts = line.split("\t");
      if (parts.length < 3) return null;
      const pid = Number(parts[0].trim());
      const ppid = Number(parts[1].trim());
      const commandLine = parts.slice(2).join("\t");
      if (!Number.isInteger(pid) || pid <= 0) return null;
      return { pid, ppid: Number.isInteger(ppid) ? ppid : 0, commandLine };
    })
    .filter(Boolean);
}

// Get the command line of ANY pid (used to inspect a candidate's parent
// wrapper). Injectable. Returns "" when unknown.
export function defaultGetCmdline(pid, spawnSync = nodeSpawnSync) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return "";
  try {
    if (process.platform === "win32") {
      const ps =
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${n}" -ErrorAction SilentlyContinue).CommandLine`;
      const res = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", ps], {
        encoding: "utf8", windowsHide: true, timeout: 5000,
      });
      return String(res.stdout || "").trim();
    }
    const res = spawnSync("ps", ["-o", "args=", "-p", String(n)], { encoding: "utf8", timeout: 5000 });
    return String(res.stdout || "").trim();
  } catch {
    return "";
  }
}

// Kill one pid + its process tree. win32: taskkill /t /f; posix: SIGTERM→SIGKILL.
export function defaultKillPid(pid, spawnSync = nodeSpawnSync) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    if (process.platform === "win32") {
      const res = spawnSync("taskkill", ["/pid", String(n), "/t", "/f"], {
        stdio: "ignore", windowsHide: true, timeout: 5000,
      });
      return res.status === 0;
    }
    process.kill(n, "SIGTERM");
    return true;
  } catch {
    return false;
  }
}

function escapeRegExp(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Procs (with ppid) whose cmdline carries `--resume <handle>` (space or = form).
export function procsForResumeHandle(procs, handle) {
  const h = String(handle || "").trim();
  if (!h) return [];
  const re = new RegExp(`--resume[=\\s]+${escapeRegExp(h)}(?![\\w-])`);
  return (procs || []).filter(
    (p) => p && typeof p.commandLine === "string" && re.test(p.commandLine),
  );
}

// Back-compat: pids matching the handle (NOT agent-scoped; for tests/inspection).
export function pidsForResumeHandle(procs, handle) {
  return procsForResumeHandle(procs, handle)
    .map((p) => Number(p.pid))
    .filter((pid) => Number.isInteger(pid) && pid > 0);
}

// Does `parentCmdline` show this is THIS agent's managed claude wrapper?
// Matches `--aify-agent <agentId>` (space or = form), with a boundary so a
// longer agent id that merely contains agentId is not matched.
export function parentBelongsToAgent(parentCmdline, agentId) {
  const a = String(agentId || "").trim();
  if (!a || !parentCmdline) return false;
  const re = new RegExp(`--aify-agent[=\\s]+${escapeRegExp(a)}(?![\\w-])`);
  return re.test(String(parentCmdline));
}

// Reap managed-claude instances of `agentId` resuming `handle`, except keepPid.
// AGENT-SCOPED: a candidate is killed ONLY when its parent process is the
// claude-aify wrapper for THIS agent (`--aify-agent <agentId>`). This makes the
// reaper immune to session-handle collisions and unable to kill another agent's
// or a resident operator's session. Fail-safe: missing agentId, or a parent we
// can't confirm, → NOT killed.
// Returns { candidates:[pid], killed:[pid], skipped:[{pid,reason}] }. Never throws.
export function reapPriorManagedClaude(
  handle,
  {
    agentId = "",
    keepPid = 0,
    list = defaultListClaudeProcs,
    getCmdline = defaultGetCmdline,
    kill = defaultKillPid,
  } = {},
) {
  const agent = String(agentId || "").trim();
  const keep = Number(keepPid) || 0;
  const result = { candidates: [], killed: [], skipped: [] };

  // SAFETY: never reap without an explicit agent id to scope the kill.
  if (!agent) {
    return { ...result, skipped: [{ pid: 0, reason: "no agentId — fail-safe, killed nothing" }] };
  }

  let procs = [];
  try {
    procs = list() || [];
  } catch {
    procs = [];
  }
  const candidates = procsForResumeHandle(procs, handle).filter((p) => Number(p.pid) !== keep);
  result.candidates = candidates.map((p) => Number(p.pid));

  for (const p of candidates) {
    const pid = Number(p.pid);
    let parentCmd = "";
    try {
      parentCmd = getCmdline(p.ppid) || "";
    } catch {
      parentCmd = "";
    }
    if (!parentBelongsToAgent(parentCmd, agent)) {
      // A different agent's claude, a resident operator session, or an
      // unconfirmable parent (e.g. orphaned wrapper). Do NOT kill.
      result.skipped.push({ pid, reason: "parent wrapper is not this agent (or unknown) — not killed" });
      continue;
    }
    try {
      if (kill(pid)) result.killed.push(pid);
    } catch {
      /* best-effort */
    }
  }
  return result;
}

// CLI: `node reap-managed-claude.js <resume-handle> <agentId> [keepPid]`
function runCli(argv) {
  const handle = String(argv[0] || "").trim();
  const agentId = String(argv[1] || "").trim();
  const keepPid = Number(argv[2] || 0) || 0;
  if (!handle || !agentId) {
    process.stderr.write("usage: reap-managed-claude.js <resume-handle> <agentId> [keepPid]\n");
    process.exit(2);
    return;
  }
  process.stdout.write(JSON.stringify(reapPriorManagedClaude(handle, { agentId, keepPid })) + "\n");
}

const _invokedDirectly =
  import.meta.url === `file://${process.argv[1]}` ||
  (process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/")));
if (_invokedDirectly) {
  runCli(process.argv.slice(2));
}
