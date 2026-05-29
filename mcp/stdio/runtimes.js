import { spawn, spawnSync } from "child_process";
import os from "os";
import fs from "fs";
import path from "path";
import readline from "readline";
import { fileURLToPath } from "url";
import WebSocket from "ws";
import { listRuntimeMarkers } from "./runtime-markers.js";
import { resolveCodexRequestCwdFor } from "./codex-errors.js";
import { adapterFor } from "./adapters/index.js";

const DEFAULT_CLAUDE_MAX_TURNS = 50;
function userHomeDir() {
  return process.env.HOME || os.homedir();
}


const RUNTIME_ALIASES = new Map([
  ["claude", "claude-code"],
  ["claude-code", "claude-code"],
  ["claude_code", "claude-code"],
  ["codex", "codex"],
  ["hermes", "hermes"],
  ["hermes-agent", "hermes"],
  ["hermes_agent", "hermes"],
  ["oh-my-pi", "pi"],
  ["oh_my_pi", "pi"],
  ["opencode", "opencode"],
  ["omp", "pi"],
  ["pi", "pi"],
  ["pi-agent", "pi"],
  ["pi_agent", "pi"],
  ["generic", "generic"],
]);


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

function killPid(pid, signal) {
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

const RUNTIME_DIR = path.dirname(fileURLToPath(import.meta.url));
const SERVER_SCRIPT = path.join(RUNTIME_DIR, "server.js");

function tomlString(value) {
  return JSON.stringify(String(value || ""));
}

function copyIfExists(source, target) {
  try {
    if (fs.existsSync(source)) fs.copyFileSync(source, target);
  } catch {
    // best effort; Codex will surface auth/config issues clearly if copy fails.
  }
}

function defaultCodexHomePath() {
  return path.join(userHomeDir(), ".codex");
}

function resolvedPath(value) {
  try {
    return path.resolve(String(value || ""));
  } catch {
    return String(value || "");
  }
}

function sameResolvedPath(left, right) {
  return resolvedPath(left) === resolvedPath(right);
}

function codexSourceHomes(targetHome = "") {
  const candidates = [
    process.env.CODEX_HOME || "",
    defaultCodexHomePath(),
  ];
  const seen = new Set();
  return candidates
    .map((candidate) => String(candidate || "").trim())
    .filter(Boolean)
    .filter((candidate) => {
      const resolved = resolvedPath(candidate);
      if (seen.has(resolved)) return false;
      seen.add(resolved);
      return !targetHome || !sameResolvedPath(candidate, targetHome);
    });
}

function findFilesContaining(baseDir, needle, { prefix = "", suffix = "" } = {}) {
  const root = String(baseDir || "").trim();
  const id = String(needle || "").trim();
  if (!root || !id) return [];
  try {
    if (!fs.statSync(root).isDirectory()) return [];
  } catch {
    return [];
  }

  const matches = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;
      if (prefix && !entry.name.startsWith(prefix)) continue;
      if (suffix && !entry.name.endsWith(suffix)) continue;
      if (!entry.name.includes(id)) continue;
      matches.push(fullPath);
    }
  }
  return matches.sort();
}

export function findCodexThreadFiles({ threadId, sourceHome } = {}) {
  const home = String(sourceHome || "").trim();
  const id = String(threadId || "").trim();
  if (!home || !id) return { rollouts: [], shellSnapshots: [] };
  return {
    rollouts: findFilesContaining(path.join(home, "sessions"), id, {
      prefix: "rollout-",
      suffix: ".jsonl",
    }),
    shellSnapshots: findFilesContaining(path.join(home, "shell_snapshots"), id),
  };
}

function copyPreservingCodexRelativePath(sourceHome, targetHome, filePath) {
  const relative = path.relative(sourceHome, filePath);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) return false;
  const targetPath = path.join(targetHome, relative);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });
  fs.copyFileSync(filePath, targetPath);
  return true;
}

export function importCodexThreadRollout({ threadId, targetHome, sourceHome = "" } = {}) {
  const id = String(threadId || "").trim();
  const target = String(targetHome || "").trim();
  if (!id || !target) {
    return { imported: false, sourceHome: "", rollouts: [], shellSnapshots: [] };
  }

  const sources = sourceHome
    ? [sourceHome]
    : codexSourceHomes(target);
  for (const candidate of sources) {
    const source = String(candidate || "").trim();
    if (!source || sameResolvedPath(source, target)) continue;
    const files = findCodexThreadFiles({ threadId: id, sourceHome: source });
    if (!files.rollouts.length) continue;

    const copiedRollouts = [];
    const copiedSnapshots = [];
    fs.mkdirSync(target, { recursive: true });
    for (const filePath of files.rollouts) {
      if (copyPreservingCodexRelativePath(source, target, filePath)) {
        copiedRollouts.push(path.relative(source, filePath));
      }
    }
    for (const filePath of files.shellSnapshots) {
      if (copyPreservingCodexRelativePath(source, target, filePath)) {
        copiedSnapshots.push(path.relative(source, filePath));
      }
    }
    return {
      imported: copiedRollouts.length > 0,
      sourceHome: source,
      rollouts: copiedRollouts,
      shellSnapshots: copiedSnapshots,
    };
  }

  return { imported: false, sourceHome: "", rollouts: [], shellSnapshots: [] };
}

function copyDirectoryFreshIfExists(source, target) {
  try {
    if (!fs.existsSync(source)) return false;
    fs.rmSync(target, { recursive: true, force: true });
    fs.cpSync(source, target, { recursive: true });
    return true;
  } catch {
    return false;
  }
}

function installManagedCodexSkills(sourceHome, targetHome) {
  const bundledSkills = path.resolve(RUNTIME_DIR, "..", "..", ".agents", "skills");
  for (const name of ["aify-comms", "aify-comms-debug"]) {
    const target = path.join(targetHome, "skills", name);
    const installedSource = path.join(sourceHome, "skills", name);
    if (copyDirectoryFreshIfExists(installedSource, target)) continue;
    copyDirectoryFreshIfExists(path.join(bundledSkills, name), target);
  }
}

export function managedCodexConfigText({ workspace = "", serverUrl = "", model = "", effort = "" } = {}) {
  const resolvedModel = String(model || "").trim();
  // Plan 6 follow-up (2026-05-26): include `env_vars` (codex's passthrough
  // mechanism) so the inner aify-comms MCP child inherits AIFY_AGENT_ID /
  // AIFY_SESSION_MODE / AIFY_MANAGED_VIA_WRAPPER / etc. from the wrapper
  // PTY's codex process. Without this, codex's per-child env REPLACES the
  // inherited environment (codex-rs/rmcp-client/src/utils.rs
  // create_env_for_mcp_server) and the inner MCP registers without an
  // agent id — no bridge advertises `channel` for the wrapper-backed
  // managed codex agent, and dispatches sit queued forever. Symmetric
  // with install.sh install_codex_mcp_env_vars for the operator's
  // ~/.codex/config.toml.
  const envVarPassthrough = [
    "AIFY_AGENT_ID",
    "AIFY_AGENT_ROLE",
    "AIFY_AGENT_CWD",
    "AIFY_SESSION_MODE",
    "AIFY_SESSION_HANDLE",
    "AIFY_RUNTIME",
    "AIFY_TERMINAL_ID",
    "AIFY_MANAGED_VIA_WRAPPER",
    "AIFY_COMMS_AGENT_ID",
    "AIFY_COMMS_URL",
    "AIFY_API_KEY",
    "CODEX_THREAD_ID",
    "AIFY_CODEX_APP_SERVER_URL",
  ];
  const lines = [
    `model_reasoning_effort = ${tomlString(effort || "high")}`,
    "",
    "[features]",
    "multi_agent = true",
    "hooks = false",
    "",
    "[notice]",
    "hide_full_access_warning = true",
    "hide_rate_limit_model_nudge = true",
    "",
    "[mcp_servers.aify-comms]",
    `command = ${tomlString(process.execPath)}`,
    `args = [${tomlString(SERVER_SCRIPT)}]`,
    "enabled = true",
    "startup_timeout_sec = 10",
    "tool_timeout_sec = 25",
    'disabled_tools = ["comms_listen"]',
    `env_vars = [${envVarPassthrough.map((n) => tomlString(n)).join(", ")}]`,
    "",
    "[mcp_servers.aify-comms.env]",
    `AIFY_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
    `CLAUDE_MCP_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
    // Plan 6 follow-up (2026-05-26): AIFY_MANAGED_DISPATCH used to be
    // hard-set to "1" here to mark legacy managed-codex MCP children
    // as "tool-only, don't autoregister" (server.js IS_MANAGED_DISPATCH
    // gate at line 885). With Plan 5/6 wrapper-backed managed codex,
    // the inner MCP MUST register and claim channel-mode runs. So we
    // now LET IT INHERIT from the wrapper PTY's env — terminal-env.js
    // sets AIFY_MANAGED_DISPATCH="0" for wrapper PTYs, which means the
    // inner MCP runs the normal autoRegisterConfiguredAgent path and
    // claims dispatches. The legacy native-managed codex path (where
    // the bridge connects directly to a codex app-server without a
    // wrapper PTY) doesn't use this config file at all — its env is
    // set per-spawn by createCodexController, which still sets
    // AIFY_MANAGED_DISPATCH="1" via runtimeChildEnv.
  ];
  if (workspace) {
    lines.push("", `[projects.${tomlString(workspace)}]`, 'trust_level = "trusted"');
  }
  if (resolvedModel) {
    lines.unshift(`model = ${tomlString(resolvedModel)}`);
  }
  return `${lines.join("\n")}\n`;
}

