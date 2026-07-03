// runtimes-codex.js — managed-Codex helpers: home/config preparation,
// thread rollout import, sandbox policy, launcher/cwd resolution, and live
// app-server thread discovery. Extracted verbatim from runtimes.js
// (task #123). runtimes.js re-exports the public surface.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { listRuntimeMarkers } from "./runtime-markers.js";
import { resolveCodexRequestCwdFor } from "./codex-errors.js";
import { userHomeDir, tokenizeCommandString } from "./runtimes-process.js";
import { resolveExecutable } from "./runtimes-exec.js";
import { createWebSocketRpcClient } from "./runtimes-rpc.js";

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
  // A bare websocket-close line (e.g. a transient 1006/1000/1001) is NOT
  // fatal on its own — classifying it fatal here tore down the shared
  // app-server and failed the turn on every transient disconnect. Let the
  // existing recover/resume path handle a plain close; only genuinely
  // unrecoverable signals (worker fatal / transport channel torn down)
  // remain instant-fatal.
  return (
    /worker quit with fatal/i.test(text) ||
    /Transport channel closed/i.test(text)
  );
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
  // Precedence bug (bughunt 2026-07-03): the old ternary ALWAYS composed
  // HOMEDRIVE+HOMEPATH in its truthy branch, ignoring USERPROFILE even when set —
  // on a roaming/mapped-drive profile that yields an inaccessible H:\… and aborts
  // the launch with AIFY_INVALID_RUNTIME_CWD. Prefer the real USERPROFILE.
  if (process.env.USERPROFILE) return process.env.USERPROFILE;
  if (process.env.HOMEDRIVE && process.env.HOMEPATH) return `${process.env.HOMEDRIVE}${process.env.HOMEPATH}`;
  return "C:\\";
}

export function hasCodexLiveAppServer(runtimeConfig = {}) {
  const url = String(runtimeConfig?.appServerUrl || "").trim();
  return /^wss?:\/\//i.test(url);
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
