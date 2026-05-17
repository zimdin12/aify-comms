import { randomUUID } from "crypto";
import { spawn, spawnSync } from "child_process";
import os from "os";
import fs from "fs";
import path from "path";
import readline from "readline";
import { fileURLToPath } from "url";
import { createOpencode } from "@opencode-ai/sdk";
import WebSocket from "ws";
import { listRuntimeMarkers } from "./runtime-markers.js";
import { detectCodexResumeFailure, resolveCodexRequestCwdFor } from "./codex-errors.js";

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

function spawnProcess(command, args, options = {}) {
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

function extractClaudeSessionInUseId(text) {
  const match = String(text || "").match(/session id\s+([0-9a-f-]{16,})\s+is already in use/i);
  return match ? match[1] : "";
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

function releaseManagedClaudeSessionLock(sessionId, cwd = "") {
  const normalized = String(sessionId || "").trim();
  if (!normalized || process.platform !== "win32") return { releasedPids: [], markerPids: [] };
  const markerPids = listRuntimeMarkers("claude-code", cwd)
    .map((marker) => Number(marker?.pid || 0))
    .filter((pid) => Number.isInteger(pid) && pid > 0);
  const script = buildManagedClaudeUnlockPowerShell(normalized, markerPids);
  const result = spawnSync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 10000,
  });
  if (result.status !== 0) return { releasedPids: [], markerPids };
  const releasedPids = String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return { releasedPids, markerPids };
}

const ENVIRONMENT_BRIDGE_ENV_KEYS = [
  "AIFY_ENVIRONMENT_BRIDGE",
  "AIFY_ENVIRONMENT_ID",
  "AIFY_ENVIRONMENT_LABEL",
  "AIFY_ENVIRONMENT_KIND",
  "AIFY_CWD_ROOTS",
];