export function prepareManagedCodexHome({ workspace = "", model = "", effort = "" } = {}) {
  const sourceHome = process.env.CODEX_HOME || defaultCodexHomePath();
  const targetHome = path.join(userHomeDir(), ".local", "state", "aify-comms", "managed-codex-home");
  fs.mkdirSync(targetHome, { recursive: true });
  for (const name of ["auth.json", "installation_id", "version.json"]) {
    copyIfExists(path.join(sourceHome, name), path.join(targetHome, name));
  }
  installManagedCodexSkills(sourceHome, targetHome);
  fs.writeFileSync(
    path.join(targetHome, "config.toml"),
    managedCodexConfigText({ workspace, serverUrl: process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "", model, effort }),
  );
  return targetHome;
}

export function quoteForDisplay(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function describeCodexItem(item = {}) {
  const type = String(item?.type || item?.kind || "item").trim() || "item";
  const name =
    String(item?.name || item?.toolName || item?.call?.name || item?.function?.name || "").trim();
  const server =
    String(item?.server || item?.serverName || item?.mcpServer || item?.call?.server || "").trim();
  const title = String(item?.title || "").trim();
  const bits = [type];
  const detail = [server, name || title].filter(Boolean).join("/");
  if (detail) bits.push(detail);
  return bits.join(" ");
}

export function isAifyCommsMcpToolItem(label) {
  const text = String(label || "");
  return /mcpToolCall/i.test(text) && /aify-comms/i.test(text);
}

export function isFatalCodexRuntimeLog(line) {
  const text = String(line || "");
  return (
    /worker quit with fatal/i.test(text) ||
    /Transport channel closed/i.test(text) ||
    /Codex WebSocket app-server connection closed/i.test(text)
  );
}

export function buildSystemPrompt(agentId, agentInfo, run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const subject = String(run?.subject || "").trim();
  const isChannelMessage = /^#[-A-Za-z0-9_.]+:/.test(subject);
  const replyParent = String(run?.messageId || run?.inReplyTo || "").trim();
  const replyVerb = replyParent
    ? `comms_send(type="response", inReplyTo="${replyParent}", to="${isDashboardSender ? "dashboard" : fromAgent}")`
    : `comms_send(type="response", to="${isDashboardSender ? "dashboard" : fromAgent}")`;
  const replyRule = isDashboardSender
    ? `The dashboard sender is the human/operator. Reply with ${replyVerb} so it threads into dashboard chat. Your final plain text is your own working output, not the team/chat reply.`
    : run?.requireReply === false
    ? `No required handoff is tracked. If this asks a question, assigns work, names you, or you have useful evidence, reply with ${replyVerb}; otherwise treat it as read context. Final plain text is your working output, not the reply.`
    : `Before you finish, send the reply with ${replyVerb} — that tool call is the team reply and closes the run. Your final plain text is your own working output, not the reply.`;
  const channelRule = isChannelMessage
    ? "This appears to be a channel/group message. Reply in the channel only when you are named, responsible, asked for evidence, or can unblock the group. Otherwise avoid broad automatic acks. Use a direct message for owner-specific follow-up."
    : "";
  return [
    "[AIFY MESSAGE]",
    `This is a message delivered through aify-comms for agent "${agentId}" (${agentInfo.role || "agent"}).`,
    isDashboardSender
      ? "This run was started by the dashboard human/operator. Reply to it with a comms_send tool call (see reply rule below); your final plain text is your own working output."
      : "This is a managed background run delivered through aify-comms. Reply to it with a comms_send tool call (see reply rule below) — that is the team-visible reply; your final plain text is your own working output, not the reply.",
    `Your aify-comms agentId is "${agentId}". Use that exact ID when checking your own inbox or conversation state.`,
    `From: ${run.from}.`,
    replyParent ? `MessageId: ${replyParent}. Use this exact value as inReplyTo when you reply with comms_send so your answer threads to this message and closes the run.` : "",
    agentInfo.instructions ? `Standing instructions: ${agentInfo.instructions}` : "",
    "Treat the content below as a message from the sender. If it contains a work request, that work is now pending in this session. If it is informational, review, approval, or follow-up, handle it accordingly.",
    `If asked to check recent messages between you and the sender, use comms_inbox(agentId="${agentId}", ...) or the relevant direct-chat context, not the global dashboard feed.`,
    "Team communication contract: stay on the current message, treat it as a small contract, and do not mix unrelated topics. Identify the owner, expected answer/action, evidence/result needed, and any follow-up wake owed. If status/history/truth matters, inspect messages/files/tools first and say what you checked.",
    "Managed visibility rule: stdout, logs, tool output, final plain text, and run summaries are YOUR working output / telemetry, not the team-visible answer. The team-visible answer is the comms_send reply you send. If you ask teammates for parallel work, name the expected reply target and completion condition.",
    "Keep the comms_send reply compact: answer, evidence checked, blocker or uncertainty, next action. Ask one clear question when blocked instead of guessing.",
    `Turn lifecycle: replying via comms_send is this turn's reply; it does not schedule future work. This is not a lockstep protocol: you may message teammates mid-turn, run parallel lanes, and continue your own bounded work inside the current turn. If future work must happen after this turn, create that wake before finishing. If your next action requires another agent, send that agent a separate comms_send. If your next action is your own next chunk after this turn, send yourself a separate comms_send(to="${agentId}", type="request", queueIfBusy=true, ...). Do not merely write "Next action: ..." unless no wake is needed.`,
    channelRule,
    replyRule,
    "Do not explain the transport wrapper or restate it unless a later normal user turn explicitly asks about it.",
    "[/AIFY MESSAGE]",
  ].filter(Boolean).join("\n");
}

export function buildUserPrompt(run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const subject = String(run?.subject || "").trim();
  const isChannelMessage = /^#[-A-Za-z0-9_.]+:/.test(subject);
  const replyParent = String(run?.messageId || run?.inReplyTo || "").trim();
  const replyTo = isDashboardSender ? "dashboard" : fromAgent;
  const replyVerb = replyParent
    ? `comms_send(type="response", inReplyTo="${replyParent}", to="${replyTo}")`
    : `comms_send(type="response", to="${replyTo}")`;
  const replyRule = isDashboardSender
    ? `Reply to the dashboard user with ${replyVerb}.`
    : run?.requireReply === false
    ? `If this asks a question, assigns you work, names you, or you have useful evidence, reply with ${replyVerb}; otherwise keep it as read context.`
    : `Required handoff: reply with ${replyVerb} before you finish.`;
  const context = formatConversationContext(run?.conversationContext || []);
  return [
    context,
    "[MESSAGE]",
    `Type: ${run.type || "request"}`,
    `Subject: ${run.subject}`,
    replyParent ? `MessageId: ${replyParent}` : "",
    "",
    run.body || "",
    "",
    "Reply delivery: send your answer as a comms_send tool call (rule below). Your final plain text / stdout is your own working output, not the delivered reply.",
    replyRule,
    isChannelMessage
      ? "Channel discipline: respond only when your reply is useful to the group or sender. Do not create broad acknowledgement loops."
      : "",
    "Keep this turn scoped to the message above and its direct context. Do not carry unrelated older topics forward unless the sender explicitly asks for them.",
    "Do not end silently. Answer the sender with comms_send (rule above). If you owe a separate update or future wake, create it with comms_send too.",
    "Parallel coordination is allowed. Self-continuation is allowed: send yourself a request with queueIfBusy=true. A written 'next action' in final text is not a wake.",
    isDashboardSender
      ? "Keep the final answer brief and directly useful."
      : "Keep the final answer compact: answer, evidence checked, blocker or uncertainty, next action.",
    "[/MESSAGE]",
  ].filter(Boolean).join("\n");
}

function formatConversationContext(messages = []) {
  if (!Array.isArray(messages) || !messages.length) return "";
  const maxMessages = 8;
  const maxBodyChars = 700;
  const lines = ["[RECENT DIRECT CONVERSATION]", "Recent direct messages between you and the sender, oldest first. Use only what is relevant to the new message; do not revive unrelated topics."];
  for (const message of messages.slice(-maxMessages)) {
    const from = String(message?.from || "").trim() || "unknown";
    const type = String(message?.type || "info").trim() || "info";
    const subject = String(message?.subject || "").trim();
    const body = String(message?.body || message?.preview || "").trim();
    const timestamp = String(message?.timestamp || "").trim();
    lines.push(`- ${timestamp ? `${timestamp} ` : ""}${from} (${type})${subject ? `: ${subject}` : ""}`);
    if (body) lines.push(body.length > maxBodyChars ? `${body.slice(0, maxBodyChars)}...` : body);
  }
  lines.push("[/RECENT DIRECT CONVERSATION]", "");
  return lines.join("\n");
}

export function splitProviderModel(value) {
  const text = String(value || "").trim();
  if (!text || !text.includes("/")) return null;
  const [providerID, ...modelParts] = text.split("/");
  const modelID = modelParts.join("/").trim();
  if (!providerID || !modelID) return null;
  return { providerID: providerID.trim(), modelID };
}

export function opencodePermissionConfig(config = {}, executionMode = "managed") {
  if (config.permission && typeof config.permission === "object") {
    return config.permission;
  }
  const policy = String(config.approvalPolicy || "").trim().toLowerCase();
  if (policy === "never" || policy === "auto") {
    return { bash: "allow", edit: "allow", webfetch: "allow" };
  }
  if (policy === "ask") {
    return { bash: "ask", edit: "ask", webfetch: "ask" };
  }
  if (executionMode !== "resident") {
    return { bash: "allow", edit: "allow", webfetch: "allow" };
  }
  return undefined;
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

export function managedCodexEffort(config = {}) {
  return String(config.effort || "high").trim();
}

function normalizeCodexSandboxMode(value) {
  const text = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  if (["danger", "danger-full", "danger-full-access", "full", "full-access", "bypass", "unsafe"].includes(text)) {
    return "danger-full-access";
  }
  if (["workspace", "workspace-write", "workspacewrite"].includes(text)) {
    return "workspace-write";
  }
  if (["read", "read-only", "readonly"].includes(text)) {
    return "read-only";
  }
  return "";
}

export function managedCodexSandboxMode(config = {}, executionMode = "managed") {
  const configured = normalizeCodexSandboxMode(config.sandboxMode || config.sandbox || config.codexSandboxMode);
  if (configured) return configured;
  return executionMode === "managed" ? "danger-full-access" : "workspace-write";
}

export function codexTurnSandboxPolicy(mode, cwd, networkAccess = true) {
  const sandboxMode = normalizeCodexSandboxMode(mode) || "workspace-write";
  if (sandboxMode === "danger-full-access") {
    return { type: "dangerFullAccess" };
  }
  if (sandboxMode === "read-only") {
    return { type: "readOnly" };
  }
  return {
    type: "workspaceWrite",
    writableRoots: [cwd],
    networkAccess,
  };
}

export function summarizeOpenCodeParts(parts = []) {
  const textChunks = [];
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      textChunks.push(String(part.text));
    }
  }
  return textChunks.join("").trim();
}

