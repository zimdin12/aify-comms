// runtimes.js — runtime registry core: alias/normalization, capability
// derivation, launch availability, session-handle helpers, machine id, and
// the launchRuntimeRun adapter entry point.
//
// Task #123 split the per-concern helper groups into sibling modules
// (runtimes-process/exec/rpc/prompts/claude/codex/hermes/pi/opencode). This
// file re-exports their full public surface so every existing importer of
// "./runtimes.js" keeps working unchanged.
import os from "os";
import fs from "fs";
import { adapterFor } from "./adapters/index.js";
import { resolveExecutable, hasExecutable, diagnosticsFor } from "./runtimes-exec.js";
import { staleClaudeAifyWrapperReason } from "./runtimes-claude.js";
import { defaultPiCommand } from "./runtimes-pi.js";

// --- re-exported per-concern modules (public surface unchanged) ---
export {
  tokenizeCommandString,
  spawnProcess,
  launchCwdProblem,
  descendantPids,
  terminateProcessTree,
  pidIsSelfProtected,
  runtimeChildEnv,
} from "./runtimes-process.js";
export {
  describeExecutableResolution,
  diagnosticsFor,
} from "./runtimes-exec.js";
export {
  quoteForDisplay,
  createRpcClient,
  createWebSocketRpcClient,
  codexAppServerReachable,
} from "./runtimes-rpc.js";
export {
  buildSystemPrompt,
  buildUserPrompt,
} from "./runtimes-prompts.js";
export {
  splitProviderModel,
  opencodePermissionConfig,
  summarizeOpenCodeParts,
  requireOpenCodeData,
} from "./runtimes-opencode.js";
export {
  extractPiAssistantText,
  extractPiSessionState,
  normalizePiModelOverride,
  detectPiRuntimeFailure,
  defaultPiCommand,
} from "./runtimes-pi.js";
export {
  defaultHermesCommand,
} from "./runtimes-hermes.js";
export {
  claudeSessionTranscriptPath,
  claudeSessionTranscriptExists,
  buildManagedClaudeUnlockPowerShell,
  managedClaudePermissionArgs,
  managedClaudeModel,
  managedClaudeEffort,
  managedClaudeMaxTurns,
  isClaudeSessionInUseError,
} from "./runtimes-claude.js";
export {
  findCodexThreadFiles,
  importCodexThreadRollout,
  managedCodexConfigText,
  prepareManagedCodexHome,
  describeCodexItem,
  isAifyCommsMcpToolItem,
  isFatalCodexRuntimeLog,
  managedCodexEffort,
  managedCodexSandboxMode,
  codexTurnSandboxPolicy,
  defaultCodexCommand,
  resolveCodexRequestCwd,
  codexSpawnCwd,
  hasCodexLiveAppServer,
  discoverCodexLiveThreadId,
  discoverCodexLiveBinding,
} from "./runtimes-codex.js";

