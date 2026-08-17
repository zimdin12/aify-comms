// runtimes-claude.js — managed-Claude helpers: session transcripts, unlock
// PowerShell, permission/model/effort/turn config, launcher resolution, and
// wrapper staleness checks. Extracted verbatim from runtimes.js (task #123).
// runtimes.js re-exports the public surface.
import fs from "fs";
import path from "path";
import { userHomeDir } from "./runtimes-process.js";


const DEFAULT_CLAUDE_MAX_TURNS = 50;

function quotePowerShellString(value) {
  return `'${String(value || "").replace(/'/g, "''")}'`;
}

function claudeProjectNameForCwd(cwd) {
  return String(cwd || process.cwd()).replace(/[^a-zA-Z0-9]/g, "-");
}

export function claudeSessionTranscriptPath(sessionId, cwd = process.cwd()) {
  const normalized = String(sessionId || "").trim();
  if (!normalized) return "";
  return path.join(userHomeDir(), ".claude", "projects", claudeProjectNameForCwd(cwd), `${normalized}.jsonl`);
}

export function claudeSessionTranscriptExists(sessionId, cwd = process.cwd()) {
  const transcriptPath = claudeSessionTranscriptPath(sessionId, cwd);
  if (!transcriptPath) return false;
  try {
    return fs.statSync(transcriptPath).isFile();
  } catch {
    return false;
  }
}

export function buildManagedClaudeUnlockPowerShell(sessionId, markerPids = []) {
  const sid = quotePowerShellString(sessionId);
  const pids = (Array.isArray(markerPids) ? markerPids : [])
    .map((pid) => Number(pid))
    .filter((pid) => Number.isInteger(pid) && pid > 0);
  const markerPidLiteral = pids.length ? `@(${pids.join(",")})` : "@()";
  return [
    "$ErrorActionPreference = 'SilentlyContinue';",
    "$sid = " + sid + ";",
    "$ownPid = $PID;",
    "$markerPids = " + markerPidLiteral + ";",
    "$all = @(Get-CimInstance Win32_Process);",
    // $pid is an AUTOMATIC read-only PowerShell variable — assigning it as a param
    // throws "Cannot overwrite variable pid because it is read-only" on every call,
    // and under SilentlyContinue the taskkill body silently never runs (the unlock
    // becomes a no-op). Use $targetPid (bughunt 2026-07-03).
    "function Stop-AifyTree($targetPid, $reason) {",
    "  if (-not $targetPid -or $targetPid -eq $ownPid) { return }",
    "  taskkill /pid $targetPid /t /f | Out-Null;",
    "  Write-Output (\"{0}:{1}\" -f $reason, $targetPid)",
    "}",
    "function Test-AifyProtected($process) {",
    "  if (-not $process) { return $true }",
    "  $cmd = [string]$process.CommandLine;",
    "  return $cmd -match 'claude-aify' -or $cmd -match '(^|\\s)--resume(\\s|=)'",
    "}",
    "function Test-AifyHeadlessClaude($process) {",
    "  if (-not $process) { return $false }",
    "  $cmd = [string]$process.CommandLine;",
    "  $name = [string]$process.Name;",
    "  if (Test-AifyProtected $process) { return $false }",
    "  if ($cmd -notmatch 'claude' -and $name -notmatch '^(node|claude|cmd)(\\.exe)?$') { return $false }",
    "  return $cmd -match '(^|\\s)(-p|--print)(\\s|$)' -or $cmd -match '(^|\\s)--session-id(\\s|=)'",
    "}",
    "Get-CimInstance Win32_Process |",
    "  Where-Object {",
    "    $_.ProcessId -ne $ownPid -and",
    "    $_.CommandLine -match [regex]::Escape($sid) -and",
    "    $_.CommandLine -notmatch '(^|\\s)--resume(\\s|=)' -and",
    "    $_.CommandLine -notmatch 'claude-aify' -and",
    "    (",
    "      $_.CommandLine -match '(^|\\s)(-p|--print)(\\s|$)' -or",
    "      $_.CommandLine -match '(^|\\s)--session-id(\\s|=)' -or",
    "      $_.CommandLine -match 'claude' -or",
    "      $_.Name -match '^(node|claude|cmd)(\\.exe)?$'",
    "    )",
    "  } |",
    "  ForEach-Object {",
    "    Stop-AifyTree $_.ProcessId 'session'",
    "  };",
    "foreach ($markerPid in $markerPids) {",
    "  $marker = $all | Where-Object { $_.ProcessId -eq $markerPid } | Select-Object -First 1;",
    "  if (-not $marker) { continue }",
    "  $parent = $all | Where-Object { $_.ProcessId -eq $marker.ParentProcessId } | Select-Object -First 1;",
    "  $grandparent = if ($parent) { $all | Where-Object { $_.ProcessId -eq $parent.ParentProcessId } | Select-Object -First 1 } else { $null };",
    "  foreach ($candidate in @($parent, $grandparent)) {",
    "    if (Test-AifyHeadlessClaude $candidate) {",
    "      Stop-AifyTree $candidate.ProcessId 'marker';",
    "      break;",
    "    }",
    "  }",
    "}",
  ].join("\n");
}