export function extractPiAssistantText(value) {
  const messages = Array.isArray(value) ? value : [value];
  const chunks = [];
  for (const message of messages) {
    if (!message || String(message.role || "").toLowerCase() !== "assistant") continue;
    const content = message.content;
    if (typeof content === "string") {
      chunks.push(content);
      continue;
    }
    if (!Array.isArray(content)) continue;
    for (const part of content) {
      if (!part || typeof part !== "object") continue;
      const type = String(part.type || "").toLowerCase();
      if (type === "text" && typeof part.text === "string") chunks.push(part.text);
    }
  }
  return chunks.join("\n").trim();
}

export function extractPiSessionState(value) {
  const source = value && typeof value === "object" ? value : {};
  const data = source.data && typeof source.data === "object" ? source.data : {};
  const session = data.session && typeof data.session === "object"
    ? data.session
    : (source.session && typeof source.session === "object" ? source.session : {});
  const sessionId = String(
    data.sessionId ||
    data.sessionID ||
    source.sessionId ||
    source.sessionID ||
    session.sessionId ||
    session.sessionID ||
    session.id ||
    "",
  ).trim();
  const sessionFile = String(
    data.sessionFile ||
    data.sessionPath ||
    source.sessionFile ||
    source.sessionPath ||
    session.file ||
    session.path ||
    "",
  ).trim();
  return { sessionId, sessionFile };
}

const PI_MODEL_PLACEHOLDER_VALUES = new Set(["default", "unknown", "auto"]);
export function normalizePiModelOverride(value) {
  const text = String(value || "").trim();
  return PI_MODEL_PLACEHOLDER_VALUES.has(text.toLowerCase()) ? "" : text;
}

export const RUNTIME_SESSION_ENV_VARS = Object.freeze({
  "claude-code": ["CLAUDE_SESSION_ID"],
  codex: ["CODEX_THREAD_ID"],
  hermes: ["HERMES_SESSION_ID", "HERMES_SESSION"],
  opencode: ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"],
  pi: ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"],
});

export function sessionEnvVarsForRuntime(runtime) {
  return RUNTIME_SESSION_ENV_VARS[normalizeRuntime(runtime)] || [];
}

export function runtimeStateWithoutSessionHandle(runtime, runtimeState = {}) {
  const next = { ...(runtimeState || {}) };
  const key = normalizeRuntime(runtime);
  if (key === "codex") {
    delete next.threadId;
    return next;
  }
  delete next.sessionId;
  if (key === "pi") delete next.sessionFile;
  return next;
}


const SHELL_TOKEN_PATTERN = String.raw`(?:"([^"]*)"|'([^']*)'|(\S+))`;

function unquoteShellToken(value = "") {
  const text = String(value || "").trim();
  if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1);
  }
  return text;
}

function shellTokenFromMatch(match) {
  return unquoteShellToken(match?.[1] || match?.[2] || match?.[3] || "");
}

function resumeRegexForRuntime(runtime, flags = "") {
  const key = normalizeRuntime(runtime);
  if (key === "codex") {
    return new RegExp(String.raw`(?:^|\s)resume(?:\s+--include-non-interactive)?\s+${SHELL_TOKEN_PATTERN}`, flags);
  }
  if (key === "pi" || key === "hermes" || key === "claude-code") {
    return new RegExp(String.raw`(?:^|\s)(?:--resume|--session-id|-r)(?:=|\s+)${SHELL_TOKEN_PATTERN}`, flags);
  }
  return null;
}

export function extractRuntimeSessionHandleFromCommand(runtime = "", command = "") {
  const regex = resumeRegexForRuntime(runtime);
  if (!regex) return "";
  return shellTokenFromMatch(String(command || "").match(regex));
}

