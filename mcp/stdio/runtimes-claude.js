// runtimes-claude.js — managed-Claude helpers: session transcripts, unlock
// PowerShell, permission/model/effort/turn config, launcher resolution, and
// wrapper staleness checks. Extracted verbatim from runtimes.js (task #123).
// runtimes.js re-exports the public surface.
import fs from "fs";
import path from "path";
import { userHomeDir } from "./runtimes-process.js";
import { resolveExecutable, inspectShebang, bashShebangFallback } from "./runtimes-exec.js";


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
    "function Stop-AifyTree($pid, $reason) {",
    "  if (-not $pid -or $pid -eq $ownPid) { return }",
    "  taskkill /pid $pid /t /f | Out-Null;",
    "  Write-Output (\"{0}:{1}\" -f $reason, $pid)",
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

function defaultClaudeCommand() {
  const configured = String(process.env.AIFY_CLAUDE_COMMAND || process.env.CLAUDE_COMMAND || "").trim();
  if (process.platform === "win32") {
    const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
    return { command: comspec, args: ["/d", "/s", "/c", configured || "claude"] };
  }
  // On POSIX, resolve to an absolute path so Node's spawn doesn't depend on the
  // bridge process inheriting an interactive shell's PATH (npm-global, nvm shims
  // etc. only appear after .profile/.bashrc sources them). Falls back to the
  // bare name if resolution fails — runtimeLaunchAvailability will then surface
  // an actionable message before the spawn is attempted.
  const target = configured || "claude";
  const resolved = resolveExecutable(target);
  if (resolved) {
    // If the resolved script has a broken shebang (its #! interpreter isn't
    // reachable from the bridge's PATH), run it through bash -lc so the
    // login shell can re-resolve the interpreter via nvm/asdf/etc.
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      return bashShebangFallback(resolved);
    }
    return { command: resolved, args: [] };
  }
  return { command: target, args: [] };
}

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
