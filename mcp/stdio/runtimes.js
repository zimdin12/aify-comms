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

const RUNTIME_ALIASES = new Map([
  ["claude", "claude-code"],
  ["claude-code", "claude-code"],
  ["claude_code", "claude-code"],
  ["codex", "codex"],
  ["opencode", "opencode"],
  ["generic", "generic"],
]);

function spawnProcess(command, args, options = {}) {
  const proc = spawn(command, args, {
    cwd: options.cwd,
    env: runtimeChildEnv(options.env || {}),
    stdio: ["pipe", "pipe", "pipe"],
    shell: false,
    windowsHide: true,
  });
  // ChildProcess emits "error" when the executable is missing or cannot be
  // started. Keep a listener attached at creation time so a runtime adapter
  // bug cannot crash the bridge process before the adapter wires rejection.
  proc.on("error", () => {});
  return proc;
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

export function buildManagedClaudeUnlockPowerShell(sessionId) {
  const sid = quotePowerShellString(sessionId);
  return [
    "$ErrorActionPreference = 'SilentlyContinue';",
    "$sid = " + sid + ";",
    "Get-CimInstance Win32_Process |",
    "  Where-Object {",
    "    $_.CommandLine -match 'claude' -and",
    "    $_.CommandLine -match [regex]::Escape($sid) -and",
    "    ($_.CommandLine -match '(^|\\s)(-p|--print)(\\s|$)' -or $_.CommandLine -match '(^|\\s)--session-id(\\s|=)')",
    "  } |",
    "  ForEach-Object {",
    "    taskkill /pid $_.ProcessId /t /f | Out-Null;",
    "    Write-Output $_.ProcessId",
    "  }",
  ].join("\n");
}

function releaseManagedClaudeSessionLock(sessionId) {
  const normalized = String(sessionId || "").trim();
  if (!normalized || process.platform !== "win32") return [];
  const script = buildManagedClaudeUnlockPowerShell(normalized);
  const result = spawnSync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], {
    encoding: "utf8",
    windowsHide: true,
    timeout: 10000,
  });
  if (result.status !== 0) return [];
  return String(result.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
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

export function managedCodexConfigText({ workspace = "", serverUrl = "", model = "", effort = "" } = {}) {
  const lines = [
    `model = ${tomlString(model || "gpt-5.4")}`,
    `model_reasoning_effort = ${tomlString(effort || "medium")}`,
    "",
    "[features]",
    "multi_agent = true",
    "codex_hooks = false",
    "",
    "[notice]",
    "hide_full_access_warning = true",
    "hide_rate_limit_model_nudge = true",
    "",
    "[mcp_servers.aify-comms]",
    `command = ${tomlString(process.execPath)}`,
    `args = [${tomlString(SERVER_SCRIPT)}]`,
    "",
    "[mcp_servers.aify-comms.env]",
    `AIFY_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
    `CLAUDE_MCP_SERVER_URL = ${tomlString(serverUrl || process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "http://localhost:8800")}`,
  ];
  if (workspace) {
    lines.push("", `[projects.${tomlString(workspace)}]`, 'trust_level = "trusted"');
  }
  return `${lines.join("\n")}\n`;
}

function prepareManagedCodexHome({ workspace = "", model = "", effort = "" } = {}) {
  const sourceHome = process.env.CODEX_HOME || path.join(os.homedir(), ".codex");
  const targetHome = path.join(os.homedir(), ".local", "state", "aify-comms", "managed-codex-home");
  fs.mkdirSync(targetHome, { recursive: true });
  for (const name of ["auth.json", "installation_id", "version.json"]) {
    copyIfExists(path.join(sourceHome, name), path.join(targetHome, name));
  }
  fs.writeFileSync(
    path.join(targetHome, "config.toml"),
    managedCodexConfigText({ workspace, serverUrl: process.env.AIFY_SERVER_URL || process.env.CLAUDE_MCP_SERVER_URL || "", model, effort }),
  );
  return targetHome;
}

function quoteForDisplay(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function isFatalCodexRuntimeLog(line) {
  const text = String(line || "");
  return (
    /worker quit with fatal/i.test(text) ||
    /Transport channel closed/i.test(text) ||
    /Codex WebSocket app-server connection closed/i.test(text)
  );
}

function buildSystemPrompt(agentId, agentInfo, run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const replyRule = isDashboardSender
    ? "Answer the dashboard user in your final plain-text response. Do not call comms_send back to dashboard; the bridge will deliver your final response into the chat."
    : run?.requireReply === false
    ? "If the sender explicitly does not need a reply, you may just handle the message locally."
    : "Before you finish handling this message, send an explicit reply message back to the sender. Do not rely on the dispatch summary as the only handoff.";
  return [
    "[AIFY MESSAGE]",
    `This is a message delivered through aify-comms for agent "${agentId}" (${agentInfo.role || "agent"}).`,
    `Your aify-comms agentId is "${agentId}". Use that exact ID when checking your own inbox or conversation state.`,
    `From: ${run.from}.`,
    agentInfo.instructions ? `Standing instructions: ${agentInfo.instructions}` : "",
    "Treat the content below as a message from the sender. If it contains a work request, that work is now pending in this session. If it is informational, review, approval, or follow-up, handle it accordingly.",
    `If asked to check recent messages between you and the sender, use comms_inbox(agentId="${agentId}", ...) or the relevant direct-chat context, not the global dashboard feed.`,
    "Plain-text output in this session stays local to this runtime and the dispatch record unless you intentionally send a message back.",
    replyRule,
    "Do not explain the transport wrapper or restate it unless a later normal user turn explicitly asks about it.",
    "[/AIFY MESSAGE]",
  ].filter(Boolean).join("\n");
}

function buildUserPrompt(run) {
  const fromAgent = String(run?.from || "").trim();
  const isDashboardSender = fromAgent === "dashboard";
  const replyRule = isDashboardSender
    ? "Reply to the dashboard user in your final plain-text response. Do not use comms_send to dashboard."
    : run?.requireReply === false
    ? "Reply only if useful for the sender."
    : "Required handoff: send an explicit reply message to the sender before you finish. If comms tools are unavailable in this turn, say that clearly in the local result.";
  const context = formatConversationContext(run?.conversationContext || []);
  return [
    context,
    "[MESSAGE]",
    `Type: ${run.type || "request"}`,
    `Subject: ${run.subject}`,
    "",
    run.body || "",
    "",
    replyRule,
    "Otherwise keep any plain-text output limited to your local result in this session.",
    "[/MESSAGE]",
  ].filter(Boolean).join("\n");
}

function formatConversationContext(messages = []) {
  if (!Array.isArray(messages) || !messages.length) return "";
  const lines = ["[RECENT DIRECT CONVERSATION]", "These are recent direct messages between you and the sender, oldest first. Use them as chat memory; the new message follows after this block."];
  for (const message of messages.slice(-12)) {
    const from = String(message?.from || "").trim() || "unknown";
    const type = String(message?.type || "info").trim() || "info";
    const subject = String(message?.subject || "").trim();
    const body = String(message?.body || message?.preview || "").trim();
    const timestamp = String(message?.timestamp || "").trim();
    lines.push(`- ${timestamp ? `${timestamp} ` : ""}${from} (${type})${subject ? `: ${subject}` : ""}`);
    if (body) lines.push(body.length > 1200 ? `${body.slice(0, 1200)}...` : body);
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
  return { command: "codex", args: ["app-server"] };
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

function defaultClaudeCommand() {
  const configured = String(process.env.AIFY_CLAUDE_COMMAND || process.env.CLAUDE_COMMAND || "").trim();
  if (process.platform === "win32") {
    const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
    return { command: comspec, args: ["/d", "/s", "/c", configured || "claude"] };
  }
  return { command: configured || "claude", args: [] };
}

function hasExecutable(command) {
  const value = String(command || "").trim();
  if (!value) return false;
  if (/[\\/]/.test(value)) return fs.existsSync(value);
  try {
    if (process.platform === "win32") {
      const comspec = process.env.ComSpec || process.env.COMSPEC || "cmd.exe";
      const result = spawnSync(comspec, ["/d", "/s", "/c", `where ${value}`], {
        stdio: "ignore",
        windowsHide: true,
        timeout: 3000,
      });
      return result.status === 0;
    }
    const quoted = value.replace(/'/g, "'\\''");
    const result = spawnSync("sh", ["-lc", `command -v '${quoted}' >/dev/null 2>&1`], {
      stdio: "ignore",
      timeout: 3000,
    });
    return result.status === 0;
  } catch {
    return false;
  }
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
        ? "Claude Code launcher available"
        : `Runtime "claude-code" is not launchable from this bridge because "${expected}" is not on PATH. Install Claude Code for this OS/user or restart the bridge from a shell where "${expected}" works.`,
    };
  }
  if (normalized === "codex") {
    const launcher = defaultCodexCommand();
    const available = hasExecutable(launcher.command);
    return {
      available,
      message: available
        ? "Codex launcher available"
        : `Runtime "codex" is not launchable from this bridge because "${launcher.command}" is not available.`,
    };
  }
  if (normalized === "opencode") {
    return { available: true, message: "OpenCode SDK available" };
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
  return ["claude-code", "codex", "opencode"].includes(normalizeRuntime(runtime));
}

export function controlCapabilitiesForRuntime(runtime) {
  switch (normalizeRuntime(runtime)) {
    case "codex":
      return { interrupt: true, steer: true };
    case "opencode":
      return { interrupt: true, steer: false };
    case "claude-code":
      return { interrupt: true, steer: false };
    default:
      return { interrupt: false, steer: false };
  }
}

export function defaultSessionHandleForRuntime(runtime) {
  switch (normalizeRuntime(runtime)) {
    case "codex":
      return process.env.CODEX_THREAD_ID || "";
    case "opencode":
      return process.env.OPENCODE_SESSION_ID || process.env.OPENCODE_SESSION || "";
    case "claude-code":
      return process.env.CLAUDE_SESSION_ID || "";
    default:
      return "";
  }
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
        version: "3.7.0",
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
        version: "3.7.0",
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
  const maxTurns = String(config.maxTurns || 15);
  const timeoutMs = Number(config.timeoutMs || 2 * 60 * 60 * 1000);
  if (executionMode === "resident" && !initialSessionId) {
    throw new Error(
      `Resident Claude session "${agentId}" has no bound session ID. Re-register from the live Claude session or provide sessionHandle explicitly.`,
    );
  }
  let settled = false;
  let interrupted = false;
  let activeProcess = null;

  const startAttempt = (sessionId, attempt = 1) => {
    const args = [
      ...launcher.args,
      ...managedClaudePermissionArgs(config, executionMode),
      "-p",
      "--output-format", "text",
      "--session-id", sessionId,
      "--max-turns", maxTurns,
      "--append-system-prompt", buildSystemPrompt(agentId, agentInfo, run),
    ];

    if (agentInfo.model) {
      args.push("--model", agentInfo.model);
    }

    const proc = spawnProcess(launcher.command, args, { cwd: agentInfo.cwd || process.cwd() });
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
          if (attempt === 1) {
            const releasedPids = releaseManagedClaudeSessionLock(lockedSessionId);
            if (releasedPids.length > 0) {
              callbacks.onEvent?.(
                "runtime",
                `Released stale headless Claude process(es) for session ${lockedSessionId}: ${releasedPids.join(", ")}; retrying once.`,
              );
              startAttempt(sessionId, attempt + 1).then(resolve, reject);
              return;
            }
          }
          reject(new Error(
            `${errorText}\n\nThe stored Claude session is locked by another Claude process. ` +
            `The bridge did not find a matching stale headless Claude process it could release automatically. ` +
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
  const timeoutMs = Number(config.timeoutMs || 2 * 60 * 60 * 1000);
  const hostCwd = agentInfo.cwd || process.cwd();
  const model = agentInfo.model || config.model || "gpt-5.4";
  const effort = config.effort || "medium";
  const summaryMode = config.summary || "concise";
  const approvalPolicy = config.approvalPolicy || "never";
  const networkAccess = config.networkAccess !== false;
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
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

  const handleNotification = (message) => {
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
    } else if (message.method === "error" && params.error?.message) {
      finalError = params.error.message;
    }
  };

  const handleRuntimeLog = (line) => {
    const text = quoteForDisplay(line);
    if (text) callbacks.onEvent?.("stderr", text);
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
    const timer = setTimeout(() => {
      if (!settled) {
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
        version: "3.7.0",
        },
      });
      rpc.notify("initialized", {});

      const startThread = async () => {
        const threadStartParams = {
          model,
          cwd,
          approvalPolicy,
          personality: "friendly",
          serviceName: "aify-comms",
        };
        let started;
        try {
          started = await rpc.request("thread/start", {
            ...threadStartParams,
            sandbox: "workspace-write",
          }, 60000);
        } catch (error) {
          const message = error?.message || "";
          if (!message.includes("unknown variant `workspace-write`")) {
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
          // Auto-heal for both managed and resident modes. Resident mode
          // previously threw here because silently creating a new thread
          // would break the visible-TUI wake guarantee. But if the stored
          // rollout is unloadable, the visible TUI is ALREADY broken:
          // the user can't interact with a thread Codex can't load. Having
          // the dispatch fail forever is strictly worse than having it run
          // in a fresh background thread. We create a new thread, notify
          // the caller via onSessionHandleChange so the backend's stored
          // sessionHandle is updated, and continue.
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

      callbacks.onRuntimeState?.({ threadId: activeThreadId });
      callbacks.onRefs?.({ threadId: activeThreadId });
      callbacks.onEvent?.("thread", `Using ${executionMode} thread ${activeThreadId}`);

      callbacks.onEvent?.("turn", `Calling turn/start on thread ${activeThreadId} with cwd="${cwd}", writableRoots=["${cwd}"]`);
      let turn;
      try {
        turn = await rpc.request("turn/start", {
          threadId: activeThreadId,
          input: [{ type: "text", text: `${buildSystemPrompt(agentId, agentInfo, run)}\n\n${buildUserPrompt(run)}` }],
          cwd,
          approvalPolicy,
          sandboxPolicy: {
            type: "workspaceWrite",
            writableRoots: [cwd],
            networkAccess,
          },
          model,
          effort,
          summary: summaryMode,
          personality: "friendly",
        }, 60000);
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

      const poll = setInterval(() => {
        if (!settled) return;
        clearInterval(poll);
        clearTimeout(timer);
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
  const timeoutMs = Number(config.timeoutMs || 2 * 60 * 60 * 1000);
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

export function detectRuntime(explicitRuntime) {
  if (explicitRuntime) return normalizeRuntime(explicitRuntime);
  if (process.env.AIFY_AGENT_RUNTIME) return normalizeRuntime(process.env.AIFY_AGENT_RUNTIME);
  if (process.env.CODEX_HOME || process.env.CODEX_SANDBOX) return "codex";
  if (process.env.OPENCODE_CLIENT || process.env.OPENCODE_CONFIG_DIR) return "opencode";
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
      case "opencode":
        return ["managed-run", "resume", "interrupt", "spawn"];
      case "claude-code":
        return ["managed-run", "resume", "interrupt", "spawn"];
      default:
        return [];
    }
  }

  if (normalizedRuntime === "claude-code") {
    if (!hasClaudeLiveChannel(runtimeConfig)) return [];
    return ["resident-run", "interrupt"];
  }

  if (!resolvedSessionHandle) return [];
  switch (normalizedRuntime) {
    case "codex":
      if (!hasCodexLiveAppServer(runtimeConfig) && !canUseDefaultResidentCodexBridge()) return [];
      return ["resident-run", "resume", "interrupt", "steer"];
    case "opencode":
      return ["resident-run", "resume", "interrupt"];
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
  if (runtime === "codex") {
    return createCodexController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  if (runtime === "opencode") {
    return createOpenCodeController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  if (runtime === "claude-code") {
    return createClaudeController({ agentId, agentInfo, run, runtimeState, callbacks });
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