// Exported so the vocabulary agreement test can compare the REAL map against
// service/contracts/vocabulary.json, rather than regex-parsing this file.
export const RUNTIME_ALIASES = new Map([
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

// WHICH RESUME FLAGS EACH RUNTIME'S LAUNCH COMMAND CAN CARRY, per the wrapper that parses them.
//
// This was an inline `key === "pi" || key === "hermes" || key === "claude-code"` alternation, and
// the two runtimes it left out are the point. Each adapter declares a `resumeCommand(id)` — the
// command that resumes that runtime — and `adapter-contract-symmetry.test.js` gates every adapter
// for having one. NOTHING connected those declarations to the regex that has to UNDO them:
//
//   codex     adapters/codex.js    -> `codex-aify --resume <id>`
//   opencode  adapters/opencode.js -> `opencode-aify --resume <id>`
//
// Neither form was recognised. `runtimeCommandWithoutResume` returned both UNCHANGED, so the
// "start a fresh session without --resume" heal in `terminal-runtime.js` — which only proceeds when
// the stripped command DIFFERS — could never fire for either, and
// `extractRuntimeSessionHandleFromCommand` reported no handle where one was plainly present, so
// `terminalChildEnv` would hand the worker an empty `CODEX_THREAD_ID`/`AIFY_SESSION_HANDLE`.
//
// The flag sets are not guessed. codex-aify parses exactly `--resume` and `--session-id` (both
// space- and `=`-separated) and NOT `-r`; see the codex branch of install.sh. opencode's adapter
// declares `--resume`. The three runtimes that already worked keep their existing set unchanged.
const RESUME_FLAGS_BY_RUNTIME = Object.freeze({
  "claude-code": ["--resume", "--session-id", "-r"],
  codex: ["--resume", "--session-id"],
  hermes: ["--resume", "--session-id", "-r"],
  opencode: ["--resume"],
  pi: ["--resume", "--session-id", "-r"],
});

const escapeForRegex = (value) => String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// A LIST, because codex has TWO resume syntaxes and one regex cannot carry both.
//
// `shellTokenFromMatch` reads capture groups 1-3, so folding a second alternative into one pattern
// would push its groups to 4-6 and silently yield "" for every match on that half — a wider regex
// that reports nothing. Separate patterns, tried in order, keep each one's groups at 1-3.
//
// Codex's SUBCOMMAND form is tried first so it keeps deciding the case it already decided: the
// dashboard renders `codex --no-alt-screen resume --include-non-interactive <handle>`, where the
// load-bearing part is the POSITIONAL id. The flag form is the wrapper/operator spelling.
function resumeRegexesForRuntime(runtime, flags = "") {
  const key = normalizeRuntime(runtime);
  const patterns = [];
  if (key === "codex") {
    patterns.push(new RegExp(String.raw`(?:^|\s)resume(?:\s+--include-non-interactive)?\s+${SHELL_TOKEN_PATTERN}`, flags));
  }
  const flagNames = RESUME_FLAGS_BY_RUNTIME[key] || [];
  if (flagNames.length) {
    const alternation = flagNames.map(escapeForRegex).join("|");
    patterns.push(new RegExp(String.raw`(?:^|\s)(?:${alternation})(?:=|\s+)${SHELL_TOKEN_PATTERN}`, flags));
  }
  return patterns;
}

export function extractRuntimeSessionHandleFromCommand(runtime = "", command = "") {
  const text = String(command || "");
  for (const regex of resumeRegexesForRuntime(runtime)) {
    const handle = shellTokenFromMatch(text.match(regex));
    if (handle) return handle;
  }
  return "";
}

export function runtimeCommandWithoutResume(runtime = "", command = "") {
  let text = String(command || "").trim();
  const regexes = resumeRegexesForRuntime(runtime, "g");
  if (!regexes.length) return text;
  for (const regex of regexes) text = text.replace(regex, " ");
  return text.replace(/\s+/g, " ").trim();
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

// Deterministic platform tag for machine_id, generic for ANY machine. WSL does
// NOT propagate WSL_DISTRO_NAME to every spawn context (present in interactive
// shells, absent in many child processes), so deriving the tag from it made the
// SAME machine register as both `wsl-<distro>:host` (env present) and
// `linux:host` (env absent). That divergence broke the machine_id match in
// dispatch-claim + bridge supersession — a WSL delivery loop registered as
// `wsl-ubuntu:host` could never claim runs for an agent recorded as
// `linux:host`, so deliveries sat queued forever (observed 2026-06-02). Detect
// WSL from /proc (visible to EVERY process on the machine, independent of env)
// and emit a STABLE `wsl` tag; native platforms keep process.platform. The host
// component is still the machine's own hostname, so this stays fully dynamic
// across everyone's PCs — nothing is hardcoded.
function stablePlatformTag() {
  if (process.platform === "linux") {
    try {
      if (/microsoft|wsl/i.test(fs.readFileSync("/proc/sys/kernel/osrelease", "utf8"))) {
        return "wsl";
      }
    } catch {
      // not WSL or /proc unreadable → fall through to the platform tag
    }
  }
  return process.platform;
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
  const wsl = stablePlatformTag();
  // Lowercase the whole "<platform>:<host>" id. Hostnames report with
  // inconsistent casing across launch paths (e.g. win32:DevBox-1 vs
  // win32:DEVBOX-1); the service compares machine_id case-insensitively
  // for bridge supersession, so send a consistent (lowercased) value.
  return `${wsl}:${host}`.toLowerCase();
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