export function runtimeCommandWithoutResume(runtime = "", command = "") {
  const regex = resumeRegexForRuntime(runtime, "g");
  if (!regex) return String(command || "").trim();
  return String(command || "").trim().replace(regex, " ").replace(/\s+/g, " ").trim();
}



export function detectPiRuntimeFailure(value) {
  const message = String(value?.message || value || "").replace(/\s+/g, " ").trim();
  const lower = message.toLowerCase();
  if (!lower) return { shouldHeal: false, authFailure: false, fatalRuntime: false, missingSession: false, healReason: null, message };
  const fatalRuntime =
    /fatal error/.test(lower) ||
    /javascript heap out of memory/.test(lower) ||
    /allocation failed/.test(lower) ||
    /\bepipe\b/.test(lower);
  if (fatalRuntime) {
    return { shouldHeal: false, authFailure: false, fatalRuntime: true, missingSession: false, healReason: null, message };
  }
  const authFailure =
    /no api key/.test(lower) ||
    /api key (?:not found|missing|required)/.test(lower) ||
    /not authenticated|authentication (?:failed|required)|unauthori[sz]ed|\b401\b/.test(lower) ||
    ((/amazon-bedrock|bedrock/.test(lower)) && /login|auth|credential|api key/.test(lower));
  if (authFailure) {
    return { shouldHeal: false, authFailure: true, fatalRuntime: false, missingSession: false, healReason: null, message };
  }
  const missingSession =
    /session\s+["']?[^"'\s]+["']?\s+(?:not found|does not exist|missing)/i.test(message) ||
    /no such session/i.test(message);
  if (missingSession) {
    return { shouldHeal: true, authFailure: false, fatalRuntime: false, missingSession: true, healReason: "missing_session", message };
  }
  const projectMismatch =
    /session\s+["']?[^"'\s]+["']?\s+is in another project/i.test(message);
  if (projectMismatch) {
    return { shouldHeal: true, authFailure: false, fatalRuntime: false, missingSession: true, healReason: "project_mismatch", message };
  }
  return { shouldHeal: false, authFailure: false, fatalRuntime: false, missingSession: false, healReason: null, message };
}


export function requireOpenCodeData(response, fallbackMessage) {
  if (response?.data) return response.data;
  const errorMessage =
    response?.error?.data?.message ||
    response?.error?.message ||
    fallbackMessage;
  throw new Error(errorMessage);
}

export function defaultCodexCommand() {
  // Test/operator override: full command line incl. args. Quote-aware so
  // paths with spaces survive (fix I7 — '"C:\Program Files\codex\codex.exe" app-server').
  const override = String(process.env.AIFY_CODEX_COMMAND || "").trim();
  if (override) {
    return tokenizeCommandString(override);
  }
  if (process.platform === "win32") {
    const systemRoot = process.env.SystemRoot || "C:\\Windows";
    return { command: `${systemRoot}\\System32\\wsl.exe`, args: ["-e", "codex", "app-server"] };
  }
  // Resolve to absolute path so spawn doesn't depend on the bridge process
  // inheriting an interactive shell's PATH (see defaultClaudeCommand notes).
  const resolved = resolveExecutable("codex");
  return { command: resolved || "codex", args: ["app-server"] };
}

function isWslCodexLauncher(launcher) {
  if (process.platform !== "win32") return false;
  const command = String(launcher?.command || "").toLowerCase().replace(/\\/g, "/");
  return command.endsWith("/wsl.exe") || command === "wsl.exe";
}

function toWslPath(inputPath) {
  const value = String(inputPath || "").trim();
  if (!value) return value;
  const normalized = value.replace(/\\/g, "/");
  const match = normalized.match(/^([A-Za-z]):\/(.*)$/);
  if (!match) return normalized;
  const drive = match[1].toLowerCase();
  const rest = match[2];
  return `/mnt/${drive}/${rest}`;
}

function codexWorkingPath(launcher, cwd) {
  if (!isWslCodexLauncher(launcher)) {
    // Codex's Rust path deserializer rejects Windows-style backslash paths
    // ("AbsolutePathBuf deserialized without a base path"). Normalize to
    // forward slashes, which Codex accepts on both Windows and Linux.
    return String(cwd || "").replace(/\\/g, "/");
  }
  return toWslPath(cwd);
}

export function resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl }) {
  return resolveCodexRequestCwdFor({
    hostCwd,
    appServerUrl,
    legacyTransform: (raw) => codexWorkingPath(launcher, raw),
  });
}

export function codexSpawnCwd(launcher, cwd) {
  if (!isWslCodexLauncher(launcher)) return cwd;
  return process.env.USERPROFILE || process.env.HOMEDRIVE && process.env.HOMEPATH
    ? `${process.env.HOMEDRIVE || "C:"}${process.env.HOMEPATH || "\\Users\\Default"}`
    : "C:\\";
}

function bashShebangFallback(absPath) {
  // Wrap the script in `bash -lic 'exec "$0" "$@"'` so the shell sources
  // both .profile (login: -l) AND .bashrc (interactive: -i) before exec'ing
  // the script. Nvm's installer adds its init to .bashrc by default — a
  // plain `bash -l` would miss it and the broken-shebang problem would
  // recur. Using exec preserves stdin/stdout/stderr semantics for Node.
  return {
    command: "bash",
    args: ["-lic", `exec "$0" "$@"`, absPath],
  };
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

export function defaultPiCommand() {
  const configured = String(process.env.AIFY_PI_COMMAND || process.env.PI_COMMAND || "").trim();
  if (process.platform === "win32") {
    return { command: configured || "omp", args: [] };
  }
  const target = configured || "omp";
  const resolved = resolveExecutable(target);
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      return bashShebangFallback(resolved);
    }
    return { command: resolved, args: [] };
  }
  return { command: target, args: [] };
}

// Common Hermes Agent install locations to probe when PATH lookup fails.
// Upstream installer (NousResearch/hermes-agent/scripts/install.ps1)
// drops Hermes into a per-user venv under AppData on Windows, which is
// NOT on the system PATH — only the User PATH env var, which child
// processes inherit only at process-spawn time. A bridge launched from
// a shell that predates the install never sees it. Probing absolute
// paths is the operator-friendly fallback so the bridge "just works"
// without requiring a setx + bridge-restart-from-fresh-shell dance.
function hermesProbePaths() {
  if (process.platform === "win32") {
    const userProfile = String(process.env.USERPROFILE || "").trim();
    if (!userProfile) return [];
    return [
      `${userProfile}\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\hermes.exe`,
      `${userProfile}\\.local\\bin\\hermes.exe`,
    ];
  }
  const home = String(process.env.HOME || "").trim();
  if (!home) return [];
  return [
    `${home}/.local/bin/hermes`,
    `${home}/.local/share/hermes/hermes-agent/venv/bin/hermes`,
  ];
}

export function defaultHermesCommand() {
  const configured = String(process.env.AIFY_HERMES_COMMAND || process.env.HERMES_COMMAND || "").trim();
  if (process.platform === "win32") {
    if (configured) return { command: configured, args: [] };
    if (hasExecutable("hermes")) return { command: "hermes", args: [] };
    for (const candidate of hermesProbePaths()) {
      try {
        if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
          return { command: candidate, args: [] };
        }
      } catch {
        // best-effort probe; ignore stat errors
      }
    }
    return { command: "hermes", args: [] };
  }
  const target = configured || "hermes";
  const resolved = resolveExecutable(target);
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      return bashShebangFallback(resolved);
    }
    return { command: resolved, args: [] };
  }
  for (const candidate of hermesProbePaths()) {
    try {
      if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return { command: candidate, args: [] };
      }
    } catch {
      // best-effort
    }
  }
  return { command: target, args: [] };
}

const RESOLVED_EXECUTABLE_CACHE = new Map();
const EXECUTABLE_RESOLUTION_LOG = new Map();