export function runtimeChildEnv(extraEnv = {}) {
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
    "",
    "[mcp_servers.aify-comms.env]",
    `AIFY_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
    `CLAUDE_MCP_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
    'AIFY_MANAGED_DISPATCH = "1"',
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

function quoteForDisplay(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function describeCodexItem(item = {}) {
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
  const replyRule = isDashboardSender
    ? "The dashboard sender is the human/operator. Answer in final plain text; the bridge stores that final answer in dashboard chat."
    : run?.requireReply === false
    ? "No required handoff is being tracked for this message. If it asks a question, assigns work, names you, or you have useful evidence, answer in final plain text; otherwise keep the final answer very short."
    : "Before you finish handling this message, put the reply in final plain text. The bridge will thread and deliver that final answer to the sender; do not call comms_send for this current reply.";
  const channelRule = isChannelMessage
    ? "This appears to be a channel/group message. Reply in the channel only when you are named, responsible, asked for evidence, or can unblock the group. Otherwise avoid broad automatic acks. Use a direct message for owner-specific follow-up."
    : "";
  return [
    "[AIFY MESSAGE]",
    `This is a message delivered through aify-comms for agent "${agentId}" (${agentInfo.role || "agent"}).`,
    isDashboardSender
      ? "This run was started by the dashboard human/operator; final plain text is the chat reply."
      : "This is a managed background run. Final plain text is the current reply; the bridge captures, threads, and delivers it to the sender.",
    `Your aify-comms agentId is "${agentId}". Use that exact ID when checking your own inbox or conversation state.`,
    `From: ${run.from}.`,
    replyParent ? `MessageId: ${replyParent}. The bridge uses this to thread your final answer; use it as inReplyTo only for separate out-of-band messages.` : "",
    agentInfo.instructions ? `Standing instructions: ${agentInfo.instructions}` : "",
    "Treat the content below as a message from the sender. If it contains a work request, that work is now pending in this session. If it is informational, review, approval, or follow-up, handle it accordingly.",
    `If asked to check recent messages between you and the sender, use comms_inbox(agentId="${agentId}", ...) or the relevant direct-chat context, not the global dashboard feed.`,
    "Team communication contract: stay on the current message, treat it as a small contract, and do not mix unrelated topics. Identify the owner, expected answer/action, evidence/result needed, and any follow-up wake owed. If status/history/truth matters, inspect messages/files/tools first and say what you checked.",
    "Managed visibility rule: stdout, logs, tool output, and run summaries are telemetry, not the team-visible answer. Close the triggering message in final plain text; use comms_send only for separate teammate/dashboard updates or future self-wakes. If you ask teammates for parallel work, name the expected reply target and completion condition.",
    "Use compact working-team replies: answer, evidence checked, blocker or uncertainty, next action. Ask one clear question when blocked instead of guessing.",
    `Turn lifecycle: final plain text is only this turn's reply. It does not schedule future work. This is not a lockstep protocol: you may message teammates mid-turn, run parallel lanes, and continue your own bounded work inside the current turn. If future work must happen after this turn, create that wake before finishing. If your next action requires another agent, send that agent a separate comms_send. If your next action is your own next chunk after this turn, send yourself a separate comms_send(to="${agentId}", type="request", queueIfBusy=true, ...). Do not merely write "Next action: ..." unless no wake is needed.`,
    channelRule,
    !isDashboardSender
      ? "Use comms_send only for separate out-of-band messages, such as a later proactive update to dashboard after this current reply is complete."
      : "",
    isDashboardSender
      ? "Keep the final answer human-readable and scoped to the dashboard message."
      : "Keep the final answer compact: answer, evidence checked, blocker or uncertainty, next action.",
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
  const replyRule = isDashboardSender
    ? "Reply to the dashboard user in final plain text."
    : run?.requireReply === false
    ? "If this message asks you a question, assigns you work, names you, or you have useful evidence, answer in final plain text; otherwise keep it as read context."
    : "Required handoff: answer in final plain text before you finish. The bridge will deliver it; do not call comms_send for this current reply.";
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
    isDashboardSender
      ? "Human-visible reply: final plain text is delivered to dashboard chat."
      : "Reply delivery: final plain text is threaded and delivered to the sender by the bridge.",
    replyRule,
    !isDashboardSender
      ? "Use comms_send only for separate out-of-band updates, not for the current reply."
      : "",
    isChannelMessage
      ? "Channel discipline: respond only when your reply is useful to the group or sender. Do not create broad acknowledgement loops."
      : "",
    "Keep this turn scoped to the message above and its direct context. Do not carry unrelated older topics forward unless the sender explicitly asks for them.",
    "Do not end silently. Answer the sender in final plain text. If you owe a separate update or future wake, create it with comms_send before the final answer.",
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

function splitProviderModel(value) {
  const text = String(value || "").trim();
  if (!text || !text.includes("/")) return null;
  const [providerID, ...modelParts] = text.split("/");
  const modelID = modelParts.join("/").trim();
  if (!providerID || !modelID) return null;
  return { providerID: providerID.trim(), modelID };
}

function opencodePermissionConfig(config = {}) {
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

function summarizeOpenCodeParts(parts = []) {
  const textChunks = [];
  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    if (part.type === "text" && part.text) {
      textChunks.push(String(part.text));
    }
  }
  return textChunks.join("").trim();
}

function extractPiAssistantText(value) {
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

function extractPiSessionState(value) {
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

function normalizePiModelOverride(value) {
  const text = String(value || "").trim();
  return text.toLowerCase() === "default" ? "" : text;
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
  if (!lower) return { shouldHeal: false, authFailure: false, missingSession: false, healReason: null, message };
  const authFailure =
    /no api key/.test(lower) ||
    /api key (?:not found|missing|required)/.test(lower) ||
    /not authenticated|authentication (?:failed|required)|unauthori[sz]ed|\b401\b/.test(lower) ||
    ((/amazon-bedrock|bedrock/.test(lower)) && /login|auth|credential|api key/.test(lower));
  if (authFailure) {
    return { shouldHeal: false, authFailure: true, missingSession: false, healReason: null, message };
  }
  const missingSession =
    /session\s+["']?[^"'\s]+["']?\s+(?:not found|does not exist|missing)/i.test(message) ||
    /no such session/i.test(message);
  if (missingSession) {
    return { shouldHeal: true, authFailure: false, missingSession: true, healReason: "missing_session", message };
  }
  return { shouldHeal: false, authFailure: false, missingSession: false, healReason: null, message };
}


function requireOpenCodeData(response, fallbackMessage) {
  if (response?.data) return response.data;
  const errorMessage =
    response?.error?.data?.message ||
    response?.error?.message ||
    fallbackMessage;
  throw new Error(errorMessage);
}

function defaultCodexCommand() {
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

function resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl }) {
  return resolveCodexRequestCwdFor({
    hostCwd,
    appServerUrl,
    legacyTransform: (raw) => codexWorkingPath(launcher, raw),
  });
}

function codexSpawnCwd(launcher, cwd) {
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

function defaultPiCommand() {
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

function diagnosticsFor(name) {
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
    const expected = configured || "claude";
    const available = hasExecutable(expected);
    return {
      available,
      message: available
        ? `Claude Code launcher available (resolved to ${resolveExecutable(expected)})`
        : `Runtime "claude-code" is not launchable from this bridge because "${expected}" could not be resolved to a real executable. ` +
          `Install Claude Code for this OS/user, or set AIFY_CLAUDE_COMMAND to an absolute path and restart the bridge. ` +
          `Diagnostic: ${diagnosticsFor(expected)}`,
    };
  }
  if (normalized === "codex") {
    const launcher = defaultCodexCommand();
    const available = hasExecutable(launcher.command);
    return {
      available,
      message: available
        ? `Codex launcher available (${launcher.command})`
        : `Runtime "codex" is not launchable from this bridge because "${launcher.command}" is not available. ` +
          `Diagnostic: ${diagnosticsFor(launcher.command)}`,
    };
  }
  if (normalized === "hermes") {
    const configured = String(process.env.AIFY_HERMES_COMMAND || process.env.HERMES_COMMAND || "").trim();
    const expected = configured || "hermes";
    const available = hasExecutable(expected);
    return {
      available,
      message: available
        ? `Hermes launcher available (resolved to ${resolveExecutable(expected)})`
        : `Runtime "hermes" is not launchable from this bridge because "${expected}" could not be resolved to a real executable. ` +
          `Install Hermes Agent for this OS/user, or set AIFY_HERMES_COMMAND to an absolute path and restart the bridge. ` +
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

function canUseDefaultResidentCodexBridge() {
  if (process.platform !== "win32") return true;
  const originator = String(process.env.CODEX_INTERNAL_ORIGINATOR_OVERRIDE || "").trim().toLowerCase();
  if (originator !== "codex desktop") return true;
  return process.env.AIFY_CODEX_ALLOW_DESKTOP_RESIDENT === "1";
}

export function hasClaudeLiveChannel(runtimeConfig = {}) {
  return (
    runtimeConfig?.channelEnabled === true ||
    process.env.AIFY_COMMS_CHANNEL_ENABLED === "1" ||
    process.env.AIFY_CLAUDE_CHANNEL_ENABLED === "1"
  );
}

function getRuntimeConfig(agentInfo) {
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
  switch (normalizeRuntime(runtime)) {
    case "codex":
      return { interrupt: true, steer: true };
    case "hermes":
      return { interrupt: true, steer: false };
    case "opencode":
      return { interrupt: true, steer: false };
    case "pi":
      return { interrupt: true, steer: true };
    case "claude-code":
      return { interrupt: true, steer: false };
    default:
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

function createRpcClient(proc, { onNotification, onStderr }) {
  const pending = new Map();
  let nextId = 1;
  let processError = null;

  function failPending(error) {
    for (const [id, pendingRequest] of pending.entries()) {
      pending.delete(id);
      pendingRequest.reject(error);
    }
  }

  proc.on("error", (error) => {
    processError = error instanceof Error ? error : new Error(String(error));
    failPending(processError);
    if (onStderr) onStderr(processError.message || String(processError));
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

    if (message.method && onNotification) {
      onNotification(message);
    }
  });

  const stderr = readline.createInterface({ input: proc.stderr });
  stderr.on("line", (line) => {
    if (onStderr) onStderr(line);
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

  return { request, notify };
}

function createWebSocketRpcClient(url, { token, onNotification, onStderr } = {}) {
  return new Promise((resolve, reject) => {
    const pending = new Map();
    let nextId = 1;
    let opened = false;
    let closed = false;

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
      }

      resolve({ request, notify, close });
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

      if (message.method && onNotification) {
        onNotification(message);
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
  const threads = Array.isArray(listResult?.threads) ? listResult.threads : [];
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

function createClaudeController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  throw new Error(
    "Claude Code managed Messenger no longer uses claude -p. " +
    "Start or attach a Claude PTY/channel runtime with claude-aify, then deliver Messenger work through the resident channel bridge.",
  );
  const config = getRuntimeConfig(agentInfo);
  const availability = runtimeLaunchAvailability("claude-code");
  if (!availability.available) throw new Error(availability.message);
  const launcher = defaultClaudeCommand();
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  const residentSessionId = String(agentInfo.sessionHandle || "").trim();
  const initialSessionId =
    executionMode === "resident"
      ? residentSessionId
      : (runtimeState?.sessionId || residentSessionId || randomUUID());
  const maxTurns = String(managedClaudeMaxTurns(config));
  const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
  if (executionMode === "resident" && !initialSessionId) {
    throw new Error(
      `Resident Claude session "${agentId}" has no bound session ID. Re-register from the live Claude session or provide sessionHandle explicitly.`,
    );
  }
  let settled = false;
  let interrupted = false;
  let activeProcess = null;

  const startAttempt = (sessionId, attempt = 1, forceResume = false) => {
    const cwd = agentInfo.cwd || process.cwd();
    const resumeExistingTranscript = forceResume || claudeSessionTranscriptExists(sessionId, cwd);
    const args = [
      ...launcher.args,
      ...managedClaudePermissionArgs(config, executionMode),
      "-p",
      "--output-format", "text",
      ...(resumeExistingTranscript ? ["--resume", sessionId] : ["--session-id", sessionId]),
      "--max-turns", maxTurns,
      "--append-system-prompt", buildSystemPrompt(agentId, agentInfo, run),
    ];

    const model = managedClaudeModel(agentInfo, config);
    if (model) {
      args.push("--model", model);
    }
    const effort = managedClaudeEffort(config);
    if (effort) {
      args.push("--effort", effort);
    }

    const proc = spawnProcess(launcher.command, args, { cwd });
    activeProcess = proc;
    const chunks = [];
    const errChunks = [];
    settled = false;

    callbacks.onRuntimeState?.({ sessionId });

    proc.stdout.on("data", (chunk) => chunks.push(chunk));
    proc.stderr.on("data", (chunk) => errChunks.push(chunk));
    proc.stdin.write(buildUserPrompt(run));
    proc.stdin.end();

    const timer = setTimeout(() => {
      if (!settled) {
        terminateProcessTree(proc);
      }
    }, timeoutMs);

    return new Promise((resolve, reject) => {
      proc.on("error", (error) => {
        settled = true;
        clearTimeout(timer);
        // Self-describing failure: when ENOENT escapes here it means the
        // path the availability check accepted is not actually spawnable
        // from this bridge process. Surface what we tried to spawn and what
        // the bridge's PATH looks like so the dashboard's failure event is
        // actionable instead of "spawn claude ENOENT".
        if (error && error.code === "ENOENT") {
          const enriched = new Error(
            `spawn "${launcher.command}" ENOENT — this bridge resolved Claude Code to "${launcher.command}" ` +
            `but Node could not execute it. Most common causes: (a) the binary requires a shell wrapper / function ` +
            `that only exists in an interactive bash, (b) the file lacks the execute bit for this user, ` +
            `(c) the resolved path is a symlink to a missing target. ` +
            `Also verify the runtime cwd exists: "${cwd}". ` +
            `Fix: set AIFY_CLAUDE_COMMAND to an absolute path to a real "claude" binary and restart aify-comms. ` +
            `Diagnostic: ${diagnosticsFor(String(process.env.AIFY_CLAUDE_COMMAND || process.env.CLAUDE_COMMAND || "claude").trim())}`,
          );
          enriched.code = error.code;
          enriched.originalError = error.message;
          reject(enriched);
          return;
        }
        reject(error);
      });

      proc.on("close", (code) => {
        settled = true;
        clearTimeout(timer);
        const stdout = Buffer.concat(chunks).toString("utf-8").trim();
        const stderr = Buffer.concat(errChunks).toString("utf-8").trim();
        if (interrupted) {
          resolve({
            status: "cancelled",
            summary: stdout || stderr || "Run interrupted",
            runtimeState: { sessionId },
          });
          return;
        }
        if (code === 0) {
          resolve({
            status: "completed",
            summary: stdout || "(no output)",
            runtimeState: { sessionId },
          });
          return;
        }
        const errorText = stderr || stdout || `Claude exited with code ${code}`;
        if (executionMode !== "resident" && isClaudeSessionInUseError(errorText)) {
          const lockedSessionId = extractClaudeSessionInUseId(errorText) || sessionId;
          if (!resumeExistingTranscript && attempt === 1) {
            callbacks.onEvent?.(
              "runtime",
              `Claude reported session ${lockedSessionId} already exists; retrying once with --resume instead of --session-id.`,
            );
            startAttempt(lockedSessionId, attempt + 1, true).then(resolve, reject);
            return;
          }
          if (attempt <= 2) {
            const { releasedPids, markerPids } = releaseManagedClaudeSessionLock(
              lockedSessionId,
              agentInfo.cwd || process.cwd(),
            );
            if (releasedPids.length > 0) {
              callbacks.onEvent?.(
                "runtime",
                `Released stale headless Claude process(es) for session ${lockedSessionId}: ${releasedPids.join(", ")}; retrying once.`,
              );
              startAttempt(sessionId, attempt + 1, resumeExistingTranscript).then(resolve, reject);
              return;
            }
            if (markerPids.length > 0) {
              callbacks.onEvent?.(
                "runtime",
                `Found Claude runtime marker(s) for this workspace (${markerPids.join(", ")}), but none looked like a headless managed Claude owner; leaving them running.`,
              );
            }
          }
          reject(new Error(
            `${errorText}\n\nThe stored Claude session is locked by another Claude process. ` +
            `The bridge did not find a matching stale headless Claude process it could release automatically by session ID or workspace runtime marker. ` +
            `Close the duplicate Claude process or explicitly clear this agent's resume state from Dashboard -> Sessions/Team, then restart/recover. ` +
            `The bridge did not create a fresh session automatically because that would discard native chat memory.`,
          ));
          return;
        }
        reject(new Error(errorText));
      });
    });
  };

  const promise = startAttempt(initialSessionId);

  return {
    capabilities: controlCapabilitiesForRuntime("claude-code"),
    interrupt: () => {
      interrupted = true;
      if (!settled && activeProcess) terminateProcessTree(activeProcess);
    },
    steer: async () => {
      throw new Error('Runtime "claude-code" does not support steer');
    },
    promise,
  };
}

export function isClaudeSessionInUseError(text) {
  return /session id(?:\s+[0-9a-f-]+)?\s+is already in use/i.test(String(text || ""));
}

function createCodexController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const config = getRuntimeConfig(agentInfo);
  const launcher = defaultCodexCommand();
  const resumePolicy = String(runtimeState?.resumePolicy || agentInfo?.runtimeState?.resumePolicy || "native_first").trim().toLowerCase();
  const allowFreshContext = resumePolicy === "fresh_context";
  const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
  const configuredQuietTimeout = Number(config.quietTimeoutMs ?? config.silenceTimeoutMs ?? 30 * 60 * 1000);
  const quietTimeoutMs = configuredQuietTimeout <= 0
    ? 0
    : Math.max(10 * 60 * 1000, configuredQuietTimeout);
  const configuredAifyMcpToolTimeout = Number(config.mcpToolTimeoutMs ?? config.commsToolTimeoutMs ?? 90 * 1000);
  const aifyMcpToolTimeoutMs = configuredAifyMcpToolTimeout <= 0
    ? 0
    : Math.max(10 * 1000, configuredAifyMcpToolTimeout);
  const hostCwd = agentInfo.cwd || process.cwd();
  const model = String(agentInfo.model || config.model || "").trim();
  const effort = managedCodexEffort(config);
  const summaryMode = config.summary || "concise";
  const approvalPolicy = config.approvalPolicy || "never";
  const networkAccess = config.networkAccess !== false;
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  const sandboxMode = managedCodexSandboxMode(config, executionMode);
  const residentThreadId = String(agentInfo.sessionHandle || "").trim();
  const appServerUrl =
    executionMode === "resident" && hasCodexLiveAppServer(config)
      ? String(config.appServerUrl || "").trim()
      : "";
  const cwd = resolveCodexRequestCwd({ hostCwd, launcher, appServerUrl });
  const spawnCwd = codexSpawnCwd(launcher, hostCwd);
  const managedCodexHome =
    executionMode === "managed"
      ? prepareManagedCodexHome({ workspace: cwd, model, effort })
      : "";
  const remoteAuthTokenEnv = String(config.remoteAuthTokenEnv || "").trim();
  const remoteAuthToken = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";

  let activeTurnId = null;
  let activeThreadId =
    executionMode === "resident"
      ? (residentThreadId || null)
      : (runtimeState?.threadId || null);
  let finalText = "";
  let finalStatus = "failed";
  let finalError = "";
  let settled = false;
  let rejectPromise;
  let interrupted = false;
  let rpc = null;
  let proc = null;
  let lastActivityAt = Date.now();
  let activityLabel = "runtime launch";
  const activeItems = new Map();

  const markActivity = (label = "runtime event") => {
    lastActivityAt = Date.now();
    activityLabel = label;
  };

  const handleNotification = (message) => {
    markActivity(message.method || "runtime notification");
    const params = message.params || {};
    if (message.method === "turn/started" && params.turn?.id) {
      activeTurnId = params.turn.id;
      callbacks.onRefs?.({ turnId: activeTurnId });
      callbacks.onEvent?.("turn", `Started turn ${activeTurnId}`);
    } else if (message.method === "turn/completed") {
      finalStatus = params.turn?.status || "completed";
      if (params.turn?.error?.message) {
        finalError = params.turn.error.message;
      }
      if (finalStatus === "completed" || finalStatus === "interrupted" || finalStatus === "failed") {
        settled = true;
      }
    } else if (message.method === "item/agentMessage/delta") {
      const delta = params.delta || "";
      if (delta) finalText += delta;
    } else if (message.method === "item/completed" && params.item?.type === "agentMessage") {
      finalText = params.item.text || finalText;
      if (params.item?.id) activeItems.delete(params.item.id);
    } else if (message.method === "item/started" && params.item?.id) {
      const itemType = describeCodexItem(params.item);
      activeItems.set(params.item.id, { label: itemType, startedAt: Date.now() });
      callbacks.onEvent?.("codex", `Started ${itemType}`);
    } else if (message.method === "item/completed" && params.item?.id) {
      const itemType = activeItems.get(params.item.id)?.label || describeCodexItem(params.item);
      activeItems.delete(params.item.id);
      callbacks.onEvent?.("codex", `Completed ${itemType}`);
    } else if (message.method === "error" && params.error?.message) {
      finalError = params.error.message;
    }
  };

  const handleRuntimeLog = (line) => {
    const text = quoteForDisplay(line);
    if (text) {
      markActivity("stderr");
      callbacks.onEvent?.("stderr", text);
    }
    if (text && isFatalCodexRuntimeLog(text) && !settled) {
      finalStatus = "failed";
      finalError = `Codex runtime fatal error: ${text}`;
      settled = true;
      try {
        terminateProcessTree(proc);
      } catch {
        // ignore shutdown errors
      }
      try {
        rpc?.close?.();
      } catch {
        // ignore close errors
      }
      if (rejectPromise) rejectPromise(new Error(finalError));
    }
  };

  const promise = new Promise(async (resolve, reject) => {
    rejectPromise = reject;
    let quietTimer = null;
    let mcpToolTimer = null;
    const timer = setTimeout(() => {
      if (!settled) {
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        try {
          terminateProcessTree(proc);
        } catch {
          // ignore shutdown errors
        }
        try {
          rpc?.close?.();
        } catch {
          // ignore close errors
        }
        reject(new Error(`Codex run timed out after ${timeoutMs}ms`));
      }
    }, timeoutMs);
    if (quietTimeoutMs > 0) {
      quietTimer = setInterval(() => {
        if (settled) return;
        const idleFor = Date.now() - lastActivityAt;
        if (idleFor < quietTimeoutMs) return;
        const activeLabel = activeItems.size
          ? ` Active Codex item(s): ${[...new Set([...activeItems.values()].map(item => item.label))].join(", ")}.`
          : "";
        const message =
          `Codex run produced no runtime activity for ${quietTimeoutMs}ms after ${activityLabel}.` +
          activeLabel +
          ` The turn was treated as stalled and terminated. Retry the message, or restart/recover the session if this repeats.`;
        finalStatus = "failed";
        finalError = message;
        settled = true;
        clearTimeout(timer);
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        try {
          callbacks.onEvent?.("stalled", message);
        } catch {
          // best effort
        }
        try {
          terminateProcessTree(proc);
        } catch {
          // ignore shutdown errors
        }
        try {
          rpc?.close?.();
        } catch {
          // ignore close errors
        }
        reject(new Error(message));
      }, Math.min(60 * 1000, Math.max(10 * 1000, Math.floor(quietTimeoutMs / 6))));
    }
    if (aifyMcpToolTimeoutMs > 0) {
      mcpToolTimer = setInterval(() => {
        if (settled) return;
        const now = Date.now();
        const stuck = [...activeItems.values()].find(item => (
          isAifyCommsMcpToolItem(item.label) && now - item.startedAt >= aifyMcpToolTimeoutMs
        ));
        if (!stuck) return;
        const message =
          `Codex aify-comms MCP tool call produced no completion for ${aifyMcpToolTimeoutMs}ms. ` +
          `The turn was terminated before the general quiet-stall timeout. Retry the message after the bridge is updated/restarted; if it repeats, inspect the aify-comms MCP server logs.`;
        finalStatus = "failed";
        finalError = message;
        settled = true;
        clearTimeout(timer);
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        try {
          callbacks.onEvent?.("mcp_tool_stalled", message);
        } catch {
          // best effort
        }
        try {
          terminateProcessTree(proc);
        } catch {
          // ignore shutdown errors
        }
        try {
          rpc?.close?.();
        } catch {
          // ignore close errors
        }
        reject(new Error(message));
      }, Math.min(10 * 1000, Math.max(2 * 1000, Math.floor(aifyMcpToolTimeoutMs / 6))));
    }

    try {
      if (appServerUrl) {
        callbacks.onEvent?.("runtime", `Connecting to shared Codex app-server ${appServerUrl}`);
        rpc = await createWebSocketRpcClient(appServerUrl, {
          token: remoteAuthToken || undefined,
          onNotification: handleNotification,
          onStderr: handleRuntimeLog,
        });
      } else {
        proc = spawnProcess(launcher.command, launcher.args, {
          cwd: spawnCwd,
          env: managedCodexHome ? { CODEX_HOME: managedCodexHome } : {},
        });
        rpc = createRpcClient(proc, {
          onNotification: handleNotification,
          onStderr: handleRuntimeLog,
        });
      }

      await rpc.request("initialize", {
        clientInfo: {
          name: "aify-comms",
          title: "aify-comms dispatch bridge",
        version: "4.0.0",
        },
      });
      markActivity("initialize");
      rpc.notify("initialized", {});

      const startThread = async () => {
        const threadStartParams = {
          cwd,
          approvalPolicy,
          personality: "friendly",
          serviceName: "aify-comms",
        };
        if (model) threadStartParams.model = model;
        let started;
        try {
          started = await rpc.request("thread/start", {
            ...threadStartParams,
            sandbox: sandboxMode,
          }, 60000);
        } catch (error) {
          const message = error?.message || "";
          if (sandboxMode !== "workspace-write" || !message.includes("unknown variant `workspace-write`")) {
            throw error;
          }
          started = await rpc.request("thread/start", {
            ...threadStartParams,
            sandbox: "workspaceWrite",
          }, 60000);
        }
        return started.thread?.id;
      };

      if (!activeThreadId) {
        if (executionMode === "resident") {
          throw new Error(
            `Resident Codex session "${agentId}" has no bound thread ID. Re-register from the live Codex session or provide sessionHandle explicitly.`,
          );
        }
        callbacks.onEvent?.("thread", `No thread bound yet; calling thread/start with cwd="${cwd}"`);
        try {
          activeThreadId = await startThread();
        } catch (error) {
          throw new Error(
            `Codex thread/start failed for fresh thread (cwd="${cwd}"): ${error?.message || error}`,
            { cause: error },
          );
        }
      } else {
        callbacks.onEvent?.("thread", `Attempting thread/resume for ${activeThreadId}`);
        try {
          const resumed = await rpc.request("thread/resume", {
            threadId: activeThreadId,
            personality: "friendly",
          }, 60000);
          activeThreadId = resumed.thread?.id || activeThreadId;
        } catch (error) {
          // Classification lives in detectCodexResumeFailure so it can be
          // unit-tested without a live Codex.
          const failure = detectCodexResumeFailure(error);
          const resumeMessage = String(error?.message || "").trim();
          if (!failure.shouldHeal) {
            // Unknown error — surface it with the step name so the dashboard
            // run log tells us exactly which RPC call failed.
            throw new Error(
              `Codex thread/resume failed for thread ${activeThreadId} with unhandled error: ${resumeMessage}`,
              { cause: error },
            );
          }

          let resumedAfterImport = false;
          if (executionMode === "managed" && failure.noRollout && managedCodexHome) {
            const imported = importCodexThreadRollout({
              threadId: activeThreadId,
              targetHome: managedCodexHome,
            });
            if (imported.imported) {
              callbacks.onEvent?.(
                "thread",
                `Imported Codex rollout for ${activeThreadId} from ${imported.sourceHome}; retrying thread/resume`,
              );
              try {
                const resumed = await rpc.request("thread/resume", {
                  threadId: activeThreadId,
                  personality: "friendly",
                }, 60000);
                activeThreadId = resumed.thread?.id || activeThreadId;
                callbacks.onEvent?.(
                  "thread",
                  `Resumed imported Codex thread ${activeThreadId} (${imported.rollouts.length} rollout file(s), ${imported.shellSnapshots.length} shell snapshot(s))`,
                );
                markActivity("thread/resume imported rollout");
                resumedAfterImport = true;
              } catch (retryError) {
                throw new Error(
                  `Codex thread/resume failed for saved thread ${activeThreadId} after importing its rollout from ${imported.sourceHome}: ` +
                  `${retryError?.message || retryError}`,
                  { cause: retryError },
                );
              }
            }
          }

          if (resumedAfterImport) {
            // The native rollout was found in another Codex home and the
            // retry succeeded. Keep the saved handle unchanged and continue.
          } else if (!allowFreshContext) {
            throw new Error(
              `Codex thread/resume failed for saved thread ${activeThreadId} (${failure.healReason}: ${resumeMessage}). ` +
              `The bridge did not create a fresh thread because that would discard native chat memory. ` +
              `Use Dashboard -> Sessions -> Recreate only when you intentionally want a new context.`,
              { cause: error },
            );
          } else {
            // Only explicit fresh-context requests may create a replacement
            // thread. Ordinary restart/recovery must fail loudly instead of
            // silently discarding native chat memory.
            const previousThreadId = activeThreadId;
            const reasonLabel = failure.corruptRollout
              ? `Rollout for thread ${previousThreadId} is corrupt (${resumeMessage})`
              : `Thread ${previousThreadId} has no rollout`;
            const modeLabel = executionMode === "resident"
              ? "; healing resident session with a fresh thread (visibility in the live TUI is lost until the user relaunches codex-aify from a clean environment)"
              : "; starting a fresh thread";
            callbacks.onEvent?.("thread", reasonLabel + modeLabel);
            try {
              activeThreadId = await startThread();
            } catch (healError) {
              throw new Error(
                `Codex thread/resume for ${previousThreadId} failed with ${failure.healReason} (${resumeMessage}), ` +
                `and the auto-heal fallback thread/start also failed: ${healError?.message || healError}. ` +
                `This usually means Codex's app-server itself is in a bad state — kill the codex app-server process ` +
                `and relaunch codex-aify from the target project directory. See the aify-comms-debug skill.`,
                { cause: healError },
              );
            }
            // Push the new thread id back to the caller so the backend's
            // stored sessionHandle gets updated. Without this, the very next
            // dispatch would try to resume the same poisoned thread and hit
            // the exact same error.
            if (activeThreadId && activeThreadId !== previousThreadId) {
              try {
                await callbacks.onSessionHandleChange?.(activeThreadId, {
                  previous: previousThreadId,
                  reason: failure.healReason,
                });
                callbacks.onEvent?.("thread", `Healed: ${previousThreadId} → ${activeThreadId} (${failure.healReason})`);
              } catch (cbError) {
                console.error(
                  `[aify] onSessionHandleChange callback failed after healing thread: ${cbError?.message || cbError}`,
                );
              }
            }
          }
        }
      }

      callbacks.onRuntimeState?.({ threadId: activeThreadId });
      callbacks.onRefs?.({ threadId: activeThreadId });
      callbacks.onEvent?.("thread", `Using ${executionMode} thread ${activeThreadId}`);
      markActivity("thread ready");

      callbacks.onEvent?.("turn", `Calling turn/start on thread ${activeThreadId} with cwd="${cwd}", writableRoots=["${cwd}"]`);
      let turn;
      try {
        const turnStartParams = {
          threadId: activeThreadId,
          input: [{ type: "text", text: `${buildSystemPrompt(agentId, agentInfo, run)}\n\n${buildUserPrompt(run)}` }],
          cwd,
          approvalPolicy,
          sandboxPolicy: codexTurnSandboxPolicy(sandboxMode, cwd, networkAccess),
          effort,
          summary: summaryMode,
          personality: "friendly",
        };
        if (model) turnStartParams.model = model;
        turn = await rpc.request("turn/start", turnStartParams, 60000);
      } catch (error) {
        // turn/start sends cwd + writableRoots — if AbsolutePathBuf fires
        // here, it's one of those two fields. Label the error so the run
        // log shows us unambiguously which RPC tripped.
        throw new Error(
          `Codex turn/start failed for thread ${activeThreadId} (cwd="${cwd}"): ${error?.message || error}`,
          { cause: error },
        );
      }

      activeTurnId = turn.turn?.id || activeTurnId;
      callbacks.onRefs?.({ threadId: activeThreadId, turnId: activeTurnId });
      markActivity("turn/start");

      const poll = setInterval(() => {
        if (!settled) return;
        clearInterval(poll);
        clearTimeout(timer);
        clearInterval(quietTimer);
        clearInterval(mcpToolTimer);
        if (finalStatus === "completed") {
          resolve({
            status: "completed",
            summary: finalText.trim() || "(no output)",
            runtimeState: { threadId: activeThreadId },
            externalRefs: { threadId: activeThreadId, turnId: activeTurnId },
          });
          try {
            terminateProcessTree(proc);
          } catch {
            // ignore shutdown errors
          }
          try {
            rpc?.close?.();
          } catch {
            // ignore close errors
          }
          return;
        }
        if (finalStatus === "interrupted" || interrupted) {
          resolve({
            status: "cancelled",
            summary: finalText.trim() || finalError || "Run interrupted",
            runtimeState: { threadId: activeThreadId },
            externalRefs: { threadId: activeThreadId, turnId: activeTurnId },
          });
          try {
            terminateProcessTree(proc);
          } catch {
            // ignore shutdown errors
          }
          try {
            rpc?.close?.();
          } catch {
            // ignore close errors
          }
          return;
        }
        const detail = finalError || finalText || `Codex turn finished with status ${finalStatus}`;
        reject(new Error(detail));
        try {
          terminateProcessTree(proc);
        } catch {
          // ignore shutdown errors
        }
        try {
          rpc?.close?.();
        } catch {
          // ignore close errors
        }
      }, 250);
    } catch (error) {
      clearTimeout(timer);
      clearInterval(quietTimer);
      clearInterval(mcpToolTimer);
      reject(error);
      try {
        terminateProcessTree(proc);
      } catch {
        // ignore shutdown errors
      }
      try {
        rpc?.close?.();
      } catch {
        // ignore close errors
      }
    }
  });

  return {
    capabilities: controlCapabilitiesForRuntime("codex"),
    interrupt: async () => {
      interrupted = true;
      if (!activeThreadId || !activeTurnId) {
        terminateProcessTree(proc);
        return;
      }
      try {
        await rpc.request("turn/interrupt", {
          threadId: activeThreadId,
          turnId: activeTurnId,
        }, 30000);
      } catch (error) {
        if (rejectPromise) rejectPromise(error);
      }
    },
    steer: async (text) => {
      if (!activeThreadId || !activeTurnId) {
        throw new Error("No active Codex turn to steer");
      }
      if (!text || !String(text).trim()) {
        throw new Error("Steer body is required");
      }
      await rpc.request("turn/steer", {
        threadId: activeThreadId,
        input: [{ type: "text", text: String(text) }],
        expectedTurnId: activeTurnId,
      }, 30000);
      callbacks.onEvent?.("steer", `Steer applied to ${activeTurnId}`);
    },
    promise,
  };
}

function createOpenCodeController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const config = getRuntimeConfig(agentInfo);
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  const residentSessionId = String(agentInfo.sessionHandle || "").trim();
  const cwd = agentInfo.cwd || process.cwd();
  const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
  const model = splitProviderModel(agentInfo.model || config.model || "");
  const permission = opencodePermissionConfig(config);
  const selectedAgent = String(config.agent || "").trim() || undefined;
  let sessionId =
    executionMode === "resident"
      ? residentSessionId
      : String(runtimeState?.sessionId || residentSessionId || "").trim();

  if (executionMode === "resident" && !sessionId) {
    throw new Error(
      `Resident OpenCode session "${agentId}" has no bound session ID. ` +
      "Re-register with sessionHandle explicitly or create a persistent environment-managed agent with comms_spawn.",
    );
  }

  let interrupted = false;
  let open = null;

  const promise = new Promise(async (resolve, reject) => {
    const timer = setTimeout(async () => {
      interrupted = true;
      try {
        if (open?.client && sessionId) {
          await open.client.session.abort({
            path: { id: sessionId },
            query: { directory: cwd },
          });
        }
      } catch {
        // best effort
      }
      reject(new Error(`OpenCode run timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    try {
      open = await createOpencode({
        port: 0,
        config: permission ? { permission } : undefined,
      });
      const client = open.client;

      if (!sessionId) {
        const created = await client.session.create({
          query: { directory: cwd },
          body: { title: run.subject || `aify:${agentId}` },
        });
        sessionId = requireOpenCodeData(created, "Failed to create OpenCode session").id;
      } else {
        requireOpenCodeData(await client.session.get({
          path: { id: sessionId },
          query: { directory: cwd },
        }), `OpenCode session "${sessionId}" was not found`);
      }

      callbacks.onRuntimeState?.({ sessionId });
      callbacks.onRefs?.({ threadId: sessionId });
      callbacks.onEvent?.("thread", `Using ${executionMode} OpenCode session ${sessionId}`);

      const response = await client.session.prompt({
        path: { id: sessionId },
        query: { directory: cwd },
        body: {
          ...(model ? { model } : {}),
          ...(selectedAgent ? { agent: selectedAgent } : {}),
          system: buildSystemPrompt(agentId, agentInfo, run),
          parts: [{ type: "text", text: buildUserPrompt(run) }],
        },
      });

      clearTimeout(timer);
      const data = requireOpenCodeData(response, "OpenCode prompt failed");
      const info = data.info || {};
      const parts = data.parts || [];
      const summary = summarizeOpenCodeParts(parts);
      const errorMessage =
        info?.error?.data?.message ||
        info?.error?.message ||
        info?.error?.name ||
        "";

      if (interrupted || /aborted/i.test(errorMessage || "")) {
        resolve({
          status: "cancelled",
          summary: summary || errorMessage || "Run interrupted",
          runtimeState: { sessionId },
          externalRefs: { threadId: sessionId, turnId: info.id || "" },
        });
        return;
      }

      if (errorMessage) {
        reject(new Error(errorMessage));
        return;
      }

      resolve({
        status: "completed",
        summary: summary || "(no output)",
        runtimeState: { sessionId },
        externalRefs: { threadId: sessionId, turnId: info.id || "" },
      });
    } catch (error) {
      clearTimeout(timer);
      reject(error);
    } finally {
      try {
        open?.server?.close?.();
      } catch {
        // ignore close errors
      }
    }
  });

  return {
    capabilities: controlCapabilitiesForRuntime("opencode"),
    interrupt: async () => {
      interrupted = true;
      if (!open?.client || !sessionId) return;
      await open.client.session.abort({
        path: { id: sessionId },
        query: { directory: cwd },
      });
    },
    steer: async () => {
      throw new Error('Runtime "opencode" does not support steer');
    },
    promise,
  };
}

function createPiController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const config = getRuntimeConfig(agentInfo);
  const availability = runtimeLaunchAvailability("pi");
  if (!availability.available) throw new Error(availability.message);
  const launcher = defaultPiCommand();
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  const residentSessionId = String(agentInfo.sessionHandle || "").trim();
  const cwd = agentInfo.cwd || process.cwd();
  const timeoutMs = Number(config.timeoutMs || 12 * 60 * 60 * 1000);
  const model = normalizePiModelOverride(agentInfo.model || config.model || "");
  const thinking = String(config.thinking || config.effort || "").trim();
  let sessionId =
    executionMode === "resident"
      ? residentSessionId
      : String(runtimeState?.sessionId || runtimeState?.sessionFile || residentSessionId || "").trim();

  if (executionMode === "resident" && !sessionId) {
    throw new Error(
      `Resident Pi session "${agentId}" has no bound session ID. ` +
      "Start omp-aify or pi-aify with a resumable session or pass sessionHandle explicitly when registering.",
    );
  }

  const startupTimeoutMs = Number(config.startupTimeoutMs || process.env.AIFY_PI_STARTUP_TIMEOUT_MS || 15000);

  let interrupted = false;
  let settled = false;
  let proc = null;
  let attemptTimer = null;
  let startupTimer = null;
  let promptAcked = false;
  let finalText = "";
  let finalSnapshotText = "";
  let finalError = "";
  let stderrText = "";
  let sessionFile = String(runtimeState?.sessionFile || "").trim();
  let initialPromptSent = false;
  let requestCounter = 1;
  let healAttempted = false;
  const pendingCommandAcks = new Map();

  const nextRequestId = (prefix) => `aify-${prefix}-${requestCounter++}`;
  const resolvedText = () => finalText.trim() || finalSnapshotText.trim();
  const runtimeSessionHandle = () => sessionId || sessionFile;
  const runtimeStateSnapshot = () => ({
    ...(sessionId ? { sessionId } : {}),
    ...(sessionFile ? { sessionFile } : {}),
  });
  const failureText = () => [finalError, finalText, stderrText].filter(Boolean).join("\n").trim();
  const buildArgs = () => {
    const nextArgs = [...launcher.args, "--mode", "rpc"];
    if (sessionId) nextArgs.push("--resume", sessionId);
    if (model) nextArgs.push("--model", model);
    if (thinking) nextArgs.push("--thinking", thinking);
    return nextArgs;
  };
  const clearAttemptTimers = () => {
    if (attemptTimer) clearTimeout(attemptTimer);
    if (startupTimer) clearTimeout(startupTimer);
    attemptTimer = null;
    startupTimer = null;
  };
  const resetAttemptState = () => {
    promptAcked = false;
    finalText = "";
    finalSnapshotText = "";
    finalError = "";
    stderrText = "";
    initialPromptSent = false;
    rejectPendingCommandAcks(new Error("Pi runtime restarting with a fresh session"));
  };
  const publishPiSessionState = (event) => {
    const next = extractPiSessionState(event);
    let changed = false;
    if (next.sessionId && next.sessionId !== sessionId) {
      sessionId = next.sessionId;
      changed = true;
    }
    if (next.sessionFile && next.sessionFile !== sessionFile) {
      sessionFile = next.sessionFile;
      changed = true;
    }
    if (changed || next.sessionId || next.sessionFile) {
      const handle = runtimeSessionHandle();
      callbacks.onRuntimeState?.(runtimeStateSnapshot());
      if (handle) callbacks.onRefs?.({ threadId: handle });
    }
  };

  function send(payload) {
    if (!proc || !proc.stdin?.writable || proc.stdin.destroyed) return false;
    try {
      proc.stdin.write(`${JSON.stringify(payload)}\n`);
      return true;
    } catch {
      return false;
    }
  }

  function sendInitialPrompt() {
    if (initialPromptSent) return;
    initialPromptSent = true;
    send({
      id: nextRequestId("prompt"),
      type: "prompt",
      message: `${buildSystemPrompt(agentId, agentInfo, run)}\n\n${buildUserPrompt(run)}`,
    });
  }

  function rejectPendingCommandAcks(error) {
    for (const pending of pendingCommandAcks.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    pendingCommandAcks.clear();
  }

  function sendCommandWithAck(payload, prefix = payload?.type || "command", timeoutMs = 30000) {
    const id = nextRequestId(prefix);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pendingCommandAcks.delete(id);
        reject(new Error(`Pi ${String(payload?.type || "command")} acknowledgement timed out`));
      }, timeoutMs);
      pendingCommandAcks.set(id, { resolve, reject, timer, command: String(payload?.type || "command") });
      if (!send({ id, ...payload })) {
        clearTimeout(timer);
        pendingCommandAcks.delete(id);
        reject(new Error(`Pi ${String(payload?.type || "command")} could not be sent because the runtime stdin is closed`));
      }
    });
  }

  const promise = new Promise((resolve, reject) => {
    const fail = (error) => {
      if (settled) return;
      settled = true;
      clearAttemptTimers();
      rejectPendingCommandAcks(error);
      try {
        terminateProcessTree(proc);
      } catch {
        // best effort
      }
      reject(error);
    };

    const maybeHealMissingSession = () => {
      const detected = detectPiRuntimeFailure(failureText());
      if (!detected.shouldHeal || !sessionId || healAttempted || executionMode === "resident") return false;
      const previous = sessionId;
      healAttempted = true;
      callbacks.onEvent?.("thread", `Pi session "${previous}" is not resumable (${detected.message}); starting fresh.`);
      clearAttemptTimers();
      sessionId = "";
      sessionFile = "";
      callbacks.onRuntimeState?.({});
      callbacks.onSessionHandleChange?.("", { reason: detected.healReason, previous });
      resetAttemptState();
      startAttempt();
      return true;
    };

    const startAttempt = () => {
      const args = buildArgs();
      proc = spawnProcess(launcher.command, args, { cwd });
      proc.stdin?.on?.("error", () => {});
      callbacks.onEvent?.("thread", `Started ${executionMode} Pi RPC runtime${sessionId ? ` for session ${sessionId}` : ""}`);

      attemptTimer = setTimeout(() => {
        if (!settled) {
          interrupted = true;
          try {
            send({ id: nextRequestId("abort"), type: "abort" });
          } catch {
            // best effort
          }
          terminateProcessTree(proc);
          fail(new Error(`Pi run timed out after ${timeoutMs}ms`));
        }
      }, timeoutMs);

      startupTimer = setTimeout(() => {
        if (settled || initialPromptSent) return;
        const detected = detectPiRuntimeFailure(failureText());
        if (detected.authFailure) {
          fail(new Error(`Pi authentication failed fast: ${detected.message}`));
          return;
        }
        fail(new Error(`Pi did not become ready within ${startupTimeoutMs}ms. Check Oh My Pi authentication/provider configuration and run "omp" manually in this environment.`));
      }, Math.max(250, startupTimeoutMs));

      proc.on("error", (error) => {
        clearAttemptTimers();
        rejectPendingCommandAcks(error);
        if (error && error.code === "ENOENT") {
          const piTarget = String(process.env.AIFY_PI_COMMAND || process.env.PI_COMMAND || "omp").trim();
          const enriched = new Error(
            `spawn "${launcher.command}" ENOENT — this bridge resolved Oh My Pi to "${launcher.command}" ` +
            `but Node could not execute it. Common causes: missing exec bit, broken shebang interpreter ` +
            `(e.g., the script's #!/usr/bin/env node points at a node that isn't on the bridge's PATH), ` +
            `or a stale symlink. Also verify the runtime cwd exists: "${cwd}". ` +
            `Fix: set AIFY_PI_COMMAND to an absolute path to a real "omp" binary and ` +
            `restart aify-comms. Diagnostic: ${diagnosticsFor(piTarget)}`,
          );
          enriched.code = error.code;
          enriched.originalError = error.message;
          fail(enriched);
          return;
        }
        fail(error);
      });

      const stdout = readline.createInterface({ input: proc.stdout });
      stdout.on("line", (line) => {
        const text = String(line || "").trim();
        if (!text) return;
        let event;
        try {
          event = JSON.parse(text);
        } catch {
          finalText += `${text}\n`;
          const detected = detectPiRuntimeFailure(text);
          if (detected.authFailure) fail(new Error(`Pi authentication failed fast: ${detected.message}`));
          return;
        }

        publishPiSessionState(event);

        if (event.type === "ready") {
          if (startupTimer) clearTimeout(startupTimer);
          startupTimer = null;
          sendCommandWithAck({ type: "get_state" }, "get-state", 2500)
            .then((stateEvent) => publishPiSessionState(stateEvent))
            .catch((error) => callbacks.onEvent?.("pi", `Pi get_state unavailable: ${quoteForDisplay(error?.message || error)}`))
            .finally(() => sendInitialPrompt());
          return;
        }

        if (event.type === "response") {
          const pending = pendingCommandAcks.get(event.id);
          if (pending) {
            pendingCommandAcks.delete(event.id);
            clearTimeout(pending.timer);
            if (event.success === false) {
              pending.reject(new Error(String(event.error || `Pi ${pending.command} failed`)));
            } else {
              publishPiSessionState(event);
              pending.resolve(event);
            }
            return;
          }
          if (event.command === "prompt") {
            promptAcked = event.success !== false;
            if (event.success === false) {
              finalError = String(event.error || "Pi prompt failed");
              const detected = detectPiRuntimeFailure(finalError);
              if (detected.authFailure) fail(new Error(`Pi authentication failed fast: ${detected.message}`));
            }
          }
          return;
        }

        if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_delta") {
          finalText += String(event.assistantMessageEvent.delta || "");
          return;
        }

        if (event.type === "message_update" && event.assistantMessageEvent?.type === "text_end") {
          finalSnapshotText = String(event.assistantMessageEvent.content || finalSnapshotText || "");
          return;
        }

        if (event.type === "message_end" || event.type === "turn_end") {
          const text = extractPiAssistantText(event.message);
          if (text) finalSnapshotText = text;
          return;
        }

        if (event.type === "agent_start") {
          callbacks.onEvent?.("pi", "Started Pi agent turn");
          return;
        }

        if (event.type === "agent_end") {
          const text = extractPiAssistantText(event.messages);
          if (text) finalSnapshotText = text;
          settled = true;
          clearAttemptTimers();
          rejectPendingCommandAcks(new Error("Pi run ended before steer acknowledgement"));
          callbacks.onRuntimeState?.(runtimeStateSnapshot());
          if (runtimeSessionHandle()) callbacks.onRefs?.({ threadId: runtimeSessionHandle() });
          resolve({
            status: interrupted ? "cancelled" : "completed",
            summary: resolvedText() || "(no output)",
            runtimeState: runtimeStateSnapshot(),
            externalRefs: { threadId: runtimeSessionHandle(), turnId: String(event.id || "") },
          });
          try {
            terminateProcessTree(proc);
          } catch {
            // ignore shutdown errors
          }
          return;
        }

        if (event.type === "error") {
          finalError = String(event.error || event.message || "Pi runtime error");
          const detected = detectPiRuntimeFailure(finalError);
          if (detected.authFailure) fail(new Error(`Pi authentication failed fast: ${detected.message}`));
        }
      });

      const stderr = readline.createInterface({ input: proc.stderr });
      stderr.on("line", (line) => {
        const text = quoteForDisplay(line);
        if (!text) return;
        stderrText += `${text}\n`;
        callbacks.onEvent?.("stderr", text);
        const detected = detectPiRuntimeFailure(text);
        if (detected.authFailure) fail(new Error(`Pi authentication failed fast: ${detected.message}`));
      });

      proc.on("close", (code) => {
        if (settled) return;
        if (maybeHealMissingSession()) return;
        settled = true;
        clearAttemptTimers();
        rejectPendingCommandAcks(new Error(finalError || finalText.trim() || stderrText.trim() || `Pi exited with code ${code}`));
        if (interrupted) {
          resolve({
            status: "cancelled",
            summary: resolvedText() || finalError || "Run interrupted",
            runtimeState: runtimeStateSnapshot(),
            externalRefs: { threadId: runtimeSessionHandle() },
          });
          return;
        }
        if (code === 0 && promptAcked && !finalError) {
          resolve({
            status: "completed",
            summary: resolvedText() || "(no output)",
            runtimeState: runtimeStateSnapshot(),
            externalRefs: { threadId: runtimeSessionHandle() },
          });
          return;
        }
        const detected = detectPiRuntimeFailure(failureText());
        if (detected.authFailure) {
          reject(new Error(`Pi authentication failed fast: ${detected.message}`));
          return;
        }
        if (detected.missingSession && executionMode === "resident") {
          reject(new Error(`Resident Pi session "${sessionId}" is not resumable: ${detected.message}. Clear the saved session handle or start a fresh managed Pi session.`));
          return;
        }
        reject(new Error(finalError || finalText.trim() || stderrText.trim() || `Pi exited with code ${code}`));
      });
    };

    startAttempt();
  });

  return {
    capabilities: controlCapabilitiesForRuntime("pi"),
    interrupt: async () => {
      interrupted = true;
      send({ id: nextRequestId("abort"), type: "abort" });
      terminateProcessTree(proc);
    },
    steer: async (text) => {
      const message = String(text || "");
      if (!message.trim()) {
        throw new Error("Steer body is required");
      }
      if (!proc || !proc.stdin?.writable || settled) {
        throw new Error("No active Pi turn to steer");
      }
      await sendCommandWithAck({ type: "steer", message }, "steer");
      callbacks.onEvent?.("steer", "Steer sent to active Pi RPC run");
    },
    promise,
  };
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

export function defaultCapabilitiesForRuntime(runtime, sessionMode = "resident", sessionHandle = "") {
  const normalizedRuntime = normalizeRuntime(runtime);
  const normalizedMode = String(sessionMode || "resident").trim().toLowerCase();
  const resolvedSessionHandle = String(sessionHandle || defaultSessionHandleForRuntime(normalizedRuntime) || "").trim();
  const runtimeConfig = arguments.length > 3 ? arguments[3] || {} : {};

  if (normalizedMode === "managed") {
    switch (normalizedRuntime) {
      case "codex":
        return ["managed-run", "resume", "interrupt", "steer", "spawn"];
      case "hermes":
        return ["managed-run", "resume", "interrupt", "spawn"];
      case "opencode":
        return ["managed-run", "resume", "interrupt", "spawn"];
      case "pi":
        return ["managed-run", "resume", "interrupt", "steer", "spawn"];
      case "claude-code":
        return ["managed-run", "resume", "interrupt", "spawn"];
      default:
        return [];
    }
  }

  if (normalizedRuntime === "claude-code") {
    if (!hasClaudeLiveChannel(runtimeConfig)) return [];
    return ["resident-run", "interrupt", "steer"];
  }

  if (!resolvedSessionHandle) return [];
  switch (normalizedRuntime) {
    case "codex":
      if (!hasCodexLiveAppServer(runtimeConfig) && !canUseDefaultResidentCodexBridge()) return [];
      return ["resident-run", "resume", "interrupt", "steer"];
    case "hermes":
      return ["resident-run", "resume", "interrupt"];
    case "opencode":
      return ["resident-run", "resume", "interrupt"];
    case "pi":
      return ["resident-run", "resume", "interrupt", "steer"];
    default:
      return [];
  }
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

export function launchRuntimeRun({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const runtime = normalizeRuntime(agentInfo.runtime || "generic");
  try {
    if (runtime === "codex") {
      return createCodexController({ agentId, agentInfo, run, runtimeState, callbacks });
    }
    if (runtime === "opencode") {
      return createOpenCodeController({ agentId, agentInfo, run, runtimeState, callbacks });
    }
    if (runtime === "pi") {
      return createPiController({ agentId, agentInfo, run, runtimeState, callbacks });
    }
    if (runtime === "claude-code") {
      return createClaudeController({ agentId, agentInfo, run, runtimeState, callbacks });
    }
    if (runtime === "hermes") {
      // Hermes is a first-class SPAWN + dashboard-console (PTY) runtime — the
      // shared Pi/Hermes terminal substrate handles it. It is intentionally
      // not a bridge active-dispatch controller. Fail fast with an actionable
      // message instead of the generic "does not support active dispatch"
      // reject, which previously made a claimed Hermes run look broken (R4).
      return createTerminalDeliveryController("hermes");
    }
  } catch (error) {
    return failedRuntimeController(runtime, error);
  }
  return {
    capabilities: controlCapabilitiesForRuntime(runtime),
    interrupt: () => {},
    steer: async () => {
      throw new Error(`Runtime "${runtime}" does not support active dispatch`);
    },
    promise: Promise.reject(new Error(`Runtime "${runtime}" does not support active dispatch`)),
  };
}

function createTerminalDeliveryController(runtime) {
  // Runtimes whose only execution surface is the dashboard console/terminal
  // PTY (not bridge active-dispatch). Returns a controller that rejects an
  // active-dispatch claim with a clear, actionable message so the run does
  // not look mysteriously "unsupported".
  const message =
    `Runtime "${runtime}" runs via the dashboard Console (terminal/PTY), not bridge active dispatch. ` +
    `Spawn it from a connected environment and drive it through its Console; ` +
    `it will not be claimed for managed/resident active-dispatch turns.`;
  return {
    capabilities: controlCapabilitiesForRuntime(runtime),
    interrupt: () => {},
    steer: async () => {
      throw new Error(message);
    },
    promise: Promise.reject(new Error(message)),
  };
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