export function managedClaudePermissionArgs(config = {}, executionMode = "managed") {
  const policy = String(config.approvalPolicy || config.permissionMode || "").trim().toLowerCase();
  if (config.skipPermissions === false || policy === "ask" || policy === "default") {
    return [];
  }
  if (executionMode !== "resident" || config.skipPermissions === true || policy === "never" || policy === "full-auto") {
    return ["--dangerously-skip-permissions"];
  }
  return [];
}

export function managedClaudeModel(agentInfo = {}, config = {}) {
  return String(agentInfo.model || config.model || "").trim();
}

export function managedClaudeEffort(config = {}) {
  return String(config.effort || "high").trim();
}

export function managedClaudeMaxTurns(config = {}) {
  const value = Number(config.maxTurns || DEFAULT_CLAUDE_MAX_TURNS);
  if (!Number.isFinite(value) || value <= 0) return DEFAULT_CLAUDE_MAX_TURNS;
  return Math.floor(value);
}

// `defaultClaudeCommand()` lived here until 2026-08-17 and was DEAD: defined once, exported never, called by
// nothing in the repo. It built the spawn command for `claude -p`, which this bridge stopped launching when
// delivery moved to the claude-channel.js sidecar inside claude-aify — the reason ClaudeController is a safety
// belt that refuses every verb. The V8-coverage census is what surfaced it (zero calls), and its removal took
// this file's only uses of resolveExecutable / inspectShebang / bashShebangFallback with it.
//
// Nothing was lost: the win32 half (ComSpec + `/d /s /c`) still lives in `runtimes-exec.js`'s `where` probe and
// in `terminal-runtime.js`'s PTY command wrapping, and the POSIX absolute-path rationale it documented is
// carried at its remaining reader in `runtimes-codex.js`.

export function staleClaudeAifyWrapperReason(resolved) {
  const value = String(resolved || "").trim();
  if (!value) return "";
  const candidates = [value];
  if (value.toLowerCase().endsWith(".cmd")) {
    candidates.push(value.slice(0, -4));
  }
  for (const candidate of candidates) {
    try {
      if (!candidate || !fs.existsSync(candidate)) continue;
      const stat = fs.statSync(candidate);
      if (!stat.isFile() || stat.size > 1024 * 1024) continue;
      const body = fs.readFileSync(candidate, "utf-8");
      if (body.includes("--channels server:aify-comms-channel")) {
        return `resolved wrapper "${candidate}" has stale Claude --channels flag (custom channels must use only --dangerously-load-development-channels); rerun install.sh for Claude support`;
      }
    } catch {
      // Best-effort validation. Launch itself will surface unreadable files.
    }
  }
  return "";
}

export function isClaudeSessionInUseError(text) {
  return /session id(?:\s+[0-9a-f-]+)?\s+is already in use/i.test(String(text || ""));
}