function isReallyExecutable(absPath) {
  if (!absPath || !/[\\/]/.test(absPath)) return false;
  try {
    const st = fs.statSync(absPath);
    if (!st.isFile()) return false;
    if (process.platform !== "win32") {
      // Check exec bit for the current user
      fs.accessSync(absPath, fs.constants.X_OK);
    }
    return true;
  } catch {
    return false;
  }
}

// Walks the bridge process's own PATH (the one the kernel will use when
// invoking /usr/bin/env <name>) and returns the absolute path to the first
// executable match. Does NOT spawn a shell — must match kernel semantics.
function findOnProcessPath(name) {
  if (!name || /[\\/]/.test(name)) {
    return isReallyExecutable(name) ? name : null;
  }
  const PATH = String(process.env.PATH || "");
  const sep = process.platform === "win32" ? ";" : ":";
  const exts = process.platform === "win32"
    ? String(process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";").filter(Boolean)
    : [""];
  for (const dir of PATH.split(sep)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + ext);
      if (isReallyExecutable(candidate)) return candidate;
    }
  }
  return null;
}

// Inspects a script's #! line. Returns { interpreter, args, valid, missing }
// or null if the file is not a script. valid=false means we can prove the
// interpreter is unreachable from THIS PROCESS's PATH (which is what the
// kernel will use for /usr/bin/env <name>); missing carries the offending
// interpreter name so error messages can be specific.
function inspectShebang(absPath) {
  if (process.platform === "win32") return null;
  try {
    const fd = fs.openSync(absPath, "r");
    try {
      const buf = Buffer.alloc(512);
      const bytes = fs.readSync(fd, buf, 0, 512, 0);
      const text = buf.slice(0, bytes).toString("utf-8");
      if (!text.startsWith("#!")) return null;
      const firstLine = text.split(/\r?\n/, 1)[0].slice(2).trim();
      if (!firstLine) return null;
      const tokens = firstLine.split(/\s+/);
      const interpreter = tokens.shift();
      const args = tokens;
      let valid = false;
      let missing = null;
      if (interpreter === "/usr/bin/env" || interpreter === "/bin/env") {
        if (!fs.existsSync(interpreter)) {
          missing = interpreter;
        } else if (args.length === 0) {
          valid = true;
        } else {
          const target = args[0];
          // CRITICAL: validate against process.env.PATH (kernel-level
          // semantics), NOT against an interactive shell. A `sh -lc command
          // -v node` may succeed because the shell sources .bashrc/.profile,
          // but the kernel's execve of /usr/bin/env will only see the
          // bridge process's PATH. These two routinely disagree for
          // nvm/asdf/fnm setups.
          if (/[\\/]/.test(target)) {
            valid = isReallyExecutable(target);
            if (!valid) missing = target;
          } else {
            const onProc = findOnProcessPath(target);
            valid = onProc !== null;
            if (!valid) missing = target;
          }
        }
      } else {
        valid = isReallyExecutable(interpreter);
        if (!valid) missing = interpreter;
      }
      return { interpreter, args, valid, missing, line: firstLine };
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
}

function resolveExecutable(command) {
  const value = String(command || "").trim();
  if (!value) return null;
  if (/[\\/]/.test(value)) {
    return isReallyExecutable(value) ? value : null;
  }
  if (RESOLVED_EXECUTABLE_CACHE.has(value)) {
    return RESOLVED_EXECUTABLE_CACHE.get(value);
  }
  let resolved = null;
  const attempts = [];
  try {
    if (process.platform === "win32") {
      const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
      const result = spawnSync(comspec, ["/d", "/s", "/c", `where ${value}`], {
        windowsHide: true,
        timeout: 3000,
        encoding: "utf-8",
      });
      attempts.push({ method: "where", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      if (result.status === 0) {
        const lines = String(result.stdout || "").split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        if (lines.length) resolved = lines[0];
      }
    } else {
      const quoted = value.replace(/'/g, "'\\''");
      // Try login shell first (sources .profile so npm-global etc. resolve)
      let result = spawnSync("sh", ["-lc", `command -v '${quoted}' 2>/dev/null`], {
        timeout: 3000,
        encoding: "utf-8",
      });
      attempts.push({ method: "sh -lc command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      if (result.status !== 0 || !String(result.stdout || "").trim()) {
        // Non-login fallback uses the current process's PATH directly
        result = spawnSync("sh", ["-c", `command -v '${quoted}' 2>/dev/null`], {
          timeout: 3000,
          encoding: "utf-8",
        });
        attempts.push({ method: "sh -c command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      }
      // Last-ditch: an interactive bash that sources .bashrc (nvm puts its
      // shim init in .bashrc, not .profile, so login-shell sh doesn't see it)
      if (result.status !== 0 || !String(result.stdout || "").trim()) {
        result = spawnSync("bash", ["-ic", `command -v '${quoted}' 2>/dev/null`], {
          timeout: 3000,
          encoding: "utf-8",
          env: process.env,
        });
        attempts.push({ method: "bash -ic command -v", status: result.status, stdout: (result.stdout || "").trim().slice(0, 400) });
      }
      const out = String(result.stdout || "").trim();
      if (result.status === 0 && out) resolved = out;
    }
  } catch (err) {
    attempts.push({ method: "exception", error: err?.message || String(err) });
  }
  // Verify the resolved path is something Node can actually spawn. A common
  // failure mode: `command -v claude` returns "claude" (a shell function) or
  // a path that exists but lacks the exec bit for the current user.
  if (resolved && !isReallyExecutable(resolved)) {
    attempts.push({ method: "stat-check", rejected: resolved, reason: "not a real executable file" });
    resolved = null;
  }
  // Second failure mode: the file exists with the exec bit but its shebang
  // line points at an interpreter the kernel can't reach (e.g., a stale
  // /home/.../node path from an uninstalled nvm version, or `#!/usr/bin/env
  // node` on a system where node isn't on the bridge's PATH). execve will
  // return ENOENT against the SCRIPT, not the interpreter, which is what
  // produces the confusing "spawn /home/.../claude ENOENT" message.
  if (resolved) {
    const shebang = inspectShebang(resolved);
    if (shebang && !shebang.valid) {
      attempts.push({
        method: "shebang-check",
        rejected: resolved,
        reason: `shebang interpreter "${shebang.missing}" is not reachable from this bridge (shebang: #!${shebang.line})`,
      });
      // Don't null out resolved — the user may want to set
      // AIFY_CLAUDE_COMMAND to a different wrapper. But surface the problem
      // in the resolution log so runtimeLaunchAvailability can report it.
    }
  }
  RESOLVED_EXECUTABLE_CACHE.set(value, resolved);
  EXECUTABLE_RESOLUTION_LOG.set(value, { resolved, attempts });
  return resolved;
}

export function describeExecutableResolution(command) {
  const value = String(command || "").trim();
  if (!value) return { resolved: null, attempts: [] };
  if (!EXECUTABLE_RESOLUTION_LOG.has(value)) resolveExecutable(value);
  return EXECUTABLE_RESOLUTION_LOG.get(value) || { resolved: null, attempts: [] };
}

function hasExecutable(command) {
  return resolveExecutable(command) !== null;
}

function staleClaudeAifyWrapperReason(resolved) {
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

function pathSummary() {
  const value = String(process.env.PATH || "").trim();
  if (!value) return "(empty)";
  const parts = value.split(path.delimiter).filter(Boolean);
  return parts.length > 6 ? `${parts.length} entries; head: ${parts.slice(0, 6).join(path.delimiter)} ...` : value;
}

// Read the build tag the same way server.js does, so error messages stamp
// the running bridge's git SHA. This lets users prove which code emitted
// the error without grep'ing the source.
function readBuildTag() {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const gitDir = path.resolve(here, "..", "..", ".git");
    const headPath = path.join(gitDir, "HEAD");
    if (!fs.existsSync(headPath)) return "no-git";
    const head = fs.readFileSync(headPath, "utf-8").trim();
    if (head.startsWith("ref:")) {
      const refPath = path.join(gitDir, head.slice(4).trim());
      if (fs.existsSync(refPath)) return fs.readFileSync(refPath, "utf-8").trim().slice(0, 12);
      const packed = path.join(gitDir, "packed-refs");
      if (fs.existsSync(packed)) {
        const refName = head.slice(4).trim();
        for (const line of fs.readFileSync(packed, "utf-8").split(/\r?\n/)) {
          if (line.endsWith(refName)) return line.split(/\s+/)[0].slice(0, 12);
        }
      }
      return "unknown-ref";
    }
    return head.slice(0, 12);
  } catch {
    return "unknown";
  }
}
const BRIDGE_BUILD_TAG = readBuildTag();

export function diagnosticsFor(name) {
  const info = describeExecutableResolution(name);
  const tried = (info.attempts || []).map(a => {
    const tag = a.method;
    if (a.rejected) return `[rejected ${a.rejected}: ${a.reason}]`;
    if (a.error) return `[${tag}: error ${a.error}]`;
    return `[${tag}: status=${a.status}${a.stdout ? ` stdout="${a.stdout}"` : ""}]`;
  }).join(" ");
  return `bridge build=${BRIDGE_BUILD_TAG} pid=${process.pid} script=${fileURLToPath(import.meta.url)}; attempts: ${tried || "(none)"}; bridge PATH: ${pathSummary()}`;
}

export function runtimeLaunchAvailability(runtime) {
  const normalized = normalizeRuntime(runtime);
  if (normalized === "claude-code") {
    const configured = String(process.env.AIFY_CLAUDE_COMMAND || process.env.CLAUDE_COMMAND || "").trim();
    const expected = configured || "claude-aify";
    const resolved = resolveExecutable(expected);
    const staleReason = staleClaudeAifyWrapperReason(resolved);
    const available = Boolean(resolved) && !staleReason;
    return {
      available,
      message: available
        ? `Claude Code aify wrapper available (resolved to ${resolved})`
        : `Runtime "claude-code" is not launchable from this bridge because "${expected}" ${staleReason ? `is stale: ${staleReason}.` : "could not be resolved to a real executable."} ` +
          `Install/update the aify Claude wrapper with install.sh, ensure raw Claude Code is installed for this OS/user, ` +
          `or set AIFY_CLAUDE_COMMAND to an absolute claude-aify-compatible wrapper path and restart the bridge. ` +
          `Diagnostic: ${diagnosticsFor(expected)}`,
    };
  }
  if (normalized === "codex") {
    const expected = String(process.env.AIFY_CODEX_AIFY_COMMAND || "").trim() || "codex-aify";
    const resolved = resolveExecutable(expected);
    const available = Boolean(resolved);
    return {
      available,
      message: available
        ? `Codex aify wrapper available (resolved to ${resolved})`
        : `Runtime "codex" is not launchable from this bridge because the required wrapper "${expected}" is not available. ` +
          `Install/update with install.sh --client codex, ensure raw Codex is installed for this OS/user, or set AIFY_CODEX_AIFY_COMMAND to an absolute codex-aify-compatible wrapper path and restart the bridge. ` +
          `Diagnostic: ${diagnosticsFor(expected)}`,
    };
  }
  if (normalized === "hermes") {
    const expected = String(process.env.AIFY_HERMES_AIFY_COMMAND || "").trim() || "hermes-aify";
    const resolved = resolveExecutable(expected);
    const available = Boolean(resolved);
    return {
      available,
      message: available
        ? `Hermes aify wrapper available (resolved to ${resolved})`
        : `Runtime "hermes" is not launchable from this bridge because the required wrapper "${expected}" is not available. ` +
          `Install/update with install.sh --client hermes, ensure Hermes Agent is installed for this OS/user, or set AIFY_HERMES_AIFY_COMMAND to an absolute hermes-aify-compatible wrapper path and restart the bridge. ` +
          `Diagnostic: ${diagnosticsFor(expected)}`,
    };
  }
  if (normalized === "opencode") {
    return { available: true, message: "OpenCode SDK available" };
  }
  if (normalized === "pi") {
    const launcher = defaultPiCommand();
    const available = hasExecutable(launcher.command);
    return {
      available,
      message: available
        ? `Pi launcher available (${launcher.command})`
        : `Runtime "pi" is not launchable from this bridge because "${launcher.command}" is not on PATH. ` +
          `Install Oh My Pi for this OS/user or restart the bridge from a shell where "${launcher.command}" works. ` +
          `Diagnostic: ${diagnosticsFor(launcher.command)}`,
    };
  }
  return { available: false, message: `Runtime "${normalized}" is not launchable from this bridge.` };
}

export function getRuntimeConfig(agentInfo) {
  return agentInfo.runtimeConfig || {};
}

export function hasCodexLiveAppServer(runtimeConfig = {}) {
  const url = String(runtimeConfig?.appServerUrl || "").trim();
  return /^wss?:\/\//i.test(url);
}

export function normalizeRuntime(runtime) {
  const key = String(runtime || "generic").trim().toLowerCase();
  return RUNTIME_ALIASES.get(key) || key || "generic";
}

export function canLaunchRuntime(runtime) {
  return ["claude-code", "codex", "hermes", "opencode", "pi"].includes(normalizeRuntime(runtime));
}

export function controlCapabilitiesForRuntime(runtime) {
  const runtimeN = normalizeRuntime(runtime || "");
  try {
    const a = adapterFor(runtimeN);
    return { interrupt: a.supportsInterrupt, steer: a.supportsSteering };
  } catch {
    return { interrupt: false, steer: false };
  }
}

export function defaultSessionHandleForRuntime(runtime) {
  for (const name of sessionEnvVarsForRuntime(runtime)) {
    const value = String(process.env[name] || "").trim();
    if (value) return value;
  }
  return "";
}

export function createRpcClient(proc, { onNotification, onStderr } = {}) {
  const pending = new Map();
  let nextId = 1;
  let processError = null;
  // Mutable notification handler so a pooled RPC (CodexSession) can swap
  // it per turn without rebuilding the client. Defaults to the
  // constructor-time `onNotification`; null disables forwarding.
  let activeNotificationHandler = onNotification || null;
  let activeStderrHandler = onStderr || null;

  function failPending(error) {
    for (const [id, pendingRequest] of pending.entries()) {
      pending.delete(id);
      pendingRequest.reject(error);
    }
  }

  proc.on("error", (error) => {
    processError = error instanceof Error ? error : new Error(String(error));
    failPending(processError);
    if (activeStderrHandler) activeStderrHandler(processError.message || String(processError));
  });

  const stdout = readline.createInterface({ input: proc.stdout });
  stdout.on("line", (line) => {
    const text = line.trim();
    if (!text) return;
    let message;
    try {
      message = JSON.parse(text);
    } catch {
      return;
    }

    if (Object.prototype.hasOwnProperty.call(message, "id")) {
      const pendingRequest = pending.get(message.id);
      if (!pendingRequest) return;
      pending.delete(message.id);
      if (message.error) pendingRequest.reject(new Error(message.error.message || JSON.stringify(message.error)));
      else pendingRequest.resolve(message.result);
      return;
    }

    if (message.method && activeNotificationHandler) {
      activeNotificationHandler(message);
    }
  });

  const stderr = readline.createInterface({ input: proc.stderr });
  stderr.on("line", (line) => {
    if (activeStderrHandler) activeStderrHandler(line);
  });

  function send(payload) {
    proc.stdin.write(`${JSON.stringify(payload)}\n`);
  }

  function request(method, params, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      if (processError) {
        reject(processError);
        return;
      }
      const id = nextId++;
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      pending.set(id, {
        resolve: (result) => {
          clearTimeout(timer);
          resolve(result);
        },
        reject: (error) => {
          clearTimeout(timer);
          reject(error);
        },
      });
      send({ jsonrpc: "2.0", id, method, params });
    });
  }

  function notify(method, params) {
    send({ jsonrpc: "2.0", method, params });
  }

  function setOnNotification(handler) {
    activeNotificationHandler = typeof handler === "function" ? handler : null;
  }

  function setOnStderr(handler) {
    activeStderrHandler = typeof handler === "function" ? handler : null;
  }

  function close() {
    failPending(new Error("rpc client closed"));
    activeNotificationHandler = null;
    activeStderrHandler = null;
    try { stdout.close(); } catch {}
    try { stderr.close(); } catch {}
    try { proc.stdin?.end?.(); } catch {}
    try { proc.stdout?.destroy?.(); } catch {}
    try { proc.stderr?.destroy?.(); } catch {}
  }

  return { request, notify, setOnNotification, setOnStderr, close };
}

export function createWebSocketRpcClient(url, { token, onNotification, onStderr } = {}) {
  return new Promise((resolve, reject) => {
    const pending = new Map();
    let nextId = 1;
    let opened = false;
    let closed = false;

    let activeNotificationHandler = onNotification || null;
    let activeStderrHandler = onStderr || null;
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const socket = new WebSocket(url, Object.keys(headers).length ? { headers } : undefined);

    function failPending(error) {
      for (const [id, pendingRequest] of pending.entries()) {
        pending.delete(id);
        pendingRequest.reject(error);
      }
    }

    function onSocketFailure(error) {
      if (!closed) {
        closed = true;
        failPending(error);
      }
      if (!opened) {
        reject(error);
      } else if (onStderr) {
        onStderr(error.message || String(error));
      }
    }

    socket.on("open", () => {
      opened = true;

      function send(payload) {
        if (socket.readyState !== WebSocket.OPEN) {
          throw new Error("Codex WebSocket app-server connection is not open");
        }
        socket.send(JSON.stringify(payload));
      }

      function request(method, params, timeoutMs = 30000) {
        return new Promise((resolveRequest, rejectRequest) => {
          if (socket.readyState !== WebSocket.OPEN) {
            rejectRequest(new Error("Codex WebSocket app-server connection is not open"));
            return;
          }

          const id = nextId++;
          const timer = setTimeout(() => {
            pending.delete(id);
            rejectRequest(new Error(`${method} timed out after ${timeoutMs}ms`));
          }, timeoutMs);

          pending.set(id, {
            resolve: (result) => {
              clearTimeout(timer);
              resolveRequest(result);
            },
            reject: (error) => {
              clearTimeout(timer);
              rejectRequest(error);
            },
          });

          send({ jsonrpc: "2.0", id, method, params });
        });
      }

      function notify(method, params) {
        send({ jsonrpc: "2.0", method, params });
      }

      function close() {
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close();
        }
        activeNotificationHandler = null;
        activeStderrHandler = null;
      }

      function setOnNotification(handler) {
        activeNotificationHandler = typeof handler === "function" ? handler : null;
      }

      function setOnStderr(handler) {
        activeStderrHandler = typeof handler === "function" ? handler : null;
      }

      resolve({ request, notify, close, setOnNotification, setOnStderr });
    });

    socket.on("message", (data) => {
      let message;
      try {
        message = JSON.parse(String(data));
      } catch {
        return;
      }

      if (Object.prototype.hasOwnProperty.call(message, "id")) {
        const pendingRequest = pending.get(message.id);
        if (!pendingRequest) return;
        pending.delete(message.id);
        if (message.error) pendingRequest.reject(new Error(message.error.message || JSON.stringify(message.error)));
        else pendingRequest.resolve(message.result);
        return;
      }

      if (message.method && activeNotificationHandler) {
        activeNotificationHandler(message);
      }
    });

    socket.on("error", (error) => {
      onSocketFailure(error instanceof Error ? error : new Error(String(error)));
    });

    socket.on("close", (code, reasonBuffer) => {
      const reasonText = quoteForDisplay(
        Buffer.isBuffer(reasonBuffer) ? reasonBuffer.toString("utf-8") : String(reasonBuffer || ""),
      );
      const detail = reasonText || `Codex WebSocket app-server connection closed (${code})`;
      onSocketFailure(new Error(detail));
    });
  });
}

export function codexAppServerReachable(url, { token, timeoutMs = 1200 } = {}) {
  const target = String(url || "").trim();
  if (!target) return Promise.resolve(false);
  return new Promise((resolve) => {
    let settled = false;
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    let socket;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
          socket.close();
        }
      } catch {
        // best effort
      }
      resolve(Boolean(ok));
    };
    const timer = setTimeout(() => finish(false), Math.max(250, Number(timeoutMs) || 1200));
    try {
      socket = new WebSocket(target, Object.keys(headers).length ? { headers } : undefined);
      socket.on("open", () => finish(true));
      socket.on("error", () => finish(false));
      socket.on("close", () => finish(false));
    } catch {
      finish(false);
    }
  });
}

function parseTimestamp(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const text = String(value || "").trim();
  if (!text) return 0;
  const numeric = Number(text);
  if (Number.isFinite(numeric) && numeric > 0) return numeric;
  const parsed = Date.parse(text);
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizePathForCompare(value) {
  return String(value || "").trim().replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

function pickNewestCodexThreadId(listResult, cwd) {
  const threads = Array.isArray(listResult?.threads)
    ? listResult.threads
    : (Array.isArray(listResult?.data) ? listResult.data : []);
  if (!threads.length) return "";

  // Normalize both sides: Codex stores Windows thread cwds with backslashes,
  // but our bridge passes forward-slash paths now, so a literal === comparison
  // would silently fall through and pick the wrong thread.
  const normalizedCwd = normalizePathForCompare(cwd);
  const preferred = [];
  const fallback = [];

  for (const thread of threads) {
    const id = String(thread?.id || "").trim();
    if (!id) continue;
    const threadCwd = normalizePathForCompare(thread?.cwd || thread?.directory || thread?.worktree || "");
    if (normalizedCwd && threadCwd && threadCwd === normalizedCwd) preferred.push(thread);
    else fallback.push(thread);
  }

  const candidates = preferred.length ? preferred : fallback;
  candidates.sort((a, b) => {
    const aTime = parseTimestamp(a?.updatedAt || a?.lastUpdatedAt || a?.createdAt || a?.timestamp);
    const bTime = parseTimestamp(b?.updatedAt || b?.lastUpdatedAt || b?.createdAt || b?.timestamp);
    return bTime - aTime;
  });

  return String(candidates[0]?.id || "").trim();
}

async function fetchCodexThreadList(rpc) {
  try {
    return await rpc.request("thread/list", { limit: 20, sourceKinds: ["cli", "vscode"] }, 5000);
  } catch {
    return await rpc.request("thread/list", {}, 5000);
  }
}

function codexMarkerToRuntimeConfig(marker = {}) {
  const runtimeConfig = {};
  const appServerUrl = String(marker.appServerUrl || "").trim();
  const remoteAuthTokenEnv = String(marker.remoteAuthTokenEnv || "").trim();
  if (appServerUrl) runtimeConfig.appServerUrl = appServerUrl;
  if (remoteAuthTokenEnv) runtimeConfig.remoteAuthTokenEnv = remoteAuthTokenEnv;
  return runtimeConfig;
}

async function inspectCodexLiveMarker(marker, cwd = process.cwd()) {
  const runtimeConfig = codexMarkerToRuntimeConfig(marker);
  if (!hasCodexLiveAppServer(runtimeConfig)) return null;

  const remoteAuthTokenEnv = String(runtimeConfig.remoteAuthTokenEnv || "").trim();
  const remoteAuthToken = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";
  let rpc = null;

  try {
    rpc = await createWebSocketRpcClient(runtimeConfig.appServerUrl, {
      token: remoteAuthToken || undefined,
    });
    await rpc.request("initialize", {
      clientInfo: {
        name: "aify-comms",
        title: "aify-comms marker inspector",
        version: "4.0.0",
      },
    });
    rpc.notify("initialized", {});

    const listResult = await fetchCodexThreadList(rpc);
    const threads = Array.isArray(listResult?.threads) ? listResult.threads : [];
    return {
      marker,
      runtimeConfig,
      threads,
      preferredThreadId: pickNewestCodexThreadId(listResult, cwd),
      fallbackThreadId: pickNewestCodexThreadId(listResult, ""),
    };
  } catch {
    return null;
  } finally {
    try {
      rpc?.close?.();
    } catch {
      // best effort
    }
  }
}

export async function discoverCodexLiveThreadId(runtimeConfig = {}, cwd = process.cwd()) {
  if (!hasCodexLiveAppServer(runtimeConfig)) return "";
  const appServerUrl = String(runtimeConfig?.appServerUrl || "").trim();
  if (!appServerUrl) return "";
  const remoteAuthTokenEnv = String(runtimeConfig?.remoteAuthTokenEnv || "").trim();
  const remoteAuthToken = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";

  let rpc = null;
  try {
    rpc = await createWebSocketRpcClient(appServerUrl, {
      token: remoteAuthToken || undefined,
    });
    await rpc.request("initialize", {
      clientInfo: {
        name: "aify-comms",
        title: "aify-comms register bridge",
        version: "4.0.0",
      },
    });
    rpc.notify("initialized", {});
    const result = await fetchCodexThreadList(rpc);
    return pickNewestCodexThreadId(result, cwd);
  } catch {
    return "";
  } finally {
    try {
      rpc?.close?.();
    } catch {
      // best effort
    }
  }
}

export async function discoverCodexLiveBinding({ sessionHandle = "", cwd = process.cwd() } = {}) {
  const normalizedSessionHandle = String(sessionHandle || "").trim();
  const normalizedCwd = String(cwd || "").trim() || process.cwd();
  const markers = listRuntimeMarkers("codex").filter((marker) =>
    hasCodexLiveAppServer(codexMarkerToRuntimeConfig(marker)),
  );
  if (!markers.length) return null;

  const inspected = [];
  const sessionMatches = [];
  for (const marker of markers) {
    const info = await inspectCodexLiveMarker(marker, normalizedCwd);
    if (!info) continue;
    inspected.push(info);

    if (
      normalizedSessionHandle &&
      info.threads.some((thread) => String(thread?.id || "").trim() === normalizedSessionHandle)
    ) {
      sessionMatches.push(info);
    }
  }

  if (!inspected.length) return null;

  if (normalizedSessionHandle && sessionMatches.length === 1) {
    return {
      runtimeConfig: sessionMatches[0].runtimeConfig,
      threadId: normalizedSessionHandle,
      ambiguous: false,
    };
  }

  if (normalizedSessionHandle && sessionMatches.length > 1) {
    return {
      runtimeConfig: null,
      threadId: normalizedSessionHandle,
      ambiguous: true,
    };
  }

  const byCwd = inspected.filter((info) => String(info.preferredThreadId || "").trim());
  if (!normalizedSessionHandle && byCwd.length === 1) {
    return {
      runtimeConfig: byCwd[0].runtimeConfig,
      threadId: String(byCwd[0].preferredThreadId || "").trim(),
      ambiguous: false,
    };
  }

  if (!normalizedSessionHandle && !byCwd.length && inspected.length === 1) {
    return {
      runtimeConfig: inspected[0].runtimeConfig,
      threadId: String(inspected[0].fallbackThreadId || "").trim(),
      ambiguous: false,
    };
  }

  if (!normalizedSessionHandle && byCwd.length > 1) {
    return {
      runtimeConfig: null,
      threadId: "",
      ambiguous: true,
    };
  }

  return null;
}

export function isClaudeSessionInUseError(text) {
  return /session id(?:\s+[0-9a-f-]+)?\s+is already in use/i.test(String(text || ""));
}


export function detectRuntime(explicitRuntime) {
  if (explicitRuntime) return normalizeRuntime(explicitRuntime);
  if (process.env.AIFY_AGENT_RUNTIME) return normalizeRuntime(process.env.AIFY_AGENT_RUNTIME);
  if (process.env.AIFY_RUNTIME) return normalizeRuntime(process.env.AIFY_RUNTIME);
  if (process.env.CODEX_HOME || process.env.CODEX_SANDBOX) return "codex";
  if (process.env.HERMES_SESSION_ID || process.env.HERMES_HOME) return "hermes";
  if (process.env.OPENCODE_CLIENT || process.env.OPENCODE_CONFIG_DIR) return "opencode";
  if (process.env.PI_SESSION_ID || process.env.OMP_SESSION_ID || process.env.AIFY_PI_SESSION_ID) return "pi";
  if (process.env.CLAUDE_PROJECT_DIR || process.env.CLAUDECODE) return "claude-code";
  return "generic";
}

export function defaultCapabilitiesForRuntime(runtime, sessionMode, sessionHandle, runtimeConfig) {
  // Plan 2 (2026-05-25): derive from RuntimeAdapter instead of hardcoded
  // per-runtime branches.
  const runtimeN = normalizeRuntime(runtime || "");
  let adapter;
  try { adapter = adapterFor(runtimeN); } catch { return []; }

  const caps = [];
  const sessionModeN = String(sessionMode || "").toLowerCase();

  if (sessionModeN === "resident") {
    // Resident-capable only when adapter declares it AND, for gateway-backed
    // runtimes, the gateway URL is present.
    let gatewayOk = true;
    if (runtimeN === "hermes") {
      const gw = String((runtimeConfig || {}).gatewayUrl || "").trim();
      gatewayOk = !!gw;
    }
    if (adapter.supportsResident && gatewayOk) caps.push("resident-run");
  } else {
    if (adapter.supportsManaged) caps.push("managed-run");
  }

  if (adapter.supportsResident || adapter.supportsManaged) caps.push("resume");
  if (adapter.supportsInterrupt) caps.push("interrupt");
  if (adapter.supportsSteering) caps.push("steer");

  if (sessionModeN !== "resident" && adapter.supportsManaged) caps.push("spawn");

  return caps;
}

export function defaultMachineId() {
  let host =
    process.env.AIFY_MACHINE_ID ||
    process.env.COMPUTERNAME ||
    process.env.HOSTNAME ||
    "";
  if (!host) {
    try {
      host = os.hostname() || "";
    } catch {
      // ignore and fall through to unknown-host
    }
  }
  host = host || "unknown-host";
  const wsl = process.env.WSL_DISTRO_NAME ? `wsl-${process.env.WSL_DISTRO_NAME}` : process.platform;
  return `${wsl}:${host}`;
}

export function launchRuntimeRun({ agentId, agentInfo, run, runtimeState, callbacks, managedViaWrapper = false }) {
  // Plan 3 Task 12 (2026-05-25): per-runtime dispatch collapses to a single
  // adapter.controllerFor call. Each adapter owns its executionMode routing
  // (e.g. pi rejects resident, codex/hermes route resident vs managed
  // internally via their controller). Extra opts like managedViaWrapper are
  // harmless to adapters that don't consume them.
  const runtime = normalizeRuntime(agentInfo.runtime || "generic");
  let adapter;
  try {
    adapter = adapterFor(runtime);
  } catch (error) {
    return failedRuntimeController(runtime, error);
  }
  if (!adapter) {
    return failedRuntimeController(runtime, new Error(`Unknown runtime "${runtime}".`));
  }
  const executionMode = run?.executionMode || agentInfo?.session_mode || agentInfo?.sessionMode || "managed";
  try {
    const controller = adapter.controllerFor({
      agentId,
      agentInfo,
      run,
      runtimeState,
      callbacks,
      managedViaWrapper,
      executionMode,
    });
    if (!controller) {
      return failedRuntimeController(
        runtime,
        new Error(`Runtime "${runtime}" does not support executionMode="${executionMode}".`),
      );
    }
    // Plan 4 Task 13 (2026-05-25): wire the bridge's onReady callback
    // BEFORE start() is called — controllers fire markReady() from inside
    // start() and the listener must already be attached. callbacks.onReady
    // is an optional hook supplied by server.js; absence is harmless.
    if (typeof callbacks?.onReady === "function" &&
        typeof controller.setReadyListener === "function") {
      try { controller.setReadyListener(callbacks.onReady); } catch { /* best-effort */ }
    }
    return controller.start();
  } catch (error) {
    return failedRuntimeController(runtime, error);
  }
}

function failedRuntimeController(runtime, error) {
  const failure = error instanceof Error ? error : new Error(String(error));
  return {
    capabilities: controlCapabilitiesForRuntime(runtime),
    interrupt: () => {},
    steer: async () => {
      throw new Error(`Runtime "${runtime}" does not support active dispatch`);
    },
    promise: Promise.reject(failure),
  };
}
