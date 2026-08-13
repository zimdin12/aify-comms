#!/usr/bin/env node
//
// aify-comms-mcp -- MCP server for inter-agent communication between coding-agent runtimes.
//
// Modes:
//   - Remote: set AIFY_SERVER_URL (or legacy CLAUDE_MCP_SERVER_URL) to use HTTP server
//   - Local: filesystem-based message bus in .messages/ directory
//

import { spawn } from "child_process";
import {
  SAFETY_HEADER,
  autoReplyBodyForRun,
  autoReplySubjectForRun,
  formatQueuedRun,
  replyExpectationSummary,
} from "./tool-response-format.mjs";

import {
  API_KEY,
  HTTP_TIMEOUT_MS,
  IS_REMOTE,
  SERVER_URL,
  SERVER_URLS,
  activeServerUrl,
  httpCall,
  isTransientHttpError,
} from "./aify-service-endpoint.mjs";
import { registerArtifactTools } from "./artifact-tools.mjs";
import { registerChannelTools } from "./channel-tools.mjs";
import { registerCompactTool } from "./compact-tool.mjs";
import { registerConsoleTools } from "./console-tools.mjs";
import { registerDashboardTool } from "./dashboard-tool.mjs";
import { registerDispatchTools } from "./dispatch-tools.mjs";
import { registerAgentReportingTools } from "./agent-reporting-tools.mjs";
import { registerEnvironmentTools } from "./environment-tools.mjs";
import { registerInboxTools } from "./inbox-tools.mjs";
import { registerLifecycleTools } from "./lifecycle-tools.mjs";
import { registerSearchTool } from "./search-tool.mjs";
import { registerSelfRecordTools } from "./self-record-tools.mjs";
import { registerUsageTool } from "./usage-tool.mjs";
import {
  INBOX_DIR, MESSAGES_DIR, SHARED_DIR,
  deliverMessage, readAgents, writeAgents,
} from "./local-store.mjs";
import {
  ACTIVE_RUNS, CONSECUTIVE_FAILURES, REMOTE_AGENT_STATE, forgetRemoteAgent,
} from "./bridge-agent-state.mjs";
import { __markControllerStart, anyControllerActive } from "./controller-activity.mjs";
import { parseJson } from "./parse-json.mjs";
import {
  DEFAULT_CWD,
  claimCapturedClaudeSession,
  normalizeRegistrationCwd,
  resolvedRuntimeConfigForRegistration,
  resolvedRuntimeMarker,
} from "./registration-inputs.mjs";
import {
  AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER, AIFY_HERMES_GATEWAY_URL,
} from "./hermes-gateway-config.mjs";
import {
  baseAgentHeartbeatFields, currentTurnHeartbeatFields, reportTurnBusy,
} from "./agent-heartbeat.mjs";
import { BRIDGE_INSTANCE_ID, BRIDGE_STARTED_AT } from "./bridge-instance.mjs";
import { clearLocalActiveRun, reconcileLocalActiveRun } from "./local-active-run.mjs";
import {
  armClaudeTurnEndDetector, isClaudeTurnDetectorArmed, stopClaudeTurnEndDetector,
} from "./claude-turn-detector-state.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import { normalizeSessionMode } from "./session-mode.mjs";
import { validateName } from "./safe-name.mjs";
import { AIFY_AGENT_ID, AIFY_AGENT_ROLE, IS_MANAGED_DISPATCH, cleanEnvPlaceholder } from "./launch-identity.mjs";
import { residentIdentityWarning } from "./register-identity.js";
import { randomUUID } from "crypto";
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { removeAgentBindingFile, writeAgentBindingFile } from "./binding-file.js";
import {
  readCapturedClaudeSessionIdForPid,
  readClaudeSessionId,
  writeClaudeSessionId,
} from "./claude-session-store.js";
import { supportedExecutionModes, wrapperChildExecutionModes } from "./dispatch-execution.js";
import { advertisedEnvironmentRuntimes, advertisedTerminalRuntimes } from "./environment-runtimes.js";
import { listRuntimeMarkers, readRuntimeMarker, writeRuntimeMarker, removeRuntimeMarker, selectClaudeChannelMarkerForParent } from "./runtime-markers.js";
import {
  canLaunchRuntime,
  codexAppServerReachable,
  defaultCapabilitiesForRuntime,
  defaultSessionHandleForRuntime,
  defaultMachineId,
  detectRuntime,
  discoverCodexLiveBinding,
  discoverCodexLiveThreadId,
  hasCodexLiveAppServer,
  launchRuntimeRun,
  normalizeRuntime,
  extractRuntimeSessionHandleFromCommand,
  runtimeStateWithoutSessionHandle,
  terminateProcessTree,
} from "./runtimes.js";

import { shutdownAllPiSessions, getPiSession, acquirePiSession } from "./pi-session.js";
import { shutdownAllCodexSessions } from "./codex-session.js";
import { shutdownAllHermesSessions } from "./hermes-session.js";
import { shutdownAllHermesGatewaySessions } from "./hermes-managed-gateway-session.js";
import { createVirtualTerminalInputManager } from "./virtual-terminal-input.js";
import { TerminalProcessManager, bridgeTerminalSupported } from "./terminal-runtime.js";
import { terminalControlFailurePatch, orphanPidToKill, orphanPidReapAllowed } from "./terminal-control.js";
import { reportDeadOwnedSessions } from "./dead-pty-reporter.js";
import { terminalChildEnv } from "./terminal-env.js";
import { managedViaWrapperRuntimesFromSettingsResponse } from "./managed-wrapper-settings.js";
import { claimFailureDecision, claimRecoveryDecision } from "./claim-failure-policy.js";
import {
  bootstrapManagedEnvironmentBridge,
  localAgentNeedsDispatchHosting,
  managedAgentNeedsDispatchHosting,
  reconcileManagedStateWithSnapshot,
  resolveFreshManagedTeardownTargets,
} from "./managed-teardown-ownership.js";
import { fillSessionHandleFromAdapter } from "./register-helpers.js";
import { startSessionHandleHeartbeat, makeDefaultHandlePoster } from "./session-handle-heartbeat.js";
import { startTurnBusyHeartbeat, makeDefaultTurnBusyPoster } from "./turn-busy-heartbeat.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { startGatewayLivenessProbe } from "./hermes-gateway-liveness.js";
import {
  runManagedTeardown,
  reapOrphanedManagedSurvivors,
  bridgeOwnerIsLive,
  enumerateManagedSurvivors,
  defaultListProcesses as listManagedProcesses,
  defaultReadMarkers as readManagedMarkers,
  defaultKillTree as killManagedTree,
  sweepTombstonedMarkers,
  stopControlTriadAgentId,
} from "./reap-managed-survivors.js";
import { defaultKillByPort, stopDaemon, defaultGetCmdline as hermesGetCmdline, looksLikeHermesProcess, clearDaemonPid } from "./hermes-daemon.js";
import { clearGatewayMarkers as hermesClearGatewayMarkers, readSessionIdMarker, writeSessionIdMarker } from "./hermes-endpoint.js";
import {
  gatewayIndexUrlFromWs,
  makeGatewayReachabilityProbe,
  openGatewayWsClient,
  reportGatewayDead,
} from "./hermes-gateway.mjs";
import {
  buildSessionActiveListFrame,
  pickSessionStatusById,
  pickSessionStatusForKey,
  pickMostRecentSessionRow,
} from "./hermes-gateway-protocol.js";
import { startHermesGatewayTurnDetector } from "./hermes-gateway-turn-detector.js";
import { pinnedSessionId } from "./hermes-session-id.js";
import { startClaudeTurnEndDetector } from "./claude-turn-end-detector.js";
import { collectOnce as collectUsageOnce, collectConsumptionOnce } from "./usage-collector.js";
import { AIFY_VERSION } from "./version.js";

// Nested-bridge guard: when a runtime adapter launches an RPC child (e.g.
// `omp --mode rpc --resume <session>`), that child inherits the aify
// MCP env and would otherwise spawn its OWN `mcp/stdio/server.js` that
// registers as the same agent and supersedes the resident bridge while
// in-flight work is owned by the parent. The parent sets AIFY_BRIDGE_DISABLED=1
// when spawning the child; this guard exits cleanly before any bridge
// registration or claim polling. The child's MCP transport is unused in
// RPC mode, so exiting here is benign.
if (String(process.env.AIFY_BRIDGE_DISABLED || "").trim() === "1") {
  // No registration. No polling. No file writes. The RPC child does not
  // need an MCP server — it talks to the parent bridge via stdio pipes.
  process.exit(0);
}

const { McpServer } = await import("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = await import("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = await import("zod");

// Load env from settings.local.json (user-level + project-level merge)
loadSettingsEnv();

// ── Configuration ────────────────────────────────────────────────────────────

const IS_ENVIRONMENT_BRIDGE =
  process.argv.includes("--environment-bridge") ||
  ["1", "true", "yes"].includes(String(process.env.AIFY_ENVIRONMENT_BRIDGE || "").toLowerCase());
// Captured ONCE at startup: for an MCP-child bridge this is the controlling
// harness (claude/codex/hermes). Used by the harness-death guard in main() so a
// dead harness can't leave this process orphaned (it would otherwise reparent to
// init/Relay and linger). process.ppid changes on reparenting; this snapshot does not.
const ORIGINAL_PARENT_PID = Number(process.ppid) || 0;
const MACHINE_ID = defaultMachineId();
// Same one source as every handshake (see version.js). This one also reaches the server as
// `bridgeVersion` on registration and the startup banner, so a stale literal here misreported
// the bridge's version to the control plane too, not just to MCP clients.
const BRIDGE_VERSION = AIFY_VERSION;

// Compute a build tag the user can paste from an error message to prove
// which code is actually running. Reads .git/HEAD next to this script so
// it works whether the bridge was started from a clone or a release tarball.
function computeBridgeBuildTag() {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    // Native-copy install (the normal case): install.sh stamps .aify-version at the
    // install root (two levels up, ~/.aify-comms/.aify-version) with the repo SHA at
    // copy time — the native copy has no .git, so without this every installed bridge
    // printed "no-git" and the banner couldn't prove which code runs (2026-06-10).
    const stampPath = path.resolve(here, "..", "..", ".aify-version");
    if (fs.existsSync(stampPath)) {
      const m = fs.readFileSync(stampPath, "utf-8").match(/^short=(\S+)/m)
        || fs.readFileSync(stampPath, "utf-8").match(/^sha=(\S+)/m);
      if (m && m[1] && m[1] !== "unknown") return m[1].slice(0, 12);
    }
    // Repo-checkout fallback: read .git/HEAD two levels up.
    const gitDir = path.resolve(here, "..", "..", ".git");
    const headPath = path.join(gitDir, "HEAD");
    if (!fs.existsSync(headPath)) return "no-git";
    const head = fs.readFileSync(headPath, "utf-8").trim();
    if (head.startsWith("ref:")) {
      const refPath = path.join(gitDir, head.slice(4).trim());
      if (fs.existsSync(refPath)) {
        return fs.readFileSync(refPath, "utf-8").trim().slice(0, 12);
      }
      // packed-refs fallback
      const packed = path.join(gitDir, "packed-refs");
      if (fs.existsSync(packed)) {
        const lines = fs.readFileSync(packed, "utf-8").split(/\r?\n/);
        const refName = head.slice(4).trim();
        for (const line of lines) {
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
const BRIDGE_BUILD_TAG = computeBridgeBuildTag();
// Log to stderr on startup so users can see which code is running.
console.error(`[aify-comms bridge] version=${BRIDGE_VERSION} build=${BRIDGE_BUILD_TAG} instance=${BRIDGE_INSTANCE_ID} pid=${process.pid} cwd=${process.cwd()} script=${fileURLToPath(import.meta.url)}`);

// Write the Codex runtime marker from this long-lived bridge process when
// we detect we are running inside a codex-aify wrapper (which sets the
// AIFY_CODEX_APP_SERVER_URL environment variable before launching Codex).
// This must happen here, not in the wrapper's bash CLI call, because on
// Git Bash for Windows `$$` is an MSYS shell PID that is not visible to
// process.kill and isProcessAlive() would auto-delete the marker on first
// read. node's process.pid is always a real Windows PID.
const AIFY_CODEX_APP_SERVER_URL = String(process.env.AIFY_CODEX_APP_SERVER_URL || "").trim();
const AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV = String(process.env.AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV || "").trim();
let codexMarkerCwd = "";
if (AIFY_CODEX_APP_SERVER_URL) {
  codexMarkerCwd = DEFAULT_CWD;
  try {
    const markerData = { appServerUrl: AIFY_CODEX_APP_SERVER_URL };
    if (AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV) markerData.remoteAuthTokenEnv = AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV;
    writeRuntimeMarker("codex", codexMarkerCwd, markerData);
  } catch (error) {
    console.error("[aify] failed to write codex runtime marker:", error?.message || String(error));
    codexMarkerCwd = "";
  }
}

// Write the Hermes runtime marker from this long-lived bridge process when
// we detect we are running inside a hermes-aify wrapper (which sets the
// AIFY_HERMES_GATEWAY_URL environment variable before launching `hermes
// chat --tui`). Mirror of the codex marker block above — same long-lived-
// PID rationale: the wrapper's bash PID isn't a real Windows PID under
// Git Bash, so the marker MUST be written from this Node process.


const __HEARTBEAT_MS = Number(process.env.AIFY_SESSION_HEARTBEAT_MS || "60000") || 60000;
// STARTED ONLY IN REMOTE MODE. This poster is handed a base URL and fetches it directly, without going
// through `httpCall` — so before v0.5.4 it beat against `http://127.0.0.1:8800` forever in local mode,
// because the URL it was given carried that default and could never be empty. The `!__serverUrl` guards
// elsewhere were written to prevent exactly this and could not fire. Gating the START is what actually
// stops it: nothing runs, nothing ticks, and nothing throws. The no-op stopper keeps `cleanupOnExit`
// calling the same slots in the same order.
const __stopHandleHeartbeat = IS_REMOTE
  ? startSessionHandleHeartbeat({
      adapter: __runtimeAdapter,
      agentId: AIFY_AGENT_ID,
      intervalMs: __HEARTBEAT_MS,
      postFn: makeDefaultHandlePoster(SERVER_URL, API_KEY),
    })
  : () => {};

// Plan 4 Task 13 (2026-05-25): turn-busy heartbeat. While any controller's
// start() promise is unresolved, POSTs turn_busy=1 every 30s to keep
// server-side status fresh independent of pre_llm_call / PostToolUse hook
// firing. Solves the operator-observed "working flapping to online during
// long turns" issue. No-op when AIFY_AGENT_ID is unset (managed dispatch
// bridges without an owning agent).
//
// The tracking moved to `controller-activity.mjs` in v0.5.4, which owns the promise
// set and keeps it private behind `__markControllerStart` / `anyControllerActive`.
// This comment named the collection directly until then.
// Turn-busy heartbeat re-pulse — NATIVE RUNTIMES ONLY (codex/pi/hermes).
//
// pure-event-status change #4 (2026-06-02): the claude transcript-growth signal
// was REMOVED from this heartbeat's isActive. It used to re-pulse turn_busy while
// claude's transcript was growing, to hold 'working' through a long GENERATION
// phase past the old short status window. With STATUS now PURE-EVENT (change #3),
// turn_busy is set ONCE at turn START and stays set until the turn-END event — the
// long ceiling holds it through a long generation with NO re-pulse needed, so the
// transcript signal is no longer used to re-arm turn_busy. Instead the transcript
// is read by the #1 turn-END DETECTOR (startClaudeTurnEndDetector below): its TAIL
// STRUCTURE (last assistant message yielded to the user vs awaiting a tool) is
// read as a turn-end, never as a turn_busy re-arm. This removal is the
// anti-feedback-loop guarantee for claude: nothing on the claude path re-asserts
// turn_busy from a derived/observed condition.
//
// isActive now keys ONLY on an in-flight native controller (codex/pi/hermes),
// which is process truth (the controller's start() promise is unresolved) — not
// derived status. claude has no such controller, so claude never triggers this
// re-pulse; its liveness is carried by the unconditional liveness heartbeat
// below, and its 'working' status by the pure-event turn_busy.
// Started only in remote mode, same reasoning as the session-handle heartbeat above: its poster takes a
// base URL and fetches it directly rather than through `httpCall`.
const __stopTurnBusyHeartbeat = !IS_REMOTE ? () => {} : startTurnBusyHeartbeat({
  agentId: AIFY_AGENT_ID,
  intervalMs: 30_000,
  // Active ONLY when a native runtime controller is mid-turn (codex/pi/hermes).
  isActive: () => anyControllerActive(),
  // Pass BRIDGE_INSTANCE_ID so the keep-alive also refreshes this bridge's
  // bridge_instances.last_seen — without it a controller turn longer than the
  // server's active-run bridge-stale window is reaped as a dead bridge
  // mid-turn even though the turn is alive.
  postFn: makeDefaultTurnBusyPoster(SERVER_URL, API_KEY, BRIDGE_INSTANCE_ID),
});

// A3 (status-liveness): unconditional liveness beat. Unlike the turn-busy
// heartbeat above (gated on isActive), this fires for as long as the bridge
// process lives so an idle-but-alive resident worker keeps its
// bridge_instances.last_seen fresh and is not reaped as dead. Liveness-only
// (no turnBusy field); the server ignores beats from a superseded bridge.
const __stopLivenessHeartbeat = startLivenessHeartbeat({
  intervalMs: 30_000,
  beat: async () => {
    if (!AIFY_AGENT_ID || !IS_REMOTE) return;
    await httpCall("POST", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/heartbeat`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      bridgeKind: "resident",
      liveness: true,
    });
  },
});

// Boot-time arm: the normal path (wrapper exported AIFY_AGENT_ID). When it did NOT,
// this no-ops and the register handler arms us late — see armClaudeTurnEndDetector.
armClaudeTurnEndDetector(AIFY_AGENT_ID);

// CODEX turn-STATE via the rollout-tail detector (WS-4b, 2026-06-17). Resident codex
// has ONLY the UserPromptSubmit/Stop hooks for turn state — inert on old CLIs, lost on
// a dropped Stop — so a real turn could read `online` or latch `working` to the 30-min
// ceiling (unlike claude's transcript detector / hermes's gateway detector). The codex
// adapter's transcriptTail reads the active rollout's tail (process truth) and yields
// the same structural summary the generic detector consumes, driving /turn-start /
// /turn-end. Armed for codex regardless of mode (idempotent with managed's .finally
// clear); transcriptTail returns null when no rollout is found, so the detector no-ops.
let __stopCodexTurnDetector = () => {};
if (
  AIFY_AGENT_ID &&
  __runtimeAdapter &&
  __runtimeAdapter.name === "codex" &&
  typeof __runtimeAdapter.transcriptTail === "function"
) {
  __stopCodexTurnDetector = startClaudeTurnEndDetector({
    // PURE-EVENT (2026-06-19): 30s→5s, same rationale as the claude detector above — fast
    // re-assert after a premature/dropped Stop now that the server grace is gone.
    intervalMs: 5_000,
    workingRefreshMs: 45_000,
    readTranscript: async () => __runtimeAdapter.transcriptTail({ agentId: AIFY_AGENT_ID }),
    postTurnStart: async () => {
      if (!AIFY_AGENT_ID || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/turn-start`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "codex",
        source: "bridge-codex-rollout-detector",
      });
    },
    postTurnEnd: async () => {
      if (!AIFY_AGENT_ID || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/turn-end`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "codex",
        source: "bridge-codex-rollout-detector",
      });
    },
  });
}

// RESIDENT-HERMES turn-END via the gateway detector (status-accuracy Task 1,
// 2026-06-07). The managed delivery loop runs startHermesGatewayTurnDetector
// against the gateway's session.active_list status and posts /turn-start /
// /turn-end — but a RESIDENT hermes ran NO such detector, so its turn never
// ended: up to 30 min of false `working` after every turn (the worst single
// status inaccuracy; pre_llm_call set turn-start, nothing cleared it, it
// self-healed only at the 1800s backstop). These two helpers wire the SAME
// detector into the resident bridge path.
//
// shouldArmResidentHermesTurnDetector — the arm gate. Only a hermes runtime with
// a non-empty ws://|wss:// gatewayUrl arms the detector (resident OR
// managed-resident); a non-hermes runtime or a missing/placeholder gateway is a
// hard no-op so it never opens a WS or posts a turn signal. Anti-feedback by
// construction: arming the detector can only ever SET working on a gateway
// "working" read and CLEAR on sustained idle — it never fabricates working.
export function shouldArmResidentHermesTurnDetector({ runtime, sessionMode, gatewayUrl } = {}) {
  void sessionMode; // accepted for symmetry with the managed path; gating is runtime+gateway only.
  if (String(runtime || "").trim() !== "hermes") return false;
  return /^wss?:\/\//i.test(String(gatewayUrl || "").trim());
}

// makeResidentGatewayStatusReader — the resident mirror of
// readManagedSessionStatus (hermes-managed-host.js): open the gateway WS,
// session.active_list, and resolve THIS agent's session status by its real id
// (pickSessionStatusById) → the legacy synthetic-key title match
// (pickSessionStatusForKey) → the most-recent row fallback (the gateway is
// PER-AGENT, so active_list holds only this agent's session). Best-effort:
// any WS / RPC error reads as "" — treated by the detector as not-idle, so a
// transient gateway hiccup NEVER ends a turn early (the 1800s server backstop
// still applies). The WS client is opened LAZILY and REUSED across reads;
// it is re-opened on the next read after a close/error so a dropped socket
// self-heals. ANTI-FEEDBACK-LOOP: this returns the gateway's OWN session
// ["running"] truth, never the aify server's derived status.
export function makeResidentGatewayStatusReader({
  agentId,
  gatewayUrl,
  openWs = openGatewayWsClient,
  readSessionId = (id) => {
    try { return String(readSessionIdMarker(id) || "").trim(); } catch { return ""; }
  },
} = {}) {
  const sessionKey = pinnedSessionId(agentId);
  let wsClient = null;
  let rpcId = 800000;
  // BACKOFF (2026-06-07): once the gateway is sustained-dead (resident-lost / host gone),
  // stop re-connecting every poll tick — each failed open eats a connect-timeout. After
  // FAIL_THRESHOLD consecutive failures, actually probe only 1 in BACKOFF_EVERY reads
  // (~10x fewer connects); the other reads return "" cheaply (a detector no-op). Any
  // successful request resets it instantly, so a recovered gateway resumes within one cycle.
  const FAIL_THRESHOLD = 3;
  const BACKOFF_EVERY = 10;
  let consecutiveFailures = 0;
  let skipCounter = 0;
  const wsOpen = (client) => {
    const rs = client?._socket?.readyState;
    return rs === undefined || rs === 1; /* OPEN (or a fake test client w/o a socket) */
  };
  return async () => {
    if (consecutiveFailures >= FAIL_THRESHOLD) {
      skipCounter = (skipCounter + 1) % BACKOFF_EVERY;
      if (skipCounter !== 0) return ""; // backed off — skip the connect attempt this tick
    }
    try {
      if (!wsClient || !wsOpen(wsClient)) {
        try { wsClient?.close?.(); } catch { /* ignore */ }
        wsClient = await openWs(gatewayUrl);
      }
      const listResp = await wsClient.request(
        buildSessionActiveListFrame({ id: rpcId++, currentSessionId: "" }),
      );
      consecutiveFailures = 0; // the gateway responded → clear any backoff
      const realId = readSessionId(agentId);
      if (realId) {
        const byId = pickSessionStatusById(listResp, realId);
        if (byId) return byId;
      }
      const byKey = pickSessionStatusForKey(listResp, sessionKey);
      if (byKey) return byKey;
      const recentRow = pickMostRecentSessionRow(listResp);
      return String(recentRow?.status || "").trim();
    } catch {
      // Gateway hiccup → drop the (possibly dead) client and read as "" so the
      // detector treats it as a transient no-op (never a false turn-end).
      try { wsClient?.close?.(); } catch { /* ignore */ }
      wsClient = null;
      consecutiveFailures += 1;
      return "";
    }
  };
}

// PROACTIVE gateway-liveness probe for RESIDENT hermes (status-liveness,
// 2026-06-02). A hermes agent shows `available`/`online` whenever its gatewayUrl
// is PRESENT (runtimes.js `gatewayOk = !!gatewayUrl`; server
// `_has_hermes_gateway_url`) — a PRESENCE check, not a LIVENESS check. So if the
// resident gateway HOST (`hermes dashboard --tui` serving the WS this bridge's
// gatewayUrl points at) DIES while this MCP bridge keeps heartbeating (the A3
// beat proves the BRIDGE is alive, not the gateway), the agent stays `available`
// for the whole heartbeat lease. This long-lived bridge runs for the resident
// session's entire lifetime and KNOWS the gatewayUrl, so it's the right place to
// probe gateway reachability and self-correct off `available` → `stale` via the
// existing resident-lost path. Debounced (3 consecutive failures) so a single
// slow/transient probe never flaps a healthy agent. Only armed when this IS a
// gateway-backed hermes (AIFY_HERMES_GATEWAY_URL present). Mirrors the managed
// path's probe in hermes-managed-host.js (deliberately NO bridgeId on
// reportGatewayDead — see that helper).
let __stopGatewayProbe = () => {};
if (AIFY_HERMES_GATEWAY_URL && AIFY_AGENT_ID) {
  __stopGatewayProbe = startGatewayLivenessProbe({
    intervalMs: 30_000,
    threshold: Math.max(1, Number(process.env.AIFY_HERMES_GATEWAY_PROBE_THRESHOLD || 3)),
    probe: makeGatewayReachabilityProbe({ indexUrl: gatewayIndexUrlFromWs(AIFY_HERMES_GATEWAY_URL) }),
    reportDead: async ({ consecutiveFailures } = {}) => {
      if (!IS_REMOTE) return;
      await reportGatewayDead({
        httpCall,
        agentId: AIFY_AGENT_ID,
        gatewayUrl: AIFY_HERMES_GATEWAY_URL,
        reason:
          `Resident hermes gateway unreachable at ${AIFY_HERMES_GATEWAY_URL} after ` +
          `${consecutiveFailures} consecutive liveness probes; the gateway host likely died. ` +
          `Self-correcting off 'available' (resident-lost).`,
      }).catch(() => {});
    },
  });
}

// RESIDENT-HERMES turn-state detector (status-accuracy Task 1, 2026-06-07).
// Armed alongside the gateway-liveness probe above — same precondition
// (gateway-backed hermes) — so a RESIDENT hermes ends its turn on sustained
// gateway idle instead of latching `working` until the 1800s backstop. Reads
// the gateway's OWN session status (session.active_list → the agent's session)
// and posts /turn-start on the gateway "working" edge / /turn-end on sustained
// idle, re-stamping turn-busy every 45s while working (< the server's 120s stale
// window) so a long autonomous turn never goes stale → `online`. ANTI-FEEDBACK:
// gateway-truth-driven, never the server's derived status; only SETs on a
// gateway working read, only CLEARs on sustained idle. Worst case (gateway read
// fails): the reader returns "" → a transient no-op → today's 1800s backstop
// still applies. Gated by shouldArmResidentHermesTurnDetector so a non-hermes /
// no-gateway resident is a no-op.
const __RESIDENT_GATEWAY_TURN_POLL_MS = Math.max(
  250,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_POLL_MS || 3000),
);
const __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE || 3),
);
let __stopResidentHermesTurnDetector = () => {};
if (
  AIFY_AGENT_ID &&
  shouldArmResidentHermesTurnDetector({
    runtime: String(process.env.AIFY_RUNTIME || "").trim(),
    sessionMode: cleanEnvPlaceholder(process.env.AIFY_SESSION_MODE || ""),
    gatewayUrl: AIFY_HERMES_GATEWAY_URL,
  })
) {
  const readResidentGatewayStatus = makeResidentGatewayStatusReader({
    agentId: AIFY_AGENT_ID,
    gatewayUrl: AIFY_HERMES_GATEWAY_URL,
  });
  __stopResidentHermesTurnDetector = startHermesGatewayTurnDetector({
    intervalMs: __RESIDENT_GATEWAY_TURN_POLL_MS,
    idleDebounce: __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE,
    workingRefreshMs: 45000,
    readGatewayStatus: readResidentGatewayStatus,
    // SET working on a gateway-running turn (edge-triggered; re-stamped every 45s
    // while working). busy:true via /turn-start; no runId (an autonomous /
    // direct-typed resident turn has no aify run).
    postTurnStart: async () => {
      if (!AIFY_AGENT_ID || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/turn-start`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "hermes",
        source: "bridge-resident-gateway-detector",
      });
    },
    // CLEAR on sustained idle — authoritative /turn-end, only ever clears.
    postTurnEnd: async () => {
      if (!AIFY_AGENT_ID || !IS_REMOTE) return;
      await httpCall("POST", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/turn-end`, {
        bridgeId: BRIDGE_INSTANCE_ID,
        turnRuntime: "hermes",
        source: "bridge-resident-gateway-detector",
      });
    },
  });
}

// Startup diagnostic: surface the env vars the bridge sees so operators
// can verify env propagation through *-aify → runtime → MCP child.
// Now adapter-driven (Plan 1 of the RuntimeAdapter refactor): the runtime
// adapter knows which env vars to report for its runtime.
try {
  const _runtime = String(process.env.AIFY_RUNTIME || "").trim();
  const _agentId = AIFY_AGENT_ID;
  const _sessionMode = cleanEnvPlaceholder(process.env.AIFY_SESSION_MODE || "");
  const _wrapperFlag = cleanEnvPlaceholder(process.env.AIFY_MANAGED_VIA_WRAPPER || "");
  let _diag = "(no adapter)";
  let _handle = "(none)";
  if (__runtimeAdapter) {
    try {
      _handle = __runtimeAdapter.getCurrentSessionId() || "(none)";
      _diag = JSON.stringify(__runtimeAdapter.diagnosticEnv());
    } catch (err) {
      _diag = `(adapter read failed: ${err?.message || err})`;
    }
  }
  console.error(`[aify] bridge startup: runtime=${_runtime || "(unset)"} agentId=${_agentId || "(unset)"} sessionMode=${_sessionMode || "(unset)"} wrapperChild=${_wrapperFlag || "0"} sessionId=${_handle} env=${_diag}`);
} catch { /* best effort */ }
const AIFY_HERMES_GATEWAY_TOKEN_ENV = String(process.env.AIFY_HERMES_GATEWAY_TOKEN_ENV || "AIFY_HERMES_GATEWAY_TOKEN").trim();
let hermesMarkerCwd = "";
if (AIFY_HERMES_GATEWAY_URL) {
  hermesMarkerCwd = DEFAULT_CWD;
  try {
    const markerData = { gatewayUrl: AIFY_HERMES_GATEWAY_URL };
    if (AIFY_HERMES_GATEWAY_TOKEN_ENV) markerData.gatewayTokenEnv = AIFY_HERMES_GATEWAY_TOKEN_ENV;
    writeRuntimeMarker("hermes", hermesMarkerCwd, markerData);
  } catch (error) {
    console.error("[aify] failed to write hermes runtime marker:", error?.message || String(error));
    hermesMarkerCwd = "";
  }
}

let shutdownStarted = false;
let reportEnvironmentOffline = async () => {};
// The synchronous process.on('exit') cleanup may only act on agent ids that the
// graceful path confirmed from a fresh service snapshot. A cached managed entry
// can be stale after managed→resident takeover; using it can kill the resident
// delivery loop and its gateway host while leaving the visible TUI attached to a
// dead websocket. Unexpected exits leave this null and rely on the boot sweep.
let confirmedManagedTeardownAgentIds = null;

async function interruptActiveRuns(reason = "Bridge shutdown") {
  const active = Array.from(ACTIVE_RUNS.values());
  if (!active.length) return;
  await Promise.allSettled(active.map(async (run) => {
    try {
      await run?.controller?.interrupt?.(reason);
    } catch {
      // Best effort. The process is going down.
    }
  }));
}

function cleanupOnExit() {
  for (const run of ACTIVE_RUNS.values()) {
    try { run?.controller?.interrupt?.("Bridge process exiting"); } catch { /* best effort */ }
  }
  if (environmentHeartbeatTimer) {
    clearInterval(environmentHeartbeatTimer);
    environmentHeartbeatTimer = null;
  }
  try { __stopHandleHeartbeat(); } catch { /* best effort */ }
  try { __stopTurnBusyHeartbeat(); } catch { /* best effort */ }
  try { __stopLivenessHeartbeat(); } catch { /* best effort */ }
  try { __stopGatewayProbe(); } catch { /* best effort */ }
  try { __stopResidentHermesTurnDetector(); } catch { /* best effort */ }
  try { stopClaudeTurnEndDetector(); } catch { /* best effort */ }
  try { __stopCodexTurnDetector(); } catch { /* best effort */ }
  if (spawnLoopTimer) {
    clearInterval(spawnLoopTimer);
    spawnLoopTimer = null;
  }
  if (terminalControlTimer) {
    clearInterval(terminalControlTimer);
    terminalControlTimer = null;
  }
  TERMINAL_MANAGER.stopAll("bridge process exiting").catch(() => {});
  // Synchronous best-effort triad reap may only reuse targets freshly confirmed
  // by runManagedTeardownForBridge. An unexpected exit has no safe ownership
  // snapshot, so it reaps nothing and the next boot sweep is the backstop.
  try { runManagedTeardownSync("bridge exit"); } catch { /* best effort */ }
  // Remove codex runtime marker
  if (codexMarkerCwd) {
    try { removeRuntimeMarker("codex", codexMarkerCwd); } catch { /* best effort */ }
  }
  // Remove hermes runtime marker
  if (hermesMarkerCwd) {
    try { removeRuntimeMarker("hermes", hermesMarkerCwd); } catch { /* best effort */ }
  }
  // Remove agent binding temp file
  removeAgentBindingFile({ pid: process.ppid || process.pid, bridgeId: BRIDGE_INSTANCE_ID });
}
async function shutdownWithStatus(code) {
  if (shutdownStarted) process.exit(code);
  shutdownStarted = true;
  await interruptActiveRuns("Bridge shutting down");
  // Clean-exit resident-lost: best-effort, resident-only, idempotent. Bounded
  // to ~1.5s so a hung/unreachable server can never delay exit indefinitely.
  if (!residentLostSent && AIFY_AGENT_ID) {
    residentLostSent = true;
    try {
      await Promise.race([
        reportResidentLost({
          httpCall,
          agentId: AIFY_AGENT_ID,
          bridgeId: BRIDGE_INSTANCE_ID,
          sessionMode: process.env.AIFY_SESSION_MODE,
          lifecycleOwner: process.env.AIFY_RESIDENT_LIFECYCLE_OWNER || (
            // ASYMMETRY(hermes): Hermes spawns the MCP bridge per turn; the
            // gateway-host wrapper owns the persistent resident lifecycle.
            // Infer this for already-open TUIs launched before the explicit
            // owner marker was added, so installing the fix needs no restart.
            normalizeRuntime(process.env.AIFY_RUNTIME || AGENT_RUNTIME) === "hermes"
              && String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim()
              ? "managed-host"
              : "bridge"
          ),
          runtime: process.env.AIFY_RUNTIME || "generic",
        }),
        new Promise((resolve) => setTimeout(resolve, 1500).unref?.()),
      ]);
    } catch { /* best effort */ }
  }
  try { await reportEnvironmentOffline(); } catch { /* best effort */ }
  // R9: await stopAll so each terminal flushes a final stopped/failed POST
  // before we exit. cleanupOnExit() (and the sync process.on('exit') path)
  // still fire stopAll best-effort for the non-graceful case; that second
  // call is a no-op for terminals already stopped here.
  try { await TERMINAL_MANAGER.stopAll("bridge process exiting"); } catch { /* best effort */ }
  // WS2: restart = clean slate. After the in-memory PTYs are stopped, reap every
  // DETACHED managed-hermes triad survivor (gateway host, delivery loop, daemon)
  // this env bridge owns — the processes engineered to outlive the launcher.
  // Scoped strictly to ownedManagedAgentIds(); never a resident/other-env process.
  try { await runManagedTeardownForBridge("graceful shutdown"); } catch { /* best effort */ }
  try { await shutdownAllPiSessions("bridge exiting"); } catch { /* best effort */ }
  try { await shutdownAllCodexSessions("bridge exiting"); } catch { /* best effort */ }
  try { await shutdownAllHermesSessions("bridge exiting"); } catch { /* best effort */ }
  try { await shutdownAllHermesGatewaySessions("bridge exiting"); } catch { /* best effort */ }
  VIRTUAL_TERMINALS_BY_AGENT.clear();
  VIRTUAL_TERMINAL_INPUT.clear();
  cleanupOnExit();
  process.exit(code);
}
process.on("exit", cleanupOnExit);
process.on("SIGINT", () => { shutdownWithStatus(130); });
process.on("SIGTERM", () => { shutdownWithStatus(143); });
const LOCAL_RUNTIME_STATE = new Map();
// agentId → { terminalId, runtime } for the bridge's synthesized RPC
// terminal. Cached so subsequent dispatches reuse the same virtual
// terminal_session row. Covers both managed pi (persistent omp --mode rpc
// child) and managed hermes (per-dispatch `hermes chat -q -Q` with a
// synthesized request/response feed).
const VIRTUAL_TERMINALS_BY_AGENT = new Map();
// Dashboard input buffering for synthesized pi RPC terminals. See
// virtual-terminal-input.js for the buffer-and-dispatch semantics.
const VIRTUAL_TERMINAL_INPUT = createVirtualTerminalInputManager({
  dispatch: (agentId, line) => dispatchVirtualTerminalLine(agentId, line),
  onError: (error, ctx) => {
    console.error(`[aify] virtual-terminal dispatch failed for "${ctx.agentId}" (line=${JSON.stringify(ctx.line?.slice(0, 80) || "")}): ${error?.message || error}`);
  },
});

// Bridge-side runtimes that own a synthesized virtual rpc
// terminal_session. Must stay aligned with the service-side
// VIRTUAL_RPC_COMMANDS_BY_RUNTIME in api_v2.py — when a new runtime
// is added there, add it here too so the bridge's terminal-control
// router routes synth-terminal controls (input/resize/stop) through
// handleVirtualTerminalControl instead of the legacy node-pty path
// (which marks the row stopped because no real PTY exists).
const VIRTUAL_RPC_RUNTIMES = new Set(["pi", "hermes", "codex", "opencode"]);

function findAgentIdForVirtualTerminal(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) return "";
  for (const [agentId, entry] of VIRTUAL_TERMINALS_BY_AGENT.entries()) {
    if (entry?.terminalId === id && VIRTUAL_RPC_RUNTIMES.has(entry?.runtime)) return agentId;
  }
  return "";
}
const DISPATCH_POLL_MS = Number(process.env.AIFY_DISPATCH_POLL_MS || 3000);
// Terminal-control loop polls separately and much tighter: console input is
// latency-sensitive (operator typing), and the terminal_controls query is
// small + indexed, so a sub-second cadence is perf-safe. Dispatch/spawn
// polling stays at the heavier DISPATCH_POLL_MS.
const TERMINAL_CONTROL_POLL_MS = Math.max(
  200,
  Number(process.env.AIFY_TERMINAL_CONTROL_POLL_MS || 800),
);
let dispatchLoopTimer = null;
let dispatchLoopBusy = false;
let environmentHeartbeatTimer = null;
let environmentBridgeBootstrapped = false;
let environmentBridgeBootstrapPromise = null;
let usageCollectorTimer = null;
let environmentControlTimer = null;
let environmentControlBusy = false;
let spawnLoopTimer = null;
let spawnLoopBusy = false;
let terminalControlTimer = null;
let terminalControlBusy = false;
let managedEnvironmentSyncBusy = false;
let spawnClaimFailureCount = 0;
let spawnClaimLastLogAt = 0;
let remoteEffectiveCwdRoots = null;
const AUTO_REREGISTER_AFTER_FAILURES = 4;
const RESIDENT_BINDING_FAILURES = new Map();
const RESIDENT_BINDING_LOST_AFTER_FAILURES = 2;
// Terminal-activity-driven turn-busy pulses. When a managed PTY produces
// sustained output (claude-aify, pi-aify, etc. working autonomously
// BETWEEN dispatch runs), the backend status engine has no authoritative
// signal that the agent is busy — dispatch_run is completed, no managed
// worker heartbeat. So the agent shows "active" while clearly working,
// which the operator has flagged repeatedly. This emits a debounced
// turn_busy=true while terminal output is fresh, and clears it after a
// short quiet window. Additive to authoritative signals: an active
// dispatch_run still keeps status='working' independently via the
// backend's status engine; this just fills the autonomous-work gap.
const TERMINAL_TURN_BUSY_REMIT_MS = 5000;
const TERMINAL_TURN_BUSY_QUIET_MS = 8000;
const TERMINAL_TURN_BUSY_TIMERS = new Map();
function pulseTerminalTurnBusy(terminalId, agentId) {
  const aid = String(agentId || "").trim();
  if (!aid) return;
  let entry = TERMINAL_TURN_BUSY_TIMERS.get(terminalId);
  if (!entry) {
    entry = { agentId: aid, lastEmit: 0, timer: null };
    TERMINAL_TURN_BUSY_TIMERS.set(terminalId, entry);
  }
  const now = Date.now();
  if (now - entry.lastEmit > TERMINAL_TURN_BUSY_REMIT_MS) {
    entry.lastEmit = now;
    const state = REMOTE_AGENT_STATE.get(aid) || {};
    reportTurnBusy(aid, state, { busy: true }).catch(() => {});
  }
  if (entry.timer) clearTimeout(entry.timer);
  entry.timer = setTimeout(() => {
    const state = REMOTE_AGENT_STATE.get(aid) || {};
    reportTurnBusy(aid, state, { busy: false }).catch(() => {});
    TERMINAL_TURN_BUSY_TIMERS.delete(terminalId);
  }, TERMINAL_TURN_BUSY_QUIET_MS);
}

// Pure gate (exported for tests): given a terminal's runtime + console classification,
// decide which working pulse to emit. Claude uses the spinner-gated console-working
// lease (the strong, specific "claude is generating" signal — the TUI footer). Other
// runtimes keep the legacy any-output terminal pulse (they own native turn detectors).
export function decideConsolePulse({ runtime, consoleClass, agentId, turnInFlight = false }) {
  const aid = String(agentId || "").trim();
  if (!aid) return { kind: "none" };
  if (runtime === "claude-code") {
    // The spinner footer ("working") is the strong, specific generating signal → refresh.
    if (consoleClass === "working") return { kind: "console-working", agentId: aid };
    // Defense-in-depth (#224, 2026-06-18): a transient "unknown" footer frame mid-generation
    // (neither a clear spinner nor the idle prompt) must NOT let the lease lapse WHEN a turn is
    // already known in flight — refresh across the ambiguous frame. NEVER on "idle" (a clear
    // at-rest reading) and never when no turn is known, so this can't manufacture working at rest.
    if (consoleClass === "unknown" && turnInFlight) return { kind: "console-working", agentId: aid };
    return { kind: "none" };
  }
  // Non-claude runtimes (codex/hermes/pi) own native turn detectors (codex turn/completed,
  // hermes gateway idle/running, pi agent_end). The legacy any-output terminal pulse was
  // effectively DEAD before this change (stateFor omitted agentId, so it never fired), so we
  // keep it disabled rather than newly activating an untested output-based `working` for them.
  return { kind: "none" };
}

const CONSOLE_WORKING_REMIT_MS = 2000;
// How recently a console-working pulse must have fired for a subsequent "unknown" footer frame
// to count as mid-turn (and thus refresh the lease). Shorter than the server console-working
// lease so a genuinely ended turn still lets the lease lapse rather than self-extending forever.
const CONSOLE_WORKING_TURN_WINDOW_MS = 15000;
const CONSOLE_WORKING_TIMERS = new Map();

// Refresh the server-side console-working lease while the claude spinner footer is
// visible. Debounced to ~once / CONSOLE_WORKING_REMIT_MS so a per-second spinner redraw
// does not spam the endpoint. No clear timer: the lease self-expires server-side (TTL).
function pulseConsoleWorking(terminalId, agentId, subagents = false) {
  const aid = String(agentId || "").trim();
  if (!aid) return;
  const last = CONSOLE_WORKING_TIMERS.get(terminalId) || 0;
  const now = Date.now();
  if (now - last < CONSOLE_WORKING_REMIT_MS) return;
  CONSOLE_WORKING_TIMERS.set(terminalId, now);
  httpCall("POST", `/agents/${encodeURIComponent(aid)}/console-working`, { subagents: !!subagents }).catch(() => {});
}
async function ensureVirtualTerminal(agentId, agentInfo, runtime) {
  const key = String(agentId || "").trim();
  const rt = String(runtime || "").trim();
  if (!key || !rt) return null;
  const cached = VIRTUAL_TERMINALS_BY_AGENT.get(key);
  if (cached?.terminalId && cached.runtime === rt) return cached;
  const sessionHandle = String(agentInfo?.sessionHandle || agentInfo?.runtimeState?.sessionId || "").trim();
  const workspace = String(agentInfo?.cwd || "").trim();
  const res = await httpCall("POST", `/agents/${encodeURIComponent(key)}/virtual-terminal/ensure`, {
    bridgeId: BRIDGE_INSTANCE_ID,
    sessionHandle,
    workspace,
    runtime: rt,
    requestedBy: "bridge-rpc",
  });
  const terminalId = String(res?.terminal?.id || "").trim();
  if (!terminalId) throw new Error("virtual-terminal/ensure returned no terminal id");
  const entry = { terminalId, runtime: rt };
  VIRTUAL_TERMINALS_BY_AGENT.set(key, entry);
  return entry;
}

async function dispatchVirtualTerminalLine(agentId, lineBody) {
  // Drive the persistent PiSession from operator-typed terminal input. This
  // is intentionally lighter than launchRuntimeRun: there's no dispatch_run
  // row, no agent_status/turn_busy management, no runtime-state PATCH. The
  // synthesized terminal stream is the only operator-visible artifact.
  const state = REMOTE_AGENT_STATE.get(String(agentId || "").trim());
  if (!state) throw new Error(`No bridge state for agent "${agentId}"`);
  const agentInfo = state.info || {};
  const sessionHandle = String(agentInfo?.sessionHandle || agentInfo?.runtimeState?.sessionId || "").trim();
  const session = await acquirePiSession({
    agentId,
    agentInfo,
    sessionId: sessionHandle,
    cwd: agentInfo?.cwd || process.cwd(),
    onPoolEvent: () => {},
  });
  const entry = await ensureVirtualTerminal(agentId, agentInfo, "pi");
  if (entry?.terminalId) session.attachTerminalSink(createVirtualTerminalSink(entry.terminalId));
  const syntheticRun = {
    from: "dashboard",
    subject: "Operator console input",
    body: String(lineBody || ""),
    type: "request",
    executionMode: "managed",
    requireReply: false,
  };
  const turnHandle = session.runTurn(syntheticRun, {
    onEvent: () => {},
    onRuntimeState: () => {},
    onRefs: () => {},
  });
  // Status gap A (2026-06-11, cross-harness audit): an operator-typed pi console turn ran
  // with NO turn tracking (deliberately no dispatch_run row — but that also skipped the
  // turn-busy heartbeat), so the agent read `online` for the whole turn. Registering the
  // turn promise arms the existing 30s turn-busy re-pulse for its duration.
  __markControllerStart(turnHandle.promise);
  return turnHandle.promise;
}

function createVirtualTerminalSink(terminalId) {
  const id = String(terminalId || "").trim();
  if (!id) return null;
  return async (output, status = "") => {
    if (!output && !status) return;
    // Retry transient POST failures up to 3 times so text_delta frames
    // during a long claude/pi turn aren't silently lost when the
    // service is briefly unreachable (e.g., container rebuild blip).
    // Operator-reported (2026-05-22): pi terminal output stopped at
    // "▶ turn started" with only one character of the assistant's
    // reply visible — the subsequent text_delta POSTs fell on the
    // floor during a service-restart window. 404 always means the
    // terminal row is gone — don't retry, invalidate the cache.
    let lastErr = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await httpCall("POST", `/terminals/${encodeURIComponent(id)}/output`, {
          bridgeId: BRIDGE_INSTANCE_ID,
          output: String(output || ""),
          status: String(status || ""),
        });
        return;
      } catch (error) {
        lastErr = error;
        const msg = error?.message || String(error);
        if (/^HTTP 404/.test(msg)) {
          for (const [key, value] of VIRTUAL_TERMINALS_BY_AGENT.entries()) {
            if (value?.terminalId === id) VIRTUAL_TERMINALS_BY_AGENT.delete(key);
          }
          return;
        }
        if (attempt < 2) {
          await new Promise((r) => setTimeout(r, 250 * Math.pow(2, attempt)));
        }
      }
    }
    // After 3 retries: still best-effort, but log so debug ledgers
    // show dropped frames rather than silent loss.
    console.error(
      `[aify] virtual terminal sink dropped frame for ${id} after 3 retries:`,
      lastErr?.message || lastErr,
    );
  };
}

const TERMINAL_MANAGER = new TerminalProcessManager({
  onOutput: async (terminalId, output) => {
    await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/output`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      output,
      status: "attached",
    });
    // Status-precision pulse (mismatch #4): keep status='working' while
    // the agent's terminal is actively producing output even when no
    // dispatch_run is in flight. Self-clears after the quiet window.
    try {
      const st = TERMINAL_MANAGER.stateFor?.(terminalId) || {};
      // A turn is "known in flight" if we emitted a console-working pulse recently (claude showed
      // its spinner within the window) — used to bridge transient "unknown" footer frames mid-turn
      // without ever manufacturing working from a cold/idle console (see decideConsolePulse).
      const lastWorking = CONSOLE_WORKING_TIMERS.get(terminalId) || 0;
      const turnInFlight = lastWorking > 0 && (Date.now() - lastWorking) < CONSOLE_WORKING_TURN_WINDOW_MS;
      const decision = decideConsolePulse({
        runtime: st.runtime,
        consoleClass: st.consoleClass,
        agentId: st.agentId,
        turnInFlight,
      });
      if (decision.kind === "console-working") pulseConsoleWorking(terminalId, decision.agentId, st.subagentsActive);
      else if (decision.kind === "terminal-pulse") pulseTerminalTurnBusy(terminalId, decision.agentId);
    } catch {}
  },
  onExit: async (terminalId, detail = {}) => {
    const error = detail?.error?.message || "";
    await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/output`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      output: error ? `\n[terminal failed] ${error}\n` : `\n[terminal exited]\n`,
      status: error ? "failed" : "stopped",
    });
  },
  onHeal: async (_terminalId, detail = {}) => {
    const agentId = String(detail.agentId || "").trim();
    if (!agentId || !detail.previousSessionHandle) return;
    try {
      await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/session-handle`, {
        sessionHandle: "",
        requestedBy: "terminal-runtime-heal",
      });
    } catch (error) {
      console.error(`[aify] failed to clear stale ${detail.runtime || "runtime"} session handle for "${agentId}":`, error?.message || error);
    }
  },
  // Auto-answer managed-claude TUI prompts (resume/compaction/perms/channel) unless the
  // operator opts out with AIFY_NO_AUTO_ANSWER=1.
  autoAnswer: process.env.AIFY_NO_AUTO_ANSWER !== "1",
  // Repaint keepalive for managed claude PTYs so the console-working lease stays fresh when the
  // Console is closed (2026-06-05). Opt out with AIFY_NO_CONSOLE_KEEPALIVE=1; override cadence
  // with AIFY_CONSOLE_KEEPALIVE_MS.
  consoleKeepaliveMs: process.env.AIFY_NO_CONSOLE_KEEPALIVE === "1"
    ? 0
    : (Number(process.env.AIFY_CONSOLE_KEEPALIVE_MS) || 4000),
});

// ── Local filesystem paths (used only in local mode) ─────────────────────────


// ── Input validation ────────────────────────────────────────────────────────

if (!IS_REMOTE) {
  for (const dir of [MESSAGES_DIR, INBOX_DIR, SHARED_DIR]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

// ── HTTP helper (remote mode) ────────────────────────────────────────────────


// Long-poll for the "claim" endpoints (2026-06-30): instead of short-polling
// "is there work yet?" every few seconds, the bridge asks the server to HOLD the
// request open up to CLAIM_WAIT_MS and return the instant work appears. This cuts
// idle HTTP request volume ~8-10x (and the per-claim BEGIN IMMEDIATE write-lock rate)
// without losing latency. The server caps the hold at longpoll.MAX_WAIT_S (30s), so
// keep CLAIM_WAIT_MS under that; the HTTP timeout must EXCEED the hold or the bridge
// would abort mid-hold and trip its failure counter. The loop busy-guards already
// prevent the setInterval ticks from stacking while a claim is held.
// Set AIFY_CLAIM_WAIT_MS=0 to fall back to legacy short-poll.
const CLAIM_WAIT_MS = Math.max(0, Math.min(28000, Number(process.env.AIFY_CLAIM_WAIT_MS ?? 20000)));
const CLAIM_HTTP_TIMEOUT_MS = CLAIM_WAIT_MS > 0 ? CLAIM_WAIT_MS + 8000 : HTTP_TIMEOUT_MS;
const CLAIM_OPTS = CLAIM_WAIT_MS > 0 ? { timeoutMs: CLAIM_HTTP_TIMEOUT_MS } : {};

// POST is not idempotent in general, so we only retry POSTs that are safe to
// replay. Everything else (GET, PATCH, DELETE) is always retriable.
// This list is intentionally narrow. If you add a new POST endpoint that can
// be retried without creating duplicate side effects, add it here explicitly.

function logTransientOrError(prefix, error) {
  if (isTransientHttpError(error)) {
    const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
    console.error(`${prefix}: transient HTTP error against ${target}: ${error?.message || String(error)}; retrying`);
    return;
  }
  console.error(`${prefix}:`, error);
}

const CONTROL_CLAIM_FAILURES = new Map();

function noteControlClaimFailure(label, error) {
  const previous = CONTROL_CLAIM_FAILURES.get(label) || { count: 0, lastLogAt: 0 };
  const state = { count: previous.count + 1, lastLogAt: previous.lastLogAt };
  const decision = claimFailureDecision(state);
  state.lastLogAt = decision.nextLastLogAt;
  CONTROL_CLAIM_FAILURES.set(label, state);
  const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
  const detail = [...new Set([error?.message, error?.cause?.code, error?.cause?.message].filter(Boolean))].join(": ");
  if (decision.debug && String(process.env.AIFY_DEBUG || "").trim() === "1") {
    console.debug(`[aify] ${label} transient failure against ${target}: ${detail}; retrying`);
  }
  if (decision.warn) {
    console.error(
      `[aify] ${label} unavailable (${state.count} consecutive) against ${target}: ${detail}. ` +
      "Retrying quietly; check that the service is running and reachable from this shell.",
    );
  }
}

function noteControlClaimSuccess(label) {
  const state = CONTROL_CLAIM_FAILURES.get(label);
  if (!state) return;
  if (claimRecoveryDecision(state.count).log) {
    console.error(`[aify] ${label} recovered after ${state.count} failure(s)`);
  }
  CONTROL_CLAIM_FAILURES.delete(label);
}





function dedupePreserveOrder(values) {
  const seen = new Set();
  const result = [];
  for (const value of values || []) {
    if (!value || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}


// Plan 6 A2 (2026-05-26): runtime-authoritative session-handle resolver
// used at the initial register path (mirrors A1's heartbeat reversal).
// The wrapper exports HERMES_SESSION_ID / CODEX_THREAD_ID etc. from
// whatever the operator's parent shell happened to have set — often
// stale values from a prior runtime session. Discover-first asks the
// runtime itself (gateway RPC / app-server probe / filesystem scan) for
// the truth; env-fallback preserves the legacy behavior when the
// runtime can't be probed (no adapter, discover throws, returns null).
// Strictly additive: when discover fails, we get exactly the pre-Plan-6
// behavior. Exported for unit testing.
export async function computeInitialSessionHandle({ adapter, envHandle }) {
  if (adapter && typeof adapter.discoverSessionId === "function") {
    try {
      const discovered = await adapter.discoverSessionId();
      if (discovered) return String(discovered).trim();
    } catch { /* swallow; fall through to env */ }
  }
  return String(envHandle || "").trim();
}

async function autoRegisterConfiguredAgent(_retriesLeft = 8) {
  // Audit 2026-06-28: the environment bridge must NEVER auto-register as an agent. It's always
  // remote and not managed-dispatch, so if it inherits a parent agent's AIFY_AGENT_ID (the known
  // gotcha) it would self-register as a resident agent and clobber that agent's real registration.
  // The launcher scrubs the env, but guard in-code too (belt-and-suspenders, like the line-5832
  // harness-death guard which already excludes the env-bridge).
  if (IS_ENVIRONMENT_BRIDGE) return;
  if (!IS_REMOTE || IS_MANAGED_DISPATCH || !AIFY_AGENT_ID) return;
  try { validateName(AIFY_AGENT_ID, "agent ID"); } catch (error) {
    console.error(`[aify] AIFY_AGENT_ID ignored: ${error.message}`);
    return;
  }
  const runtime = detectRuntime(process.env.AIFY_RUNTIME || "");
  // FIX 2 (2026-06-03): an UNEXPANDED literal like "${AIFY_AGENT_CWD}" (when the
  // wrapper didn't export it) is truthy and would bypass DEFAULT_CWD. Treat any
  // value still containing a ${...} placeholder as empty so DEFAULT_CWD applies.
  const rawAgentCwd = process.env.AIFY_AGENT_CWD || "";
  const agentCwd = /\$\{.*\}/.test(rawAgentCwd) ? "" : rawAgentCwd;
  const cwd = normalizeRegistrationCwd(runtime, agentCwd || DEFAULT_CWD);
  let runtimeConfig = resolvedRuntimeConfigForRegistration(runtime, null, cwd);
  // Same ${...}-placeholder guard as AIFY_AGENT_CWD above (FIX 2): an unexpanded
  // `AIFY_SESSION_HANDLE="${HERMES_SESSION_ID}"` (wrapper/config var unset) must NOT
  // become the registered handle — it poisons the agent→session binding. Strip it so
  // the runtime default / discover path applies instead (2026-06-04).
  const rawSessionHandle = String(process.env.AIFY_SESSION_HANDLE || "");
  const cleanSessionHandle = /\$\{.*\}/.test(rawSessionHandle) ? "" : rawSessionHandle;
  const envHandle = String(cleanSessionHandle || defaultSessionHandleForRuntime(runtime) || "").trim();
  // Plan 6 A2: discover authoritative, env fallback. See computeInitialSessionHandle above.
  const initialHandle = await computeInitialSessionHandle({ adapter: __runtimeAdapter, envHandle });
  let codexLiveBinding = null;
  if (runtime === "codex" && !hasCodexLiveAppServer(runtimeConfig)) {
    codexLiveBinding = await discoverCodexLiveBinding({ sessionHandle: initialHandle, cwd });
    if (codexLiveBinding?.runtimeConfig) runtimeConfig = { ...runtimeConfig, ...codexLiveBinding.runtimeConfig };
  }
  const discoveredCodexThreadId =
    runtime === "codex" && hasCodexLiveAppServer(runtimeConfig)
      ? (codexLiveBinding?.threadId || await discoverCodexLiveThreadId(runtimeConfig, cwd))
      : "";
  const sessionHandle = initialHandle || discoveredCodexThreadId || "";
  // Native-session-id model (2026-06-03): bind agentId -> the REAL hermes
  // session id in the per-agent marker so the wrapper resumes the SAME session
  // next launch and the delivery loop targets it. Best-effort; never throws.
  if (runtime === "hermes" && AIFY_AGENT_ID && sessionHandle) {
    try { writeSessionIdMarker(AIFY_AGENT_ID, sessionHandle); } catch { /* best-effort */ }
  }
  // Wrapper-declared session mode + channel state. The *-aify wrappers set
  // AIFY_SESSION_MODE (resident default for human TTY, managed when
  // aify-comms spawns the wrapper) and AIFY_CHANNELS_ENABLED=1 when they
  // launched the runtime with the aify channel MCP loaded. We trust the
  // wrapper's declaration so the service's resident-cap strip (which
  // requires runtime_config.channelEnabled) gets the truth.
  const resolvedSessionMode = (() => {
    const explicit = String(process.env.AIFY_SESSION_MODE || "").trim().toLowerCase();
    return explicit === "managed" || explicit === "resident" ? explicit : "resident";
  })();
  const channelsEnabled = String(process.env.AIFY_CHANNELS_ENABLED || "").trim() === "1";
  const capabilities = defaultCapabilitiesForRuntime(runtime, resolvedSessionMode, sessionHandle, runtimeConfig);
  const effectiveRuntimeConfig = channelsEnabled
    ? { ...(runtimeConfig || {}), channelEnabled: true }
    : (runtimeConfig || {});
  const payload = {
    agentId: AIFY_AGENT_ID,
    role: AIFY_AGENT_ROLE || "coder",
    name: process.env.AIFY_AGENT_NAME || AIFY_AGENT_ID,
    cwd,
    runtime,
    machineId: MACHINE_ID,
    bridgeId: BRIDGE_INSTANCE_ID,
    launchMode: "detached",
    sessionMode: resolvedSessionMode,
    sessionHandle,
    capabilities,
    runtimeConfig: effectiveRuntimeConfig,
    terminalId: cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || ""),
    managedWrapperChild: String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1",
    restoreDeleted: true,
    autoRegister: true,
    // Tombstone-resurrection guard (2026-06-03): the service only clears an
    // agent tombstone for a GENUINE fresh relaunch — a bridge whose
    // bridgeStartedAt is newer than the tombstone's removed_at. Sending this
    // stops a still-running bridge from resurrecting a deliberately-removed
    // agent on its next passive auto re-register.
    bridgeStartedAt: BRIDGE_STARTED_AT,
    // Phase 4 race guard escape hatch (2026-05-31): when a same-mode resident
    // bridge is still LIVE, the service hard-rejects (409) a different bridge
    // re-registering this identity. Set AIFY_FORCE_REGISTER=1 to deliberately
    // take over after restarting the prior wrapper.
    force: String(process.env.AIFY_FORCE_REGISTER || "").trim() === "1",
  };
  try {
    const r = await httpCall("POST", "/agents", payload);
    let runtimeState = {};
    try {
      const agentInfo = await httpCall("GET", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}`);
      runtimeState = agentInfo.agent?.runtimeState || {};
    } catch {
      // Best effort.
    }
    const pendingTakeover =
      r.ownershipTransition === "pending_resident_takeover" ||
      (
        runtimeState?.pendingResidentTakeover &&
        String(runtimeState.pendingResidentTakeover.bridgeId || "") === BRIDGE_INSTANCE_ID
      );
    if (!pendingTakeover) {
      runtimeState = { ...runtimeState, bridgeInstanceId: BRIDGE_INSTANCE_ID };
      try {
        await httpCall("PATCH", `/agents/${encodeURIComponent(AIFY_AGENT_ID)}/runtime-state`, { runtimeState });
      } catch {
        // Best effort.
      }
    }
    REMOTE_AGENT_STATE.set(AIFY_AGENT_ID, { info: { ...payload, runtimeState } });
    try {
      writeAgentBindingFile({ pid: process.ppid || process.pid, agentId: AIFY_AGENT_ID, bridgeId: BRIDGE_INSTANCE_ID });
    } catch {
      // Best effort; notification hooks can still operate after explicit comms_register.
    }
    ensureDispatchLoop();
    const transition = r.ownershipTransition ? ` (${r.ownershipTransition})` : "";
    console.error(`[aify] auto-registered "${AIFY_AGENT_ID}" as resident ${runtime}${sessionHandle ? ` session ${sessionHandle}` : ""}${transition}`);
  } catch (error) {
    const msg = String(error?.message || error || "");
    // Phase 4 race guard: a LIVE same-mode bridge already owns this identity.
    // RETRY, DON'T GIVE UP (2026-06-13, the sc-manager stale+deaf incident): a quick
    // close-and-relaunch always trips this guard — kill-prior killed the prior session
    // seconds ago, but its heartbeat lease makes it look live for up to ~150s. A
    // one-shot refusal left the session permanently unbound (mute sidecar = no inbound
    // delivery; stale status). The server now allows same-session-handle relaunch
    // takeover, and this retry loop covers older servers + genuine lease-expiry waits:
    // re-attempt every 30s for ~4 minutes. A genuinely-owned identity keeps refusing
    // (correct — operator hint stands); the dead prior simply ages out and a retry wins.
    if (/already has a LIVE/i.test(msg) || /force=true/i.test(msg)) {
      console.error(
        `[aify] auto-register for "${AIFY_AGENT_ID}" was refused — another live wrapper owns this session.\n` +
          `       ${msg}\n` +
          `       Retrying every 30s for ~4 minutes (a just-killed prior wrapper ages out of its lease).` +
          ` To take over immediately, relaunch with AIFY_FORCE_REGISTER=1.`,
      );
      const retriesLeft = Number.isFinite(_retriesLeft) ? _retriesLeft : 8;
      if (retriesLeft > 0) {
        const t = setTimeout(() => {
          autoRegisterConfiguredAgent(retriesLeft - 1).catch(() => {});
        }, 30_000);
        if (typeof t.unref === "function") t.unref();
      } else {
        console.error(`[aify] auto-register retries exhausted for "${AIFY_AGENT_ID}" — run comms_register in this session to bind it.`);
      }
    } else {
      console.error(`[aify] auto-register failed for "${AIFY_AGENT_ID}": ${msg}`);
    }
  }
}


// What did this agent last PRODUCE? Audit finding 1: nothing on the health surface answered that.
//
// During the 2026-08-10 outage `unread: 0`, an inbound `last read`, and an advancing `last seen`
// were all individually true while an agent's reply sat undelivered — and a manager reported the
// lane dead to the operator three times on that evidence. The missing field was never a liveness
// marker; it was "what came OUT of this agent, and when".
//
// Says "unknown" rather than "never" when the field is absent. A pre-fix service does not send it,
// and rendering that as "has never sent anything" would manufacture exactly the confident-but-wrong
// claim this whole finding is about.
//
// AUDIT 4/4 F2. The first cut collapsed the two absences into one line, which reopened a smaller
// version of the very ambiguity above: a CURRENT service reporting a known-empty `outbound: {}` for
// a fresh agent was rendered as "the service did not report outbound activity" — false, and it
// blames the wrong component. `_agent_record_to_dict` always emits the key (`"outbound": outbound
// or {}`), so key PRESENCE is the discriminator and no API change is needed:
//
//     key absent   -> pre-v0.3.1 service; we genuinely cannot answer
//     key present, empty -> the service answered: nothing produced yet

async function ensureRequiredReplyHandoff(agentId, run = {}, terminalStatus = "completed", detailText = "") {
  if (!run?.id || !run?.from) return;
  try {
    const latest = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(run.id)}`);
    const current = latest?.run || {};
    if (!current.requireReply || current.resultMessageId) return;

    // Strict reply mode (managed_reply_capture_fallback=false): do NOT mirror
    // final output as the reply. The agent is expected to answer via
    // comms_send(inReplyTo); leave the run reply-owed and visible so a missing
    // reply is surfaced, not fabricated from working/telemetry text.
    if (!(await readReplyCaptureFallback())) {
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        appendEvent: `Run ended without an explicit comms_send reply; strict reply mode does not auto-mirror. Reply still owed to ${run.from}.`,
        eventType: "handoff",
      });
      return;
    }

    const body = {
      from_agent: agentId,
      to: run.from,
      type: terminalStatus === "failed" ? "error" : "response",
      subject: autoReplySubjectForRun(run, terminalStatus),
      body: autoReplyBodyForRun(run, terminalStatus, detailText),
      priority: run.priority || "normal",
      trigger: false,
      // Deterministic idempotency key (#240): this owed-reply handoff is the highest-value
      // victim of a dropped send — a transient socket error here strands the require_reply
      // run. Keying the nonce on the run id lets httpCall retry the POST safely, and also
      // dedups if the handoff fires more than once for the same run.
      clientNonce: `handoff-${run.id}-${terminalStatus}`,
    };
    const replyParent = current.messageId || current.inReplyTo || "";
    if (replyParent) body.inReplyTo = replyParent;

    const sent = await httpCall("POST", "/messages/send", body);
    await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
      resultMessageId: sent?.messageId || "",
      appendEvent: `Auto-mirrored result to ${run.from} because no explicit reply message was sent during the run.`,
      eventType: "handoff",
    });
  } catch (error) {
    try {
      const latest = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(run.id)}`);
      if (latest?.run?.resultMessageId) return;
      await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
        appendEvent: `Run ended without an explicit reply. Auto-mirror to ${run.from} failed: ${error?.message || error}`,
        eventType: "handoff",
      });
    } catch {
      // best effort
    }
  }
}

// ── Local filesystem helpers ─────────────────────────────────────────────────






// ── Message safety ───────────────────────────────────────────────────────────
// Messages from other agents are UNTRUSTED DATA. Wrap in code fences so
// Claude Code treats them as data, not instructions to follow.



async function reregisterAgentFromState(agentId, state) {
  if (!state?.info) return false;
  const info = state.info;
  const payload = {
    agentId,
    role: info.role || "generic",
    name: info.name || agentId,
    cwd: info.cwd || "",
    model: info.model || "",
    description: info.description || "",
    instructions: info.instructions || "",
    runtime: info.runtime || "generic",
    machineId: info.machineId || MACHINE_ID,
    bridgeId: BRIDGE_INSTANCE_ID,
    launchMode: info.launchMode || "detached",
    sessionMode: info.sessionMode || "resident",
    sessionHandle: info.sessionHandle || "",
    managedBy: info.managedBy || "",
    capabilities: info.capabilities || [],
    runtimeConfig: info.runtimeConfig || {},
    // R8: mirror the initial /agents register so a 404 auto-re-register does
    // not drop the console_terminal_attached binding. AIFY_TERMINAL_ID is
    // stable for the bridge process lifetime; fall back to cached info.
    terminalId: cleanEnvPlaceholder(process.env.AIFY_TERMINAL_ID || info.terminalId || ""),
    managedWrapperChild: String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1" || !!info.managedWrapperChild,
    autoRegister: true,
    // Tombstone-resurrection guard (2026-06-03): see autoRegisterConfiguredAgent.
    // A 404 auto-re-register from a lingering bridge must not resurrect a
    // deliberately-removed agent unless this bridge launched after the deletion.
    bridgeStartedAt: BRIDGE_STARTED_AT,
  };
  try {
    await httpCall("POST", "/agents", payload);
    console.error(`[aify] auto-re-registered "${agentId}" from cached state`);
    return true;
  } catch (error) {
    if (error?.status === 410) {
      forgetRemoteAgent(agentId, "server marked it intentionally removed");
      return false;
    }
    console.error(`[aify] auto-re-register failed for "${agentId}": ${error?.message || error}`);
    return false;
  }
}


async function residentRuntimeBindingLost(agentId, info = {}) {
  const sessionMode = normalizeSessionMode(info.sessionMode);
  const runtime = normalizeRuntime(info.runtime || "generic");
  if (sessionMode !== "resident" || runtime !== "codex") return false;
  const runtimeConfig = info.runtimeConfig || {};
  const appServerUrl = String(runtimeConfig.appServerUrl || "").trim();
  if (!appServerUrl || !info.sessionHandle) {
    RESIDENT_BINDING_FAILURES.delete(agentId);
    return false;
  }
  const remoteAuthTokenEnv = String(runtimeConfig.remoteAuthTokenEnv || "").trim();
  const token = remoteAuthTokenEnv ? String(process.env[remoteAuthTokenEnv] || "").trim() : "";
  const reachable = await codexAppServerReachable(appServerUrl, { token, timeoutMs: 1200 });
  if (reachable) {
    RESIDENT_BINDING_FAILURES.delete(agentId);
    return false;
  }
  const failures = (RESIDENT_BINDING_FAILURES.get(agentId) || 0) + 1;
  RESIDENT_BINDING_FAILURES.set(agentId, failures);
  console.error(`[aify] resident Codex app-server for "${agentId}" is unreachable (${failures}/${RESIDENT_BINDING_LOST_AFTER_FAILURES}): ${appServerUrl}`);
  return failures >= RESIDENT_BINDING_LOST_AFTER_FAILURES;
}

async function reportResidentRuntimeLost(agentId, info = {}, reason = "resident runtime app-server is unreachable") {
  try {
    const result = await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/resident-lost`, {
      bridgeId: BRIDGE_INSTANCE_ID,
      machineId: info.machineId || MACHINE_ID,
      runtime: normalizeRuntime(info.runtime || "generic"),
      reason,
    });
    const transition = result?.transition ? ` (${result.transition})` : "";
    console.error(`[aify] resident runtime lost for "${agentId}"${transition}: ${reason}`);
  } catch (error) {
    console.error(`[aify] failed to report resident runtime loss for "${agentId}": ${error?.message || error}`);
  } finally {
    forgetRemoteAgent(agentId, reason);
    if (!IS_ENVIRONMENT_BRIDGE && REMOTE_AGENT_STATE.size === 0) {
      setTimeout(() => { shutdownWithStatus(0); }, 50).unref();
    }
  }
}

// Clean-exit resident-lost signal. When an operator cleanly closes a RESIDENT
// *-aify session (Ctrl-D / window close / SIGTERM), nothing else POSTs a
// "resident is leaving" signal, so the agent keeps showing `available` for the
// full ~150s heartbeat lease until bridge_instances.last_seen ages out. This
// long-lived bridge IS the resident MCP bridge that owns
// runtime_state.bridgeInstanceId, so it can self-correct on the way out by
// POSTing the SAME /agents/{id}/resident-lost signal the reactive paths use
// (reportResidentRuntimeLost above). It carries bridgeId — unlike the
// managed-host's bridgeId-less variant — because this bridge id matches the
// owning runtime_state and passes the server's bridge_not_current guard.
//
// STRICTLY gated to RESIDENT sessions only: managed teardown is handled by
// terminal reaping, and a managed bridge must never flip its own agent off
// `available`. Pure + dependency-injected so it's unit-testable: it does NOT
// POST unless (resident AND an agent id is bound). Best-effort: never throws.
export async function reportResidentLost({
  httpCall: call,
  agentId,
  bridgeId,
  sessionMode,
  lifecycleOwner = "bridge",
  machineId = MACHINE_ID,
  runtime = "generic",
  reason = "Resident *-aify session closed cleanly; self-correcting off 'available' (resident-lost).",
} = {}) {
  const id = String(agentId || "").trim();
  // Resident gate: managed sessions must NOT POST resident-lost.
  if (normalizeSessionMode(sessionMode) !== "resident") return false;
  // Some harnesses spawn this MCP bridge as a short-lived per-turn child. Their
  // wrapper/sidecar owns the actual resident TUI lifecycle and reports the real
  // close; a child exit is not evidence that the operator's TUI disappeared.
  if (String(lifecycleOwner || "bridge").trim().toLowerCase() !== "bridge") return false;
  if (!call || !id) return false;
  try {
    await call("POST", `/agents/${encodeURIComponent(id)}/resident-lost`, {
      bridgeId,
      machineId,
      runtime: normalizeRuntime(runtime || "generic"),
      reason,
    });
    return true;
  } catch (error) {
    console.error(
      `[aify] clean-exit resident-lost for "${id}" failed (best-effort): ${error?.message || String(error)}`,
    );
    return false;
  }
}

// Idempotency guard so the clean-exit resident-lost POST can't double-fire
// across the SIGTERM→shutdownWithStatus and process.on('exit') handlers.
let residentLostSent = false;

let residentStopInProgress = false;
function terminateResidentHost(reason = "Resident session stopped from dashboard") {
  if (residentStopInProgress) return;
  residentStopInProgress = true;
  console.error(`[aify] ${reason}; terminating resident host process`);
  setTimeout(() => {
    try {
      const parentPid = Number(process.ppid);
      if (Number.isInteger(parentPid) && parentPid > 1) {
        terminateProcessTree({ pid: parentPid, kill: (signal) => process.kill(parentPid, signal) });
      }
    } catch {
      // Best effort. If the parent cannot be killed, still stop this MCP process.
    }
    process.exit(0);
  }, 25).unref();
}

function environmentKind() {
  const explicit = String(process.env.AIFY_ENVIRONMENT_KIND || "").trim();
  if (explicit) return explicit;
  if (process.env.WSL_DISTRO_NAME) return "wsl";
  if (process.env.container || fs.existsSync("/.dockerenv")) return "docker";
  if (process.platform === "win32") return "windows";
  if (process.platform === "darwin") return "macos";
  return "linux";
}

function environmentOs() {
  if (process.platform === "win32") return "windows";
  if (process.platform === "darwin") return "macos";
  return "linux";
}

function environmentLabel(kind, hostname) {
  const explicit = String(process.env.AIFY_ENVIRONMENT_LABEL || "").trim();
  if (explicit) return explicit;
  if (kind === "wsl") return `WSL ${process.env.WSL_DISTRO_NAME || ""} on ${hostname}`.replace(/\s+/g, " ").trim();
  if (kind === "docker") return `Docker on ${hostname}`;
  if (kind === "windows") return `Windows on ${hostname}`;
  if (kind === "macos") return `macOS on ${hostname}`;
  return `Linux on ${hostname}`;
}

function cwdRootsForEnvironment() {
  const explicit = String(process.env.AIFY_CWD_ROOTS || "").trim();
  if (explicit) {
    return dedupePreserveOrder(explicit.split(path.delimiter).map((item) => item.trim()).filter(Boolean));
  }
  return dedupePreserveOrder([DEFAULT_CWD]);
}

// Tear down every managed-hermes triad survivor (gateway host, delivery loop,
// daemon, console PTY) this env bridge owns. Targets come from a FRESH service
// ownership read — NEVER the long-lived REMOTE_AGENT_STATE cache, because a
// managed→resident switch can make that cache stale until the next heartbeat.
// async: awaits the port-kill/stopDaemon promises so the kills land before
// process.exit. If the service is unavailable, fail safe and reap nothing; the
// next boot sweep handles genuine managed survivors after ownership is readable.
async function runManagedTeardownForBridge(reason = "bridge teardown") {
  if (!IS_ENVIRONMENT_BRIDGE) return;
  const resolved = await resolveFreshManagedTeardownTargets({
    selfBridgeId: BRIDGE_INSTANCE_ID,
    fetchOwnership: fetchManagedOwnershipForEnv,
    // What we PROVED we owned earlier in this process's life. Used only when the live read
    // fails, which on a full shutdown is the normal case because the service goes down first.
    lastKnownOwnedAgentIds: confirmedManagedTeardownAgentIds,
  });
  const ownedAgentIds = resolved.agentIds;
  // Only remember ownership we actually verified — never overwrite a proven list with a
  // degraded fallback, or one failed read would erode the evidence the next one relies on.
  if (resolved.source === "fresh-ownership") confirmedManagedTeardownAgentIds = ownedAgentIds;
  if (resolved.degraded) {
    console.error(
      `[aify] managed teardown (${reason}): live ownership unavailable (${resolved.error?.message || resolved.error}) — `
      + `falling back to ${ownedAgentIds.length} agent(s) this bridge previously proved it owned: ${ownedAgentIds.join(", ")}`,
    );
  }
  if (resolved.skipped === "ownership-unavailable") {
    console.error(
      `[aify] managed teardown (${reason}): fresh ownership unavailable — reaping nothing (fail-safe):`,
      resolved.error?.message || resolved.error,
    );
    return;
  }
  if (!ownedAgentIds.length) return;
  try {
    const result = runManagedTeardown({
      ownedAgentIds,
      cwdRoots: cwdRootsForEnvironment(),
      listProcesses: listManagedProcesses,
      readMarkers: () => readManagedMarkers(os.tmpdir()),
      // Owned console PTYs are already killed by TERMINAL_MANAGER.stopAll on the
      // graceful path; the detached triad (gateway/loop/daemon) is the survivor
      // concern here, enumerated from markers + the process scan.
      consolePtyPids: [],
      killByPort: defaultKillByPort,
      stopDaemon,
      killTree: killManagedTree,
    });
    if (Array.isArray(result?.pending) && result.pending.length) {
      await Promise.allSettled(result.pending);
    }
    const n =
      (result?.killed?.gatewayHosts?.length || 0) +
      (result?.killed?.deliveryLoops?.length || 0) +
      (result?.killed?.daemons?.length || 0) +
      (result?.killed?.consolePtys?.length || 0);
    if (n) {
      console.error(`[aify] managed teardown (${reason}): reaped ${n} survivor(s) for agents ${ownedAgentIds.join(", ")}`);
    }
    if (result?.errors?.length) {
      console.error(`[aify] managed teardown (${reason}) had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
  } catch (error) {
    console.error(`[aify] managed teardown (${reason}) failed:`, error?.message || error);
  }
}

// Tear down ONE managed-hermes agent's triad (gateway host, delivery loop,
// daemon, console PTY) — the agent-scoped reaper for a Dashboard STOP/REMOVE of
// a managed hermes agent (fix/hermes-leak P2). Scoped strictly to the single
// agentId passed in: enumeration keys on the delivery-loop cmdline + the agent's
// own port/daemon-pid markers, so another agent's or a resident operator's
// processes can NEVER be enumerated. async: awaits the port-kill/stopDaemon
// promises. Best-effort; never throws.
async function runSingleAgentManagedTeardown(agentId, reason = "agent stop") {
  const id = String(agentId || "").trim();
  if (!id) return;
  try {
    const result = runManagedTeardown({
      ownedAgentIds: [id],
      cwdRoots: cwdRootsForEnvironment(),
      listProcesses: listManagedProcesses,
      readMarkers: () => readManagedMarkers(os.tmpdir()),
      // The console PTY is killed by the in-memory TERMINAL_MANAGER.stop on the
      // stop control itself; here we reap the DETACHED triad (gateway/loop/daemon)
      // that the PTY stop leaves behind.
      consolePtyPids: [],
      killByPort: defaultKillByPort,
      stopDaemon,
      killTree: killManagedTree,
    });
    if (Array.isArray(result?.pending) && result.pending.length) {
      await Promise.allSettled(result.pending);
    }
    const n =
      (result?.killed?.gatewayHosts?.length || 0) +
      (result?.killed?.deliveryLoops?.length || 0) +
      (result?.killed?.daemons?.length || 0);
    if (n) {
      console.error(`[aify] single-agent managed teardown (${reason}): reaped ${n} survivor(s) for agent ${id}`);
    }
    // Marker hygiene (P4): a STOP/REMOVE is the lifecycle end of this managed
    // session; clear its gateway port/key markers so they don't linger.
    try { hermesClearGatewayMarkers(id, os.tmpdir()); } catch { /* best effort */ }
    if (result?.errors?.length) {
      console.error(`[aify] single-agent managed teardown (${reason}) had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
  } catch (error) {
    console.error(`[aify] single-agent managed teardown (${reason}) failed:`, error?.message || error);
  }
}

// Synchronous best-effort variant for the process.on('exit') path
// (cleanupOnExit), where no async work can run. Fires spawnSync kills (taskkill
// /t /f for loops + console-style trees; the gateway port-kill is the async
// path's job — here we kill the tracked daemon pid + delivery-loop trees, the
// processes most likely to be orphaned). Scoped identically; never throws.
//
// NOTE: this sync exit path CANNOT port-kill gateway hosts (defaultKillByPort is
// async and nothing can await on process exit), so a SIGKILLed/crashed bridge's
// gateway-host survivors are not reaped here. The env-bridge BOOT survivor sweep
// (runBootSurvivorSweep) is their backstop — and now that the sweep correctly
// keys ownerLive on owning-bridge freshness (not agent status), it reaps those
// gateway survivors on the next bridge start, so this gap is self-healing.
function runManagedTeardownSync(reason = "bridge exit") {
  if (!IS_ENVIRONMENT_BRIDGE) return;
  const ownedAgentIds = Array.isArray(confirmedManagedTeardownAgentIds)
    ? confirmedManagedTeardownAgentIds
    : [];
  if (!ownedAgentIds.length) return;
  try {
    const found = enumerateManagedSurvivors({
      ownedAgentIds,
      cwdRoots: cwdRootsForEnvironment(),
      listProcesses: listManagedProcesses,
      readMarkers: () => readManagedMarkers(os.tmpdir()),
      consolePtyPids: [],
    });
    for (const l of found.deliveryLoops) {
      try { killManagedTree(l.pid); } catch { /* best effort */ }
    }
    for (const d of found.daemons) {
      // ANTI-OVERKILL: a stale daemon-pid marker can name a pid the OS reused for
      // an UNRELATED operator process. Verify the pid's cmdline is hermes before
      // taskkill /t /f; SKIP + log + clear the stale marker otherwise. Mirrors
      // stopDaemon's tracked-pid cross-check (sync path can't await stopDaemon).
      try {
        if (looksLikeHermesProcess(hermesGetCmdline(d.pid))) {
          killManagedTree(d.pid);
        } else {
          console.error(`[aify] managed teardown sync: tracked daemon pid ${d.pid} for agent ${d.agentId} is not hermes — SKIP (stale daemon-pid marker, pid reused)`);
          try { clearDaemonPid(d.agentId, os.tmpdir()); } catch { /* best effort */ }
        }
      } catch { /* best effort */ }
    }
  } catch (error) {
    console.error(`[aify] managed teardown sync (${reason}) failed:`, error?.message || error);
  }
}

// Build per-agent ownership records for the boot sweep: managed agents in THIS
// environment (within cwdRoots) with their owning bridge id + whether that
// OWNING ENVIRONMENT BRIDGE is alive. Derived from /agents + /sessions +
// /environments. Throws on HTTP failure so the sweep fail-safes to reaping
// nothing.
//
// ownerLive must NOT be derived from the agent's status: after a SIGKILL/crash
// the survivor's detached delivery loop keeps heartbeating + holds its claimer
// lease, so the agent stays online/working. A status-based signal would mark the
// DEAD owner as live and the sweep would skip exactly the orphans it exists to
// kill. Instead we key on owning-bridge freshness: the agent's stored
// runtimeState.bridgeInstanceId vs the CURRENT bridgeId of an ONLINE environment
// (GET /environments — the host-side mirror of the server's
// _resident_bridge_is_fresh check). See bridgeOwnerIsLive.
async function fetchManagedOwnershipForEnv() {
  const environment = effectiveEnvironmentPayload();
  const [agentsRes, sessionsRes, environmentsRes] = await Promise.all([
    httpCall("GET", "/agents"),
    httpCall("GET", `/sessions?environmentId=${encodeURIComponent(environment.id)}&limit=500`),
    httpCall("GET", "/environments"),
  ]);
  const sessionByAgent = new Map();
  for (const session of sessionsRes?.sessions || []) {
    if (session?.agentId && !sessionByAgent.has(session.agentId)) sessionByAgent.set(session.agentId, session);
  }
  const environments = Array.isArray(environmentsRes?.environments) ? environmentsRes.environments : [];
  const records = [];
  for (const [agentId, info] of Object.entries(agentsRes?.agents || {})) {
    if (normalizeSessionMode(info.sessionMode) !== "managed") continue;
    const runtimeState = info.runtimeState || {};
    const session = sessionByAgent.get(agentId);
    const belongsToEnvironment =
      session || String(runtimeState.environmentId || "") === environment.id;
    if (!belongsToEnvironment) continue;
    const workspace = session?.workspace || info.cwd || DEFAULT_CWD;
    if (!workspaceWithinRoots(workspace, environment.cwdRoots)) continue;
    const owningBridgeId = String(runtimeState.bridgeInstanceId || "").trim();
    records.push({
      agentId,
      owningBridgeId,
      ownerLive: bridgeOwnerIsLive(owningBridgeId, {
        environments,
        selfBridgeId: BRIDGE_INSTANCE_ID,
      }),
    });
  }
  return records;
}

// Env-bridge BOOT survivor sweep (before ensureSpawnLoop). Reaps managed-triad
// survivors of dead/crashed predecessors so "restart = zero survivors" holds
// even after SIGKILL — while NEVER touching an agent owned by a currently-live
// different bridge. Fail-safe: if ownership can't be fetched, reaps nothing.
async function runBootSurvivorSweep() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return true;
  let records = null;
  try {
    records = await fetchManagedOwnershipForEnv();
  } catch (error) {
    if (error?.status !== 404) {
      console.error("[aify] boot survivor sweep: ownership query failed — reaping nothing (fail-safe):", error?.message || error);
    }
    return false;
  }
  try {
    const result = reapOrphanedManagedSurvivors({
      selfBridgeId: BRIDGE_INSTANCE_ID,
      cwdRoots: cwdRootsForEnvironment(),
      fetchOwnership: () => records,
      listProcesses: listManagedProcesses,
      readMarkers: () => readManagedMarkers(os.tmpdir()),
      killByPort: defaultKillByPort,
      stopDaemon,
      killTree: killManagedTree,
      // Fresh boot: a survivor whose agent record now reads THIS bridge id is a
      // predecessor's orphan (the heartbeat re-sync can rebind it to self before
      // this sweep reads ownership; a SIGKILL can leave the env row briefly
      // online under the old id). This bridge has spawned no managed children
      // yet, so any running survivor predates the boot and is reapable. A live
      // DIFFERENT bridge's agents are still skipped (owner !== self && ownerLive).
      treatSelfAsOrphan: true,
    });
    if (result?.skipped === "ownership-unavailable") return false;
    if (Array.isArray(result?.pending) && result.pending.length) {
      await Promise.allSettled(result.pending);
    }
    const n =
      (result?.killed?.gatewayHosts?.length || 0) +
      (result?.killed?.deliveryLoops?.length || 0) +
      (result?.killed?.daemons?.length || 0) +
      (result?.killed?.consolePtys?.length || 0);
    if (n) {
      console.error(`[aify] boot survivor sweep: reaped ${n} orphaned managed survivor(s) (owning bridge not live)`);
    }
    if (result?.errors?.length) {
      console.error(`[aify] boot survivor sweep had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
    return true;
  } catch (error) {
    console.error("[aify] boot survivor sweep failed:", error?.message || error);
    return false;
  }
}

// Env-bridge BOOT tombstoned-marker sweep (fix/hermes-leak P4). The survivor
// sweep above kills orphaned PROCESSES; this deletes the stale marker FILES
// (aify-hermes-{port,daemon-pid,key}-<agent>) a REMOVED agent leaves behind.
// A tombstoned agent never relaunches, so its gateway port/key markers are dead
// weight that would otherwise persist forever (the loop's agent-removed teardown
// now clears them too, but a SIGKILLed loop never runs that, so the boot sweep is
// the backstop). Scope: an agent absent from the live `/agents` keyset no longer
// exists in ANY environment, so deleting its markers is machine-safe; a still-
// known agent (incl. a co-located other-env's live agent) is NEVER swept.
// FAIL-SAFE: if `/agents` can't be fetched, the keyset is null → sweep nothing.
async function runBootTombstonedMarkerSweep() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return;
  let knownAgentIds = null;
  try {
    const agentsRes = await httpCall("GET", "/agents");
    knownAgentIds = Object.keys(agentsRes?.agents || {});
  } catch (error) {
    // Unknown keyset → fail-safe (sweep nothing). 404 is just "no agents yet".
    if (error?.status !== 404) {
      console.error("[aify] boot tombstoned-marker sweep: /agents query failed — sweeping nothing (fail-safe):", error?.message || error);
      return;
    }
    knownAgentIds = [];
  }
  try {
    const result = sweepTombstonedMarkers({ knownAgentIds, tempDir: os.tmpdir() });
    if (result?.skipped) return;
    const n = result?.swept?.length || 0;
    if (n) {
      console.error(`[aify] boot tombstoned-marker sweep: cleared markers for ${n} removed agent(s)`);
    }
    if (result?.errors?.length) {
      console.error(`[aify] boot tombstoned-marker sweep had ${result.errors.length} error(s):`, JSON.stringify(result.errors));
    }
  } catch (error) {
    console.error("[aify] boot tombstoned-marker sweep failed:", error?.message || error);
  }
}


function environmentHeartbeatPayload() {
  const hostname = (() => {
    try { return os.hostname() || "unknown-host"; } catch { return "unknown-host"; }
  })();
  const kind = environmentKind();
  const id = String(process.env.AIFY_ENVIRONMENT_ID || `${kind}:${hostname}:default`).trim();
  const terminalSupported = bridgeTerminalSupported();
  return {
    id,
    label: environmentLabel(kind, hostname),
    machineId: MACHINE_ID,
    os: environmentOs(),
    kind,
    bridgeId: BRIDGE_INSTANCE_ID,
    bridgeVersion: BRIDGE_VERSION,
    cwdRoots: cwdRootsForEnvironment(),
    runtimes: advertisedEnvironmentRuntimes(),
    terminal: terminalSupported,
    pty: terminalSupported,
    terminalRuntimes: advertisedTerminalRuntimes({ terminalSupported }),
    metadata: {
      pid: process.pid,
      platform: process.platform,
      arch: process.arch,
      node: process.version,
      cwd: DEFAULT_CWD,
      wslDistro: process.env.WSL_DISTRO_NAME || "",
      bridgeStartedAt: BRIDGE_STARTED_AT,
      // The sha of the code THIS PROCESS IS ACTUALLY RUNNING (v0.2 item B1). It was already
      // computed for the startup banner and then only written to stderr, where nothing can read
      // it — so the one fact that proves a running bridge is current was thrown away at boot.
      //
      // Why it matters here specifically: `aify-doctor`'s `bridge-running` check reads /proc and
      // SKIPS on Windows, so on this host nothing verifies that a running wrapper executes current
      // code. `bridge-installed` only proves the FILES on disk are current, which is a different
      // claim — a process keeps whatever it loaded at boot.
      //
      // That gap has a live artifact, not a hypothetical one: on 2026-08-10 I verified a
      // just-shipped multipart fix through comms_share, saw the OLD corrupted output, and nearly
      // recorded a working fix as broken. My own bridge was pre-restart and nothing said so.
      //
      // Reporting it on registration makes the check platform-independent: the server can compare
      // what each LIVE bridge is running against the checkout, with no process inspection at all.
      bridgeBuild: BRIDGE_BUILD_TAG,
    },
  };
}


// Unified-backing refactor 2026-05-24: read the `managed_via_wrapper` setting
// so the dispatch loop knows which runtimes to skip claiming for (the
// wrapper's child bridge claims those). 5s cache to avoid hammering /settings.
let _managedViaWrapperCache = { fetchedAt: 0, runtimes: new Set() };
async function readManagedViaWrapperRuntimes() {
  if (Date.now() - _managedViaWrapperCache.fetchedAt < 5000) {
    return _managedViaWrapperCache.runtimes;
  }
  try {
    const resp = await httpCall("GET", "/settings");
    const set = managedViaWrapperRuntimesFromSettingsResponse(resp);
    _managedViaWrapperCache = { fetchedAt: Date.now(), runtimes: set };
    return set;
  } catch (_) {
    return _managedViaWrapperCache.runtimes; // best-effort: return stale cache
  }
}

// Reply contract toggle (managed_reply_capture_fallback). True (default) =
// safety-net: auto-mirror the run summary when a delivered run ends without an
// explicit comms_send reply. False = strict: never fabricate a reply from final
// text; leave the run reply-owed. 5s cache to avoid hammering /settings.
let _replyCaptureFallbackCache = { fetchedAt: 0, value: true };
async function readReplyCaptureFallback() {
  if (Date.now() - _replyCaptureFallbackCache.fetchedAt < 5000) {
    return _replyCaptureFallbackCache.value;
  }
  try {
    const resp = await httpCall("GET", "/settings");
    const s = (resp && resp.settings) ? resp.settings : (resp || {});
    const v = s.managed_reply_capture_fallback;
    const value = v === undefined || v === null ? true : !!v;
    _replyCaptureFallbackCache = { fetchedAt: Date.now(), value };
    return value;
  } catch (_) {
    return _replyCaptureFallbackCache.value; // best-effort: stale cache
  }
}

async function reportAgentHeartbeat(agentId, state = {}, activeRun = null) {
  return httpCall(
    "POST",
    `/agents/${encodeURIComponent(agentId)}/heartbeat`,
    currentTurnHeartbeatFields(state, activeRun),
  );
}



function effectiveEnvironmentPayload() {
  const payload = environmentHeartbeatPayload();
  if (remoteEffectiveCwdRoots && remoteEffectiveCwdRoots.length) {
    return { ...payload, cwdRoots: remoteEffectiveCwdRoots };
  }
  return payload;
}

function workspaceWithinRoots(workspace, roots = []) {
  // 2026-06-03: two latent bugs made spawns into the common ['/', '~'] roots
  // (the bridge's default advertised cwdRoots) reject EVERY absolute workspace:
  //   1. The root "/" (meaning "anywhere") had its trailing slash stripped to ""
  //      and was then filter(Boolean)'d OUT, so a "/"-rooted env matched nothing.
  //   2. The root "~" was never expanded to $HOME, so an absolute workspace under
  //      the home dir never matched "~".
  // Result: managed spawns failed with "outside this bridge's advertised roots"
  // for any normal env. Fix: treat "/" as match-all, and expand "~"/"~/..".
  const home = String(process.env.HOME || process.env.USERPROFILE || "")
    .replace(/\\/g, "/")
    .replace(/\/+$/, "");
  const expand = (p) => {
    let s = String(p || "").trim().replace(/\\/g, "/");
    if (s === "~") s = home;
    else if (s.startsWith("~/")) s = `${home}/${s.slice(2)}`;
    return s.replace(/\/+$/, "");
  };
  const rawRoots = (roots || []).map((r) => String(r || "").trim()).filter(Boolean);
  // "/" is the match-all root.
  if (rawRoots.some((r) => r === "/")) return true;
  const value = expand(workspace);
  const normalizedRoots = rawRoots.map(expand).filter(Boolean);
  if (!value || !normalizedRoots.length) return true;
  return normalizedRoots.some((root) => value === root || value.startsWith(`${root}/`));
}

async function heartbeatEnvironment({ syncManaged = true } = {}) {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return false;
  try {
    const response = await httpCall("POST", "/environments/heartbeat", environmentHeartbeatPayload());
    const roots = response?.environment?.cwdRoots;
    if (Array.isArray(roots)) {
      remoteEffectiveCwdRoots = roots.map((root) => String(root || "").trim()).filter(Boolean);
    }
    if (syncManaged) await syncManagedEnvironmentAgents();
    return true;
  } catch (error) {
    // Bootstrap must fail closed: without registration there is no authoritative
    // handover snapshot, so managed adoption/spawn waits for the next retry.
    return false;
  }
}

async function bootstrapEnvironmentBridge() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return { started: false, skipped: "not-environment-bridge" };
  if (environmentBridgeBootstrapped) return { started: true };
  if (environmentBridgeBootstrapPromise) return environmentBridgeBootstrapPromise;

  environmentBridgeBootstrapPromise = bootstrapManagedEnvironmentBridge({
    // Publish this bridge as the environment's current owner first. That makes a
    // superseded predecessor non-live in the ownership snapshot while leaving the
    // managed agents bound to the predecessor until the sweep has reaped them.
    registerEnvironment: () => heartbeatEnvironment({ syncManaged: false }),
    sweepSurvivors: runBootSurvivorSweep,
    sweepTombstones: runBootTombstonedMarkerSweep,
    syncManagedAgents: syncManagedEnvironmentAgents,
    startSpawnLoop: ensureSpawnLoop,
  })
    .then((result) => {
      if (result?.started) environmentBridgeBootstrapped = true;
      return result;
    })
    .catch((error) => {
      console.error("[aify] environment bridge bootstrap failed:", error?.message || error);
      return { started: false, skipped: "bootstrap-error", error };
    })
    .finally(() => {
      environmentBridgeBootstrapPromise = null;
    });
  return environmentBridgeBootstrapPromise;
}

reportEnvironmentOffline = async () => {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE) return;
  const payload = environmentHeartbeatPayload();
  await httpCall("POST", "/environments/heartbeat", {
    ...payload,
    status: "offline",
    metadata: {
      ...(payload.metadata || {}),
      exitPid: process.pid,
      exitAt: new Date().toISOString(),
    },
  });
};

function ensureEnvironmentHeartbeat() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || environmentHeartbeatTimer) return;
  bootstrapEnvironmentBridge().catch((error) => console.error("[aify] environment bridge bootstrap error:", error));
  const intervalMs = Math.max(5000, Number(process.env.AIFY_ENVIRONMENT_HEARTBEAT_MS || 30000));
  environmentHeartbeatTimer = setInterval(() => {
    if (!environmentBridgeBootstrapped) {
      bootstrapEnvironmentBridge().catch((error) => console.error("[aify] environment bridge bootstrap error:", error));
      return;
    }
    heartbeatEnvironment();
  }, intervalMs);
}

// Usage/quota collector (2026-06-26): poll each subscription pool's remaining %% on
// this host (~3 min) and POST to /usage. Env-bridge only — it has the host creds and
// reads the rollouts. Best-effort; a failed poll never disturbs the bridge.
function ensureUsageCollector() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || usageCollectorTimer) return;
  const tick = () => {
    collectUsageOnce({ post: (p) => httpCall("POST", "/usage", p) }).catch(() => {});
    collectConsumptionOnce({
      getAgents: () => httpCall("GET", "/agents").then((r) => (r && r.agents) || {}),
      post: (rows) => httpCall("POST", "/usage/consumption", { rows }),
    }).catch(() => {});
  };
  tick();
  const intervalMs = Math.max(60000, Number(process.env.AIFY_USAGE_POLL_MS || 180000));
  usageCollectorTimer = setInterval(tick, intervalMs);
}

function ensureEnvironmentControlLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || environmentControlTimer) return;
  runEnvironmentControlLoop().catch((error) => console.error("[aify] environment control loop error:", error));
  environmentControlTimer = setInterval(() => {
    runEnvironmentControlLoop().catch((error) => console.error("[aify] environment control loop error:", error));
  }, DISPATCH_POLL_MS);
}

async function runEnvironmentControlLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || environmentControlBusy) return;
  environmentControlBusy = true;
  try {
    const environment = effectiveEnvironmentPayload();
    const claim = await httpCall("POST", "/environments/controls/claim", {
      environmentId: environment.id,
      bridgeId: BRIDGE_INSTANCE_ID,
      machineId: MACHINE_ID,
      waitMs: CLAIM_WAIT_MS,
    }, CLAIM_OPTS);
    noteControlClaimSuccess("environment controls");
    const control = claim?.control;
    if (!control) return;
    if (control.action === "stop") {
      const current = control.currentEnvironment || {};
      const currentMeta = current.metadata || {};
      if (control.requestedBy === "server:superseded-bridge" && current.bridgeId && current.bridgeId !== BRIDGE_INSTANCE_ID) {
        const replacementBits = [
          `replacement bridge ${current.bridgeId}`,
          currentMeta.pid ? `pid ${currentMeta.pid}` : "",
          currentMeta.cwd ? `cwd ${currentMeta.cwd}` : "",
        ].filter(Boolean).join(", ");
        console.error(`[aify] environment ${environment.id} was superseded by ${replacementBits}; this older bridge (${BRIDGE_INSTANCE_ID}) is exiting`);
      } else {
        console.error(`[aify] environment stop requested for ${environment.id}; bridge exiting`);
      }
      try {
        await httpCall("PATCH", `/environments/controls/${encodeURIComponent(control.id)}`, {
          status: "completed",
        });
      } catch {
        // The process is going down anyway; best effort.
      }
      // Supersede / env-stop path: route through shutdownWithStatus so the WS2
      // managed-triad teardown (runManagedTeardownForBridge) reaps this older
      // bridge's detached survivors before it exits — same clean-slate guarantee
      // as a SIGINT/SIGTERM restart.
      setTimeout(() => { shutdownWithStatus(0); }, 50);
      return;
    }
    await httpCall("PATCH", `/environments/controls/${encodeURIComponent(control.id)}`, {
      status: "failed",
      error: `Unsupported environment control action: ${control.action}`,
    });
  } catch (error) {
    if (error?.status !== 404) {
      noteControlClaimFailure("environment controls", error);
    }
  } finally {
    environmentControlBusy = false;
  }
}

function ensureSpawnLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || spawnLoopTimer) return;
  runSpawnLoop().catch((error) => console.error("[aify] spawn loop error:", error));
  spawnLoopTimer = setInterval(() => {
    runSpawnLoop().catch((error) => console.error("[aify] spawn loop error:", error));
  }, DISPATCH_POLL_MS);
}

function ensureTerminalControlLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || terminalControlTimer || !bridgeTerminalSupported()) return;
  runTerminalControlLoop().catch((error) => console.error("[aify] terminal control loop error:", error));
  terminalControlTimer = setInterval(() => {
    runTerminalControlLoop().catch((error) => console.error("[aify] terminal control loop error:", error));
  }, TERMINAL_CONTROL_POLL_MS);
}

async function updateTerminalControl(controlId, body) {
  return httpCall("PATCH", `/terminals/controls/${encodeURIComponent(controlId)}`, body);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function extractTerminalSessionHandle(runtime = "", command = "") {
  return extractRuntimeSessionHandleFromCommand(runtime, command);
}

async function handleVirtualTerminalControl(agentId, terminalId, control) {
  const action = String(control.action || "").trim();
  if (action === "input") {
    const rawBody = String(control.body || "");
    await VIRTUAL_TERMINAL_INPUT.append(agentId, terminalId, rawBody);
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  if (action === "resize") {
    // The synthesized terminal has no PTY dimensions; ack so the dashboard
    // doesn't keep retrying. Future: surface cols/rows in the dashboard hint.
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  if (action === "stop") {
    const session = getPiSession(agentId);
    if (session) await session.stop("virtual-terminal stop control");
    VIRTUAL_TERMINALS_BY_AGENT.delete(agentId);
    VIRTUAL_TERMINAL_INPUT.remove(terminalId);
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "stopped" });
    return;
  }
  if (action === "start") {
    // Virtual terminals are created via /agents/{id}/virtual-terminal/ensure,
    // not via the start control. Treat a stray start as a no-op ack so the
    // dashboard's reconcile path doesn't infinite-retry.
    await updateTerminalControl(control.id, { status: "completed", terminalStatus: "running" });
    return;
  }
  throw new Error(`Unsupported virtual-terminal control action: ${action}`);
}

// WS4 Task 4.2: host-reported dead-PTY marking. The server cannot probe a
// remote host pid; only the OWNING env bridge can. For each console PTY this
// bridge owns in-memory that is still `attached` but whose local pid is no
// longer alive, POST /terminals/{id}/report-dead so the server marks the row
// stopped + invalidates live-state (a frozen/crashed console can otherwise keep
// manufacturing presence). Best-effort; never throws. Does NOT kill anything —
// the in-memory exit path owns real teardown; this only reconciles stale rows.
async function reportDeadOwnedTerminals() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || !bridgeTerminalSupported()) return [];
  try {
    const owned = TERMINAL_MANAGER.listOwnedSessions?.() || [];
    if (!owned.length) return [];
    return await reportDeadOwnedSessions(owned, {
      report: async ({ terminalId, pid }) => {
        await httpCall("POST", `/terminals/${encodeURIComponent(terminalId)}/report-dead`, {
          bridgeId: BRIDGE_INSTANCE_ID,
          processId: pid != null ? String(pid) : "",
          reason: "Console PTY process is no longer alive (host-reported).",
        });
        console.error(`[aify] terminal ${terminalId} (pid ${pid}) is dead locally — reported to server for stop/reconcile`);
      },
    });
  } catch (error) {
    logTransientOrError("[aify] dead-PTY report failed", error);
    return [];
  }
}

async function runTerminalControlLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || terminalControlBusy || !bridgeTerminalSupported()) return;
  terminalControlBusy = true;
  try {
    // Reconcile any console PTY this bridge owns whose local pid has died but
    // whose server row is still `attached` (WS4 Task 4.2). Cheap + best-effort.
    await reportDeadOwnedTerminals();
    const environment = effectiveEnvironmentPayload();
    const claim = await httpCall("POST", "/terminals/controls/claim", {
      environmentId: environment.id,
      bridgeId: BRIDGE_INSTANCE_ID,
      waitMs: CLAIM_WAIT_MS,
    }, CLAIM_OPTS);
    noteControlClaimSuccess("terminal controls");
    const controls = claim?.controls || [];
    for (const control of controls) {
      try {
        const terminalId = String(control.terminalId || "").trim();
        if (!terminalId) throw new Error("Terminal control missing terminal id");
        const virtualAgentId = findAgentIdForVirtualTerminal(terminalId);
        if (virtualAgentId) {
          await handleVirtualTerminalControl(virtualAgentId, terminalId, control);
          continue;
        }
        if (control.action === "start") {
          const terminalRes = await httpCall("GET", `/terminals/${encodeURIComponent(terminalId)}`);
          const terminal = terminalRes?.terminal || {};
          const workspace = terminal.workspace || DEFAULT_CWD;
          if (!workspaceWithinRoots(workspace, environment.cwdRoots)) {
            throw new Error(`Terminal workspace "${workspace}" is outside this bridge's advertised roots`);
          }
          const command = terminal.command || control.body || "";
          const runtime = normalizeRuntime(terminal.runtime || "");
          const sessionHandle = extractTerminalSessionHandle(runtime, command);
          let agentInfo = {};
          if (terminal.agentId) {
            try {
              const agentResp = await httpCall("GET", `/agents/${encodeURIComponent(terminal.agentId)}`);
              agentInfo = agentResp?.agent || {};
            } catch {
              agentInfo = {};
            }
          }
          let managedViaWrapper = runtime === "claude-code";
          try {
            const _wrapperRuntimes = await readManagedViaWrapperRuntimes();
            managedViaWrapper = managedViaWrapper || Boolean(_wrapperRuntimes && _wrapperRuntimes.has?.(runtime));
          } catch { /* best effort */ }
          const wrapperEnv = terminalChildEnv({ runtime, sessionHandle, terminal, workspace, terminalId, agentInfo, managedViaWrapper });
          if (managedViaWrapper && terminal.agentId) wrapperEnv.AIFY_AGENT_ID = String(terminal.agentId);
          const started = await TERMINAL_MANAGER.start({
            id: terminalId,
            command,
            cwd: workspace,
            env: wrapperEnv,
            cols: control.cols || 100,
            rows: control.rows || 28,
            runtime,
            sessionHandle,
            agentId: terminal.agentId || "",
            // FIX 6 (2026-06-03): tag the PTY's session mode so an env-bridge
            // stopAll never reaps an operator-launched resident console.
            sessionMode: normalizeSessionMode(agentInfo.sessionMode || agentInfo.session_mode),
          });
          await updateTerminalControl(control.id, {
            status: "completed",
            terminalStatus: "attached",
            output: `[terminal attached pid=${started.pid}]\n`,
            // Report the PTY root pid so the server persists it
            // (terminal_sessions.process_id). Lets Dashboard Stop/Restart
            // kill-by-pid if THIS bridge later dies and orphans the PTY.
            processId: started.pid != null ? String(started.pid) : "",
          });
        } else if (control.action === "input") {
          // Raw passthrough: callers own newline semantics. Prompt answers are
          // handled separately by TerminalProcessManager's cursor-verified rules.
          const rawBody = String(control.body || "");
          TERMINAL_MANAGER.input(terminalId, rawBody);
          await updateTerminalControl(control.id, { status: "completed", terminalStatus: "attached" });
        } else if (control.action === "resize") {
          TERMINAL_MANAGER.resize(terminalId, control.cols || 0, control.rows || 0);
          await updateTerminalControl(control.id, { status: "completed", terminalStatus: "attached" });
        } else if (control.action === "stop") {
          const stopResult = await TERMINAL_MANAGER.stop(terminalId, "terminal stop control");
          // Kill-by-pid fallback (2026-06-02): the in-memory stop path is a
          // no-op when THIS bridge never owned the PTY (Map miss) — the owning
          // bridge restarted/died and orphaned a still-live console. The stop
          // control carries the persisted PTY root pid (server-scoped to this
          // bridge's environment, so machine-local). Reap the orphan by pid so
          // Stop/Restart isn't silently dropped. Owned-in-memory path unchanged.
          const orphanPid = orphanPidToKill(stopResult, control);
          if (orphanPid) {
            // Identity guard (2026-07-10 bughunt HIGH): this pid is the PRIOR
            // spawn's persisted PTY root and the fallback fires only on the
            // owning-bridge-gone path — the window where Windows may have RECYCLED
            // it onto a live sibling agent's worker. Refuse only when the cmdline
            // positively names a DIFFERENT agent; fail-open otherwise so a real
            // orphan Stop is never dropped. terminateProcessTree's pidIsSelfProtected
            // still blocks the bridge/shell/init separately.
            if (orphanPidReapAllowed(orphanPid, control, { getCmdline: hermesGetCmdline })) {
              TERMINAL_MANAGER.killByPid(orphanPid);
            } else {
              console.error(
                `[aify] orphan Stop: refused kill-by-pid ${orphanPid} for terminal ${terminalId} — ` +
                `its command line identifies a different agent (recycled pid?); leaking rather than cross-killing`,
              );
            }
          }
          // fix/hermes-leak P2: a STOP/REMOVE of a MANAGED HERMES agent must tear
          // down the WHOLE triad (detached gateway host + delivery loop + daemon),
          // not just the PTY above — otherwise Stop/Remove leaves the gateway/loop/
          // daemon orphaned (the big latent leak). AGENT-SCOPED: stopControlTriadAgentId
          // returns the agent id ONLY for a managed-hermes stop (sessionMode=managed
          // or the REMOVE body sentinel); a resident hermes / claude / another runtime
          // returns null and is never touched.
          const triadAgentId = stopControlTriadAgentId(control);
          if (triadAgentId && IS_ENVIRONMENT_BRIDGE) {
            await runSingleAgentManagedTeardown(triadAgentId, "dashboard stop/remove");
          }
          await updateTerminalControl(control.id, { status: "completed", terminalStatus: "stopped" });
        } else {
          throw new Error(`Unsupported terminal control action: ${control.action}`);
        }
      } catch (error) {
        await updateTerminalControl(
          control.id,
          terminalControlFailurePatch(control.action, error),
        ).catch(() => {});
      }
    }
  } catch (error) {
    if (error?.status !== 404) {
      noteControlClaimFailure("terminal controls", error);
    }
  } finally {
    terminalControlBusy = false;
  }
}

function noteSpawnClaimFailure(error) {
  spawnClaimFailureCount += 1;
  const now = Date.now();
  const decision = claimFailureDecision({
    count: spawnClaimFailureCount,
    lastLogAt: spawnClaimLastLogAt,
    now,
  });
  spawnClaimLastLogAt = decision.nextLastLogAt;
  const detail = error?.message || String(error || "unknown error");
  const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
  if (decision.debug && String(process.env.AIFY_DEBUG || "").trim() === "1") {
    console.debug(`[aify] spawn claim transient failure against ${target}: ${detail}; retrying`);
  }
  if (decision.warn) {
    const fallbacks = SERVER_URLS.length > 1 ? `; configured URLs: ${SERVER_URLS.join(", ")}` : "";
    console.error(
      `[aify] spawn claim failed (${spawnClaimFailureCount} consecutive) against ${target}: ${detail}${fallbacks}. ` +
      "The bridge will keep retrying; check that the service is running and reachable from this shell.",
    );
  }
}

function noteSpawnClaimSuccess() {
  if (spawnClaimFailureCount > 0) {
    if (claimRecoveryDecision(spawnClaimFailureCount).log) {
      console.error(`[aify] spawn claim recovered after ${spawnClaimFailureCount} failure(s)`);
    }
    spawnClaimFailureCount = 0;
    spawnClaimLastLogAt = 0;
  }
}

function isActiveManagedSessionStatus(status) {
  return ["starting", "running", "recovering", "restarting"].includes(String(status || "").toLowerCase());
}

async function syncManagedEnvironmentAgents() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || managedEnvironmentSyncBusy) return;
  managedEnvironmentSyncBusy = true;
  try {
    const environment = effectiveEnvironmentPayload();
    const [agentsRes, sessionsRes] = await Promise.all([
      httpCall("GET", "/agents"),
      httpCall("GET", `/sessions?environmentId=${encodeURIComponent(environment.id)}&limit=500`),
    ]);
    // A managed agent can be taken over by an operator as resident while this
    // environment bridge remains alive. Drop that now-stale cached managed row
    // as soon as a successful full snapshot proves the mode changed. Without
    // this reconciliation, graceful shutdown can target the resident's
    // identical hermes-managed-host delivery loop and kill its gateway.
    reconcileManagedStateWithSnapshot(REMOTE_AGENT_STATE, agentsRes.agents || {});
    const availableRuntimes = new Set((environment.runtimes || []).filter((item) => item?.available !== false).map((item) => normalizeRuntime(item.runtime)));
    const activeSessionsByAgent = new Map();
    for (const session of sessionsRes.sessions || []) {
      if (!session?.agentId || !isActiveManagedSessionStatus(session.status)) continue;
      if (!activeSessionsByAgent.has(session.agentId)) activeSessionsByAgent.set(session.agentId, session);
    }

    for (const [agentId, managedInfo] of Object.entries(agentsRes.agents || {})) {
      if (normalizeSessionMode(managedInfo.sessionMode) !== "managed") continue;
      if ((managedInfo.launchMode || "managed") === "none") continue;
      const capabilities = managedInfo.capabilities || [];
      if (capabilities.length && !capabilities.includes("managed-run")) continue;

      const session = activeSessionsByAgent.get(agentId);
      const runtimeState = managedInfo.runtimeState || {};
      const belongsToEnvironment =
        session ||
        String(runtimeState.environmentId || "") === environment.id;
      if (!belongsToEnvironment) continue;

      // `available` means this environment can cold-start the agent, not that a
      // worker exists to host. The spawn loop owns that wake path. Adopting every
      // historical available agent here made the 3s dispatch loop GET + heartbeat
      // each one forever. An active session remains authoritative even if its
      // derived status is briefly stale during bridge handover; runSpawnLoop adds
      // newly spawned workers to REMOTE_AGENT_STATE itself.
      if (!session && !managedAgentNeedsDispatchHosting(managedInfo)) continue;

      const runtime = normalizeRuntime((session?.runtime || managedInfo.runtime || "generic"));
      if (!availableRuntimes.has(runtime)) continue;
      const workspace = session?.workspace || managedInfo.cwd || DEFAULT_CWD;
      if (!workspaceWithinRoots(workspace, environment.cwdRoots)) continue;

      const nextRuntimeState = {
        ...runtimeState,
        bridgeInstanceId: BRIDGE_INSTANCE_ID,
        environmentId: environment.id,
        mode: session?.mode || runtimeState.mode || "managed-warm",
      };
      if (session?.spawnRequestId) nextRuntimeState.spawnRequestId = session.spawnRequestId;
      try {
        await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
          runtimeState: nextRuntimeState,
        });
      } catch {
        // Best effort; the claim guard also checks the current environment bridge.
      }

      REMOTE_AGENT_STATE.set(agentId, {
        info: {
          agentId,
          role: managedInfo.role || "coder",
          name: managedInfo.name || agentId,
          cwd: workspace,
          model: managedInfo.model || "",
          instructions: managedInfo.instructions || "",
          runtime,
          machineId: managedInfo.machineId || environment.machineId || MACHINE_ID,
          launchMode: "managed",
          sessionMode: "managed",
          sessionHandle: session?.sessionHandle || managedInfo.sessionHandle || "",
          managedBy: managedInfo.managedBy || "dashboard",
          capabilities,
          runtimeConfig: managedInfo.runtimeConfig || {},
          runtimeState: nextRuntimeState,
        },
      });
    }
    if (REMOTE_AGENT_STATE.size) ensureDispatchLoop();
  } catch (error) {
    if (error?.status !== 404) {
      console.error("[aify] managed environment sync failed:", error?.message || error);
    }
  } finally {
    managedEnvironmentSyncBusy = false;
  }
}

async function runSpawnLoop() {
  if (!IS_REMOTE || !IS_ENVIRONMENT_BRIDGE || spawnLoopBusy) return;
  spawnLoopBusy = true;
  try {
    const environment = effectiveEnvironmentPayload();
    let claim;
    try {
      claim = await httpCall("POST", "/spawn-requests/claim", {
        environmentId: environment.id,
        bridgeId: BRIDGE_INSTANCE_ID,
        machineId: MACHINE_ID,
        waitMs: CLAIM_WAIT_MS,
      }, CLAIM_OPTS);
    } catch (error) {
      if (error?.status !== 404) {
        noteSpawnClaimFailure(error);
      }
      return;
    }
    noteSpawnClaimSuccess();
    const spawnRequest = claim?.spawnRequest;
    if (!spawnRequest) return;

    const workspace = spawnRequest.workspace || spawnRequest.workspaceRoot || DEFAULT_CWD;
    if (!workspaceWithinRoots(workspace, environment.cwdRoots)) {
      await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
        status: "failed",
        bridgeId: BRIDGE_INSTANCE_ID,
        error: `Workspace "${workspace}" is outside this bridge's advertised roots`,
      });
      return;
    }

    await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
      status: "starting",
      bridgeId: BRIDGE_INSTANCE_ID,
    });

    const runtime = normalizeRuntime(spawnRequest.runtime || "generic");
    const runtimeConfig =
      (spawnRequest.spawnSpec?.metadata && typeof spawnRequest.spawnSpec.metadata.runtimeConfig === "object")
        ? spawnRequest.spawnSpec.metadata.runtimeConfig
        : {};
    const requestedSessionHandle = String(spawnRequest.sessionHandle || "").trim();
    const capabilities = defaultCapabilitiesForRuntime(runtime, "managed", requestedSessionHandle, runtimeConfig);
    const runtimeState = {
      bridgeInstanceId: BRIDGE_INSTANCE_ID,
      environmentId: environment.id,
      spawnRequestId: spawnRequest.id,
      mode: spawnRequest.mode || "managed-warm",
      resumePolicy: spawnRequest.resumePolicy || "native_first",
    };
    if (requestedSessionHandle) {
      if (runtime === "codex") {
        runtimeState.threadId = requestedSessionHandle;
      } else {
        runtimeState.sessionId = requestedSessionHandle;
      }
    }
    await httpCall("PATCH", `/spawn-requests/${encodeURIComponent(spawnRequest.id)}`, {
      status: "running",
      bridgeId: BRIDGE_INSTANCE_ID,
      processId: String(process.pid),
      sessionHandle: requestedSessionHandle,
      runtimeState,
      capabilities: {
        persistent: true,
        nativeResume: Boolean(requestedSessionHandle) || runtime === "codex" || runtime === "hermes" || runtime === "opencode" || runtime === "pi",
        bridgeResume: true,
        cliAttach: false,
        interrupt: true,
        streaming: true,
        tokenTelemetry: false,
        costTelemetry: false,
        contextReset: true,
      },
      telemetry: {},
    });

    REMOTE_AGENT_STATE.set(spawnRequest.agentId, {
      info: {
        agentId: spawnRequest.agentId,
        role: spawnRequest.role || "coder",
        name: spawnRequest.name || spawnRequest.agentId,
        cwd: workspace,
        model: spawnRequest.spawnSpec?.model || "",
        instructions: spawnRequest.spawnSpec?.instructions || "",
        runtime,
        machineId: MACHINE_ID,
        launchMode: "managed",
        sessionMode: "managed",
        sessionHandle: requestedSessionHandle,
        managedBy: spawnRequest.createdBy || "dashboard",
        capabilities,
        runtimeConfig,
        runtimeState,
      },
    });
    ensureDispatchLoop();
    console.error(`[aify] spawned managed agent "${spawnRequest.agentId}" from request ${spawnRequest.id}`);
  } finally {
    spawnLoopBusy = false;
  }
}

function ensureDispatchLoop() {
  if (!IS_REMOTE || dispatchLoopTimer) return;
  if (!localAgentNeedsDispatchHosting({
    agentId: AIFY_AGENT_ID,
    channelsEnabled: String(process.env.AIFY_CHANNELS_ENABLED || "").trim() === "1",
  })) return;
  dispatchLoopTimer = setInterval(() => {
    runDispatchLoop().catch((error) => console.error("[aify] dispatch loop error:", error));
  }, DISPATCH_POLL_MS);
}

ensureEnvironmentControlLoop();
ensureUsageCollector();
// Register the replacement bridge, reap the predecessor's managed survivors,
// then adopt managed ownership and start spawning. The serialized bootstrap
// closes the live-old-bridge handover gap and retries on later heartbeats when
// the service or ownership snapshot is unavailable.
ensureEnvironmentHeartbeat();
ensureTerminalControlLoop();



async function runDispatchLoop() {
  if (!IS_REMOTE || dispatchLoopBusy) return;
  dispatchLoopBusy = true;
  try {
    // Long-poll the dispatch claim ONLY when this bridge hosts a single agent (every
    // resident claude/codex/hermes bridge — the common case). This loop iterates its
    // agents SEQUENTIALLY, so a long idle wait per agent would serialize and delay the
    // others; a multi-agent env-bridge therefore keeps the legacy short-poll (waitMs=0).
    const soloAgentBridge = REMOTE_AGENT_STATE.size <= 1;
    for (const [agentId, state] of REMOTE_AGENT_STATE.entries()) {
      if (!state?.info) continue;

      const active = ACTIVE_RUNS.get(agentId);
      if (active) {
        const dropped = await reconcileLocalActiveRun(agentId, state, active);
        if (!dropped) {
          // Heartbeat while an active run is genuinely owned by this process.
          reportAgentHeartbeat(agentId, state, active).catch(() => {});
          await processRunControls(agentId, active).catch((error) => {
            logTransientOrError("[aify] control processing error", error);
          });
          continue;
        }
      }

      try {
        const agentRes = await httpCall("GET", `/agents/${encodeURIComponent(agentId)}`);
        const liveAgent = agentRes.agent || null;
        if (liveAgent) {
          if (
            normalizeSessionMode(liveAgent.sessionMode) === "resident" &&
            (liveAgent.launchMode || "") === "none" &&
            String(liveAgent.statusRaw || liveAgent.status || "").toLowerCase().startsWith("stopped")
          ) {
            terminateResidentHost(`Stop requested for resident agent "${agentId}"`);
            continue;
          }
          state.info = {
            ...state.info,
            ...liveAgent,
            runtimeState: liveAgent.runtimeState || state.info.runtimeState || {},
          };
          if (
            normalizeSessionMode(liveAgent.sessionMode) === "managed" &&
            liveAgent.runtimeState?.pendingResidentTakeover &&
            String(liveAgent.runtimeState.pendingResidentTakeover.bridgeId || "") === BRIDGE_INSTANCE_ID
          ) {
            // A CLI registered for this agent while a managed turn was active.
            // Keep heartbeating, but do not claim work until the backend
            // promotes ownership after that active turn reaches a terminal
            // state.
            continue;
          }
        }
      } catch (error) {
        // If the server forgot about this agent (404), auto-re-register from
        // cached state instead of silently polling a dead agentId forever.
        // This is the common "re-registration fixes it" symptom.
        if (error?.status === 404) {
          console.error(`[aify] agent "${agentId}" missing from server; auto-re-registering`);
          await reregisterAgentFromState(agentId, state);
          CONSECUTIVE_FAILURES.set(agentId, 0);
          continue;
        }
        if (error?.status === 410) {
          forgetRemoteAgent(agentId, "server marked it intentionally removed");
          continue;
        }
        // Other errors: log only, keep going.
      }

      if (await residentRuntimeBindingLost(agentId, state.info)) {
        await reportResidentRuntimeLost(agentId, state.info, "resident Codex app-server is unreachable");
        continue;
      }

      // Heartbeat after validating resident runtime reachability. This avoids
      // orphaned MCP child processes keeping a closed resident CLI "active".
      reportAgentHeartbeat(agentId, state).catch(() => {});

      const managedViaWrapperRuntimes = await readManagedViaWrapperRuntimes().catch(() => null);
      let executionModes = supportedExecutionModes(state.info, { managedViaWrapperRuntimes });
      // When this bridge IS the wrapper child for a managed agent (env
      // AIFY_MANAGED_VIA_WRAPPER=1 set by server.js when it spawned the
      // wrapper PTY), claim channel + resident regardless of the agent's
      // recorded session_mode. The wrapper IS the backing — its in-process
      // bridge owns delivery via the runtime's local backing (gateway / app-
      // server / RPC). Mirror of how claude-channel.js polls for channel +
      // resident from inside claude-aify. Operator-stated 2026-05-25:
      // "managed workers are just pseudo terminals running resident sessions
      // in them".
      //
      // EXCEPTION (managed-hermes visible-TUI, 2026-05-31): hermes' wrapper
      // child is the thin `hermes --tui` (a WS client); channel/resident
      // delivery is owned by the per-agent `hermes-managed-host.js run` loop
      // (bridgeKind="channel-sidecar"). If this hermes wrapper child also
      // claimed channel runs it would RACE that loop and route the run through
      // the leftover ChannelDelegatedController (auto-mirrored summary instead
      // of the real agent reply). wrapperChildExecutionModes excludes hermes.
      if (String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1" && String(state.info?.agentId || agentId || "") === (process.env.AIFY_AGENT_ID || "")) {
        executionModes = wrapperChildExecutionModes(executionModes, {
          runtime: normalizeRuntime(state.info?.runtime || ""),
          isWrapperChild: true,
        });
      }
      if (!executionModes.length) continue;

      // Claim all available dispatches and merge into one turn. The server
      // queues messages one by one as they arrive; the bridge batches them
      // for delivery so the agent sees everything at once. Symmetric with
      // the Claude channel bridge's batch notification.
      const batchedRuns = [];
      for (let i = 0; i < 20; i++) {
        let claim;
        try {
          claim = await httpCall("POST", "/dispatch/claim", {
            agentId,
            machineId: state.info.machineId || MACHINE_ID,
            bridgeId: BRIDGE_INSTANCE_ID,
            executionModes,
            // Long-poll only the FIRST claim of the batch (wait for work to arrive), and
            // only on a single-agent bridge (see soloAgentBridge). The remaining iterations
            // drain already-queued runs and must return at once.
            waitMs: (i === 0 && soloAgentBridge ? CLAIM_WAIT_MS : 0),
          }, (i === 0 && soloAgentBridge ? CLAIM_OPTS : {}));
          CONSECUTIVE_FAILURES.set(agentId, 0);
        } catch (error) {
          if (error?.status === 404) {
            console.error(`[aify] dispatch/claim 404 for "${agentId}"; auto-re-registering`);
            await reregisterAgentFromState(agentId, state);
            CONSECUTIVE_FAILURES.set(agentId, 0);
          } else if (error?.status === 410) {
            forgetRemoteAgent(agentId, "server marked it intentionally removed");
            break;
          } else {
            const count = (CONSECUTIVE_FAILURES.get(agentId) || 0) + 1;
            CONSECUTIVE_FAILURES.set(agentId, count);
            if (count >= AUTO_REREGISTER_AFTER_FAILURES) {
              console.error(`[aify] ${count} consecutive dispatch/claim failures for "${agentId}" (last: ${error?.message || error}); attempting auto-re-register`);
              await reregisterAgentFromState(agentId, state);
              CONSECUTIVE_FAILURES.set(agentId, 0);
            }
          }
          break;
        }
        if (!claim?.run) break;
        batchedRuns.push(claim.run);
      }
      if (!batchedRuns.length) continue;

      const run = batchedRuns[0];
      if (batchedRuns.length > 1) {
        const extras = batchedRuns.slice(1).map((r, i) =>
          `--- Message ${i + 2} of ${batchedRuns.length} ---\nFrom: ${r.from}\nSubject: ${r.subject}\n${r.body || ""}`
        ).join("\n\n");
        run.body = `${run.body || ""}\n\n${extras}`;
        run.subject = `${batchedRuns.length} messages (latest: ${run.subject})`;
      }
      const runtime = normalizeRuntime(state.info.runtime || "generic");
      if (run.requestedRuntime && normalizeRuntime(run.requestedRuntime) !== runtime) {
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
          status: run.mode === "require_start" ? "failed" : "cancelled",
          error: `Requested runtime "${run.requestedRuntime}" does not match registered runtime "${runtime}"`,
          agentStatus: "idle",
          appendEvent: `Skipped: requested runtime "${run.requestedRuntime}" does not match "${runtime}"`,
          eventType: "skipped",
        });
        continue;
      }
      if (!canLaunchRuntime(runtime)) {
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
          status: run.mode === "require_start" ? "failed" : "cancelled",
          error: `Runtime "${runtime}" does not support active dispatch`,
          agentStatus: "idle",
          appendEvent: `Skipped: runtime "${runtime}" does not support active dispatch`,
          eventType: "skipped",
        });
        continue;
      }
      const runtimeState = state.info.runtimeState || {};
      let turnBusyStarted = false;
      await reportTurnBusy(agentId, state, {
        busy: true,
        runId: run.id,
        runtime,
      }).then(() => {
        turnBusyStarted = true;
      }).catch(() => {});
      try {
        await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
          status: "running",
          runtime,
          agentStatus: "working",
          appendEvent: `Starting ${runtime} run for "${run.subject}"`,
          eventType: "runtime",
        });
      } catch (error) {
        if (turnBusyStarted) {
          await reportTurnBusy(agentId, state, {
            busy: false,
            runId: run.id,
            runtime,
          }).catch(() => {});
        }
        throw error;
      }

      // Pass managedViaWrapper into the controller so native RPC adapters
      // (CodexController / HermesController) can short-circuit to a delegated
      // marker when the wrapper's child bridge owns delivery. Defensive: if
      // the main bridge dispatch loop's executionMode gate (Task A4) somehow
      // misses a wrapper-backed managed run, the controller still no-ops
      // rather than competing with the wrapper.
      //
      // BUT: when THIS bridge IS the wrapper child (AIFY_MANAGED_VIA_WRAPPER=1),
      // it IS the wrapper — it should NOT short-circuit. The wrapper child
      // needs to actually deliver via the runtime's local backing (gateway /
      // app-server). Only the main bridge should short-circuit.
      const _runRuntime = normalizeRuntime(state.info?.runtime || "");
      const _isWrapperChild = String(process.env.AIFY_MANAGED_VIA_WRAPPER || "").trim() === "1";
      const _isManagedViaWrapper = !_isWrapperChild && Boolean(_runRuntime && managedViaWrapperRuntimes && managedViaWrapperRuntimes.has?.(_runRuntime));
      const controller = launchRuntimeRun({
        agentId,
        agentInfo: state.info,
        run,
        runtimeState,
        managedViaWrapper: _isManagedViaWrapper,
        callbacks: {
          // Plan 4 Task 13 (2026-05-25): controllers fire this when their
          // initial handshake completes (WS app-server initialize, gateway
          // connect, pi agent_ready, etc.). Maps to PATCH /agents/{id}/ready
          // so operators can see "ready" as a distinct state from "online".
          onReady: () => {
            httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/ready`, {
              ready: true,
              requestedBy: "controller-handshake",
            }).catch(() => { /* best-effort */ });
          },
          onEvent: async (eventType, text) => {
            try {
              await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
                appendEvent: text,
                eventType,
              });
            } catch {
              // best effort
            }
          },
          onRuntimeState: async (nextState) => {
            try {
              state.info.runtimeState = { ...(state.info.runtimeState || {}), ...nextState };
              await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
                runtimeState: state.info.runtimeState,
              });
            } catch {
              // best effort
            }
          },
          onRefs: async (refs) => {
            try {
              const body = {};
              if (refs.threadId) body.externalThreadId = refs.threadId;
              if (refs.turnId) body.externalTurnId = refs.turnId;
              if (Object.keys(body).length > 0) {
                await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, body);
              }
            } catch {
              // best effort
            }
          },
          // TERTIARY pure-event (2026-06-19): wire codex's native app-server turn events to the
          // turn-state poster — turn/started → working, turn/completed → cleared — so managed
          // codex status is event-EXACT instead of leaning on the 5s rollout-tail poll. Both are
          // idempotent (reportTurnBusy is ownership-guarded) and additive to the existing
          // dispatch-boundary + rollout-detector signals, so they only sharpen, never conflict.
          onTurnStart: async () => {
            try { await reportTurnBusy(agentId, state, { busy: true, runId: run.id, runtime: "codex" }); } catch { /* best-effort */ }
          },
          onTurnEnd: async () => {
            try { await reportTurnBusy(agentId, state, { busy: false, runId: run.id, runtime: "codex" }); } catch { /* best-effort */ }
          },
          // Fired when the runtime controller had to discard an unloadable
          // thread/session and start a fresh one. Non-empty handles are
          // persisted through re-registration; explicit clears use the
          // lightweight session-handle endpoint so a poisoned handle is gone
          // even if the fresh run fails before discovering its replacement.
          onSessionHandleChange: async (newHandle, meta = {}) => {
            const nextHandle = String(newHandle || "").trim();
            const metaLabel = meta?.reason ? ` (reason: ${meta.reason}, previous: ${meta.previous || ""})` : "";
            try {
              if (!nextHandle && meta?.reason) {
                state.info.sessionHandle = "";
                state.info.runtimeState = runtimeStateWithoutSessionHandle(
                  state.info.runtime || "",
                  state.info.runtimeState || {},
                );
                await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/session-handle`, {
                  sessionHandle: "",
                  requestedBy: "pi-rpc-heal",
                });
                await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
                  runtimeState: state.info.runtimeState,
                });
                console.error(`[aify] cleared stale sessionHandle for "${agentId}"${metaLabel}`);
                return;
              }
              if (!nextHandle) return;
              state.info.sessionHandle = nextHandle;
              await reregisterAgentFromState(agentId, state);
              console.error(`[aify] healed sessionHandle for "${agentId}" → ${nextHandle}${metaLabel}`);
            } catch (error) {
              console.error(`[aify] failed to persist healed sessionHandle for "${agentId}": ${error?.message || error}`);
            }
          },
          // Synthesized terminal_session row backing the bridge's native
          // RPC controller. Pi (Phase 2): persistent omp --mode rpc child
          // streams its event feed through this sink. Hermes: per-dispatch
          // `hermes chat -q -Q` controller pushes request/response frames.
          // PiController (managed mode only post-Plan-2 flip) wires this
          // sink via session.attachTerminalSink. Other runtimes return null
          // and stay on their existing visibility surface.
          terminalSinkProvider: async ({ agentId: provId, agentInfo }) => {
            const rt = normalizeRuntime(agentInfo?.runtime || "");
            // Phases 2 + 7 + 5/6: pi (persistent), hermes (per-dispatch
            // with synth feed), codex (per-dispatch with synth feed),
            // opencode (per-dispatch with synth feed). Codex/opencode
            // still use per-dispatch controllers; the synth terminal
            // gives operators visible Console activity even before the
            // full Phase 5/6 persistent-worker pool refactor.
            if (rt !== "pi" && rt !== "hermes" && rt !== "codex" && rt !== "opencode") return null;
            try {
              const entry = await ensureVirtualTerminal(provId, agentInfo, rt);
              if (!entry?.terminalId) return null;
              return createVirtualTerminalSink(entry.terminalId);
            } catch (error) {
              console.error(`[aify] virtual-terminal/ensure failed for "${provId}" (runtime=${rt}): ${error?.message || error}`);
              return null;
            }
          },
        },
      });

      ACTIVE_RUNS.set(agentId, { runId: run.id, runtime, controller });
      // Plan 4 Task 13: track this controller's work promise so the
      // turn-busy heartbeat fires while it's unresolved.
      __markControllerStart(controller.promise);
      let turnBusyCleared = false;
      const clearTurnBusy = async () => {
        if (turnBusyCleared) return;
        turnBusyCleared = true;
        await reportTurnBusy(agentId, state, {
          busy: false,
          runId: run.id,
          runtime,
        }).catch(() => {});
      };

      // Audit 2026-06-28: when >1 run is claimed into a batch, only run[0] is executed — the
      // extras' bodies are merged into run[0]'s prompt (above) but the extra dispatch_runs were
      // left at `claimed`. That stranded them: false-busy "activeRun" for ~5min, then a spurious
      // [FAILED] handoff mirror to their senders (for content that WAS delivered), plus unclosed
      // reply contracts. Finalize each extra as `completed` (its text reached the agent in the
      // merged turn; the response lives in run[0]). Mirrors claude-channel.js, which already
      // marks every run in its batch delivered. Best-effort; the server reconciler is the backstop.
      let batchExtrasFinalized = false;
      const finalizeBatchedExtras = async () => {
        if (batchExtrasFinalized || batchedRuns.length <= 1) return;
        batchExtrasFinalized = true;
        for (const extra of batchedRuns.slice(1)) {
          try {
            await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(extra.id)}`, {
              status: "completed",
              agentStatus: "idle",
              summary: `Delivered in a merged batch turn with run ${run.id} (response is on that run).`,
              appendEvent: `Batch-merged into run ${run.id}; delivered in the same turn.`,
              eventType: "completed",
            });
          } catch { /* best-effort; server reconciler backstops */ }
        }
      };

      controller.promise
        .then(async (result) => {
          const summary = result.summary || "";
          const terminalStatus = result.status === "cancelled" ? "cancelled" : "completed";
          await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
            status: terminalStatus,
            summary,
            agentStatus: "idle",
            appendEvent:
              result.status === "cancelled"
                ? "Run cancelled."
                : "Run completed successfully.",
            eventType: terminalStatus,
          });
          await clearTurnBusy();
          await finalizeBatchedExtras();
          await ensureRequiredReplyHandoff(agentId, run, terminalStatus, summary);
          if (result.runtimeState) {
            state.info.runtimeState = { ...(state.info.runtimeState || {}), ...result.runtimeState };
            await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
              runtimeState: state.info.runtimeState,
            });
          }
        })
        .catch(async (error) => {
          const message = error?.message || String(error);
          // Retry the failure-PATCH up to 3 times with exponential
          // backoff. Without this, a transient connection blip during
          // the FAILURE path leaves the dispatch_run stuck `running`
          // — operator-reported "hermes stuck working" symptom. The
          // server's stale-run reconciler eventually catches it, but
          // its window is 5+ minutes (5 for managed). Retrying here
          // closes the gap for the common case.
          let lastErr = null;
          for (let attempt = 0; attempt < 3; attempt++) {
            try {
              await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(run.id)}`, {
                status: "failed",
                error: message,
                agentStatus: "idle",
                appendEvent: message,
                eventType: "failed",
              });
              await clearTurnBusy();
              await finalizeBatchedExtras();
              await ensureRequiredReplyHandoff(agentId, run, "failed", message);
              return;
            } catch (inner) {
              lastErr = inner;
              if (attempt < 2) {
                await new Promise((r) => setTimeout(r, 500 * Math.pow(2, attempt)));
              }
            }
          }
          console.error(
            `[aify] failed to report dispatch failure for ${run.id} after 3 retries; server reconciler will catch it within active_managed_run_stale_minutes:`,
            lastErr?.message || lastErr,
          );
        })
        .finally(async () => {
          await clearTurnBusy();
          ACTIVE_RUNS.delete(agentId);
        });
    }
  } finally {
    dispatchLoopBusy = false;
  }
}

async function processRunControls(agentId, activeRun) {
  if (!activeRun?.runId || !activeRun?.controller) return;
  const claim = await httpCall("POST", "/dispatch/controls/claim", {
    agentId,
    runId: activeRun.runId,
    machineId: MACHINE_ID,
  });
  const controls = claim.controls || [];
  const steerControls = controls.filter((control) => control.action === "steer");
  const otherControls = controls.filter((control) => control.action !== "steer");
  for (const control of otherControls) {
    try {
      if (control.action === "interrupt") {
        if (!activeRun.controller.capabilities?.interrupt || !activeRun.controller.interrupt) {
          throw new Error("Interrupt is not supported by this runtime");
        }
        await activeRun.controller.interrupt();
      } else if (control.action === "steer") {
        if (!activeRun.controller.capabilities?.steer || !activeRun.controller.steer) {
          throw new Error("Steer is not supported by this runtime");
        }
        await activeRun.controller.steer(control.body || "");
      } else {
        throw new Error(`Unknown control action "${control.action}"`);
      }

      await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
        status: "completed",
        response: `${control.action} accepted`,
      });
    } catch (error) {
      await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
        status: "failed",
        response: error?.message || String(error),
      });
    }
  }
  if (steerControls.length) {
    try {
      if (!activeRun.controller.capabilities?.steer || !activeRun.controller.steer) {
        throw new Error("Steer is not supported by this runtime");
      }
      const body = steerControls.length === 1
        ? steerControls[0].body || ""
        : [
            "[AIFY STEER BATCH]",
            `${steerControls.length} messages arrived while this run was active. Apply them to the current turn in order.`,
            "",
            ...steerControls.map((control, index) => [
              `--- Steer ${index + 1} of ${steerControls.length} ---`,
              control.body || "",
            ].join("\n")),
            "[/AIFY STEER BATCH]",
          ].join("\n\n");
      await activeRun.controller.steer(body);
      for (const control of steerControls) {
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "completed",
          response: steerControls.length === 1 ? "steer accepted" : `batched steer accepted (${steerControls.length})`,
        });
      }
    } catch (error) {
      for (const control of steerControls) {
        await httpCall("PATCH", `/dispatch/controls/${encodeURIComponent(control.id)}`, {
          status: "failed",
          response: error?.message || String(error),
        });
      }
    }
  }
}

// ── MCP Server ───────────────────────────────────────────────────────────────

const server = new McpServer({
  name: "aify-comms-mcp",
  version: AIFY_VERSION,
});

// ═══════════════════════════════════════════════════════════════════════════════
// 1. comms_register -- Register agent with ID, role, name, cwd, model, instructions
// ═══════════════════════════════════════════════════════════════════════════════

server.tool(
  "comms_register",
  "Register this agent instance. " +
    "Register this exact live session so other agents can message and, when supported, trigger this specific session. " +
    "New persistent agents should be created with comms_spawn or the dashboard Environments page.",
  {
    agentId: z.string().describe("Unique ID (e.g. 'coder-1', 'tester')"),
    role: z.string().describe("Role: 'coder', 'tester', 'reviewer', 'architect', etc."),
    name: z.string().optional().describe("Friendly name"),
    cwd: z.string().optional().describe("Working directory (used when triggered)"),
    model: z.string().optional().describe("Preferred model (e.g. 'sonnet', 'opus', 'haiku')"),
    description: z.string().optional().describe("Team-facing short description: who you are, what project you're on, what you focus on. Visible to other agents in comms_agents. Preserved across re-register; pass \"\" to clear."),
    instructions: z.string().optional().describe("Standing instructions for when triggered"),
    runtime: z.string().optional().describe("Runtime type (e.g. 'claude-code', 'codex', 'hermes', 'opencode', 'pi')"),
    machineId: z.string().optional().describe("Stable machine identifier (auto-detected by default)"),
    launchMode: z.string().optional().describe("Launch mode hint (default: detached)"),
    sessionMode: z.enum(["resident", "managed"]).optional().describe("Session type (default: resident)"),
    sessionHandle: z.string().optional().describe("Runtime-specific live session handle if known"),
    appServerUrl: z.string().optional().describe("Runtime-specific live app-server URL if known (Codex live sessions)"),
    managedBy: z.string().optional().describe("Owning agent ID for environment-managed sessions"),
  },
  async (args) => {
    args = fillSessionHandleFromAdapter(args, __runtimeAdapter);
    const { agentId, role, name, cwd, model, description, instructions, runtime, machineId, launchMode, sessionMode, sessionHandle, appServerUrl, managedBy } = args;
    try { validateName(agentId, "agent ID"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
    if (IS_MANAGED_DISPATCH) {
      // Allow EXPLICIT resident takeover. Operator-verified 2026-05-22:
      // a managed dashboard agent that's stopped/stale (wake disabled)
      // needs a way to be picked up by a fresh CLI session — without
      // this exit, the operator's only path was to delete + re-register
      // from a different shell, which fights the env-var lifecycle.
      // The guard's original purpose was to prevent ACCIDENTAL conversion
      // from a managed-dispatch turn's tool-call. An explicit
      // `sessionMode: "resident"` is an intentional act and should
      // succeed. Other sessionMode values (managed, omitted, etc.)
      // still hit the guard so a tool-call slip can't reclassify.
      if (normalizeSessionMode(sessionMode) !== "resident") {
        return {
          content: [{
            type: "text",
            text:
              "This is a dashboard-managed run. The agent identity is already registered by the environment bridge, " +
              "so comms_register without an explicit sessionMode is disabled here to avoid converting the managed agent into a resident CLI identity. " +
              "To take this identity over as a resident CLI session (e.g., the managed worker is stopped and you want to claim it from here), " +
              "call comms_register with sessionMode=\"resident\" explicitly. " +
              "Otherwise, do NOT call comms_register from this managed run — reply to the current aify-comms message with " +
              "comms_send(type=\"response\", inReplyTo=<the message id>) when a reply is owed (final plain text is only your working output, not the delivered reply).",
          }],
          isError: true,
        };
      }
    }
    const resolvedRuntime = detectRuntime(runtime);
    const resolvedMachineId = machineId || MACHINE_ID;
    const resolvedSessionMode = normalizeSessionMode(sessionMode);
    const previousInfo = REMOTE_AGENT_STATE.get(agentId)?.info;
    const resolvedCwd = normalizeRegistrationCwd(resolvedRuntime, cwd || DEFAULT_CWD);
    let runtimeConfig = resolvedRuntimeConfigForRegistration(resolvedRuntime, previousInfo, resolvedCwd);
    const hermesGatewayRegistration =
      resolvedRuntime === "hermes" &&
      /^wss?:\/\//i.test(String(runtimeConfig?.gatewayUrl || ""));
    const allowPreviousSessionHandle =
      !(hermesGatewayRegistration && !String(sessionHandle || "").trim());
    const initialSessionHandle =
      sessionHandle ||
      defaultSessionHandleForRuntime(resolvedRuntime) ||
      (allowPreviousSessionHandle ? previousInfo?.sessionHandle : "") ||
      "";
    const explicitAppServerUrl = String(appServerUrl || "").trim();
    if (resolvedRuntime === "codex" && explicitAppServerUrl) {
      runtimeConfig = { ...runtimeConfig, appServerUrl: explicitAppServerUrl };
    }
    let codexLiveBinding = null;
    if (resolvedRuntime === "codex" && !hasCodexLiveAppServer(runtimeConfig)) {
      codexLiveBinding = await discoverCodexLiveBinding({
        sessionHandle: initialSessionHandle,
        cwd: resolvedCwd,
      });
      if (codexLiveBinding?.runtimeConfig) {
        runtimeConfig = { ...runtimeConfig, ...codexLiveBinding.runtimeConfig };
      }
    }
    const discoveredCodexThreadId =
      resolvedRuntime === "codex" && hasCodexLiveAppServer(runtimeConfig)
        ? (codexLiveBinding?.threadId || await discoverCodexLiveThreadId(runtimeConfig, resolvedCwd))
        : "";
    const resolvedSessionHandle =
      sessionHandle ||
      discoveredCodexThreadId ||
      initialSessionHandle ||
      (allowPreviousSessionHandle ? previousInfo?.sessionHandle : "") ||
      "";
    // Native-session-id model (2026-06-03): comms_register binds this agent's
    // identity to its REAL hermes session id by persisting the per-agent marker,
    // so a relaunch resumes the SAME session and the delivery loop targets it.
    // Best-effort; never throws. (Gateway-url resolution is unchanged.)
    const resolvedAgentId = String(args?.agentId || agentId || "").trim();
    if (resolvedRuntime === "hermes" && resolvedAgentId && resolvedSessionHandle) {
      try { writeSessionIdMarker(resolvedAgentId, resolvedSessionHandle); } catch { /* best-effort */ }
    }
    const capabilities = defaultCapabilitiesForRuntime(resolvedRuntime, resolvedSessionMode, resolvedSessionHandle, runtimeConfig);

    const agentData = {
      agentId,
      role,
      name,
      cwd: resolvedCwd,
      model: model || "",
      description: description === undefined ? null : description,
      instructions: instructions || "",
      runtime: resolvedRuntime,
      machineId: resolvedMachineId,
      launchMode: launchMode || "detached",
      sessionMode: resolvedSessionMode,
      sessionHandle: resolvedSessionHandle,
      managedBy: managedBy || "",
      bridgeId: BRIDGE_INSTANCE_ID,
      capabilities,
      runtimeConfig,
      restoreDeleted: true,
      // Tombstone-resurrection guard (2026-06-03): carry this bridge's launch
      // time so the service can distinguish a genuine fresh relaunch from a
      // lingering bridge re-registering a deliberately-removed agent.
      bridgeStartedAt: BRIDGE_STARTED_AT,
    };

    // Write agent ID to a session-specific temp file keyed by PID so the
    // channel bridge and notification hook can find it. Only resident
    // sessions represent the current UI/CLI session.
    //
    // Previously we also wrote to {cwd}/.aify-agent, but that file is
    // shared across all sessions in the same directory — when two agents
    // (e.g. manager + tester) run in the same folder, the last to
    // register wins and the other agent's channel bridge picks up the
    // wrong agentId, causing cross-talk.
    if (resolvedSessionMode === "resident") {
      try {
        writeAgentBindingFile({ pid: process.ppid || process.pid, agentId, bridgeId: BRIDGE_INSTANCE_ID });
      } catch { /* best effort */ }
    }

    // REGISTERING TURNS STATUS ON (2026-07-14). If the wrapper never exported
    // AIFY_AGENT_ID (session launched without `--aify-agent`), the turn detector could not
    // arm at boot and this bridge had NO way to ever report turn state — the agent would
    // register, message and heartbeat perfectly while its status latched forever. But THIS
    // call is the bridge learning who it is, so use it: claim the session id the hook
    // captured before we had an identity, then arm the detector. Registering now does what
    // an operator always assumed it did.
    if (!isClaudeTurnDetectorArmed() && agentId) {
      const claimed = claimCapturedClaudeSession(agentId);
      if (armClaudeTurnEndDetector(agentId)) {
        console.error(
          `[aify] turn detection armed late for '${agentId}' via comms_register ` +
          `(session started without --aify-agent${claimed ? "; session id claimed from the pid capture" : ""}). ` +
          `Status will work from now on; relaunch with --aify-agent to arm it at boot.`,
        );
      }
    }

    if (IS_REMOTE) {
      const r = await httpCall("POST", "/agents", agentData);
      let runtimeState = {};
      try {
        const agentInfo = await httpCall("GET", `/agents/${encodeURIComponent(agentId)}`);
        runtimeState = agentInfo.agent?.runtimeState || {};
      } catch {
        // best effort
      }
      runtimeState = { ...runtimeState, bridgeInstanceId: BRIDGE_INSTANCE_ID };
      try {
        await httpCall("PATCH", `/agents/${encodeURIComponent(agentId)}/runtime-state`, {
          runtimeState,
        });
      } catch {
        // best effort
      }
      REMOTE_AGENT_STATE.set(agentId, {
        info: {
          ...agentData,
          runtimeState,
        },
      });
      const active = ACTIVE_RUNS.get(agentId);
      if (active) {
        await reconcileLocalActiveRun(agentId, REMOTE_AGENT_STATE.get(agentId), active);
      }
      try {
        const agentsRes = await httpCall("GET", "/agents");
        for (const [managedId, managedInfo] of Object.entries(agentsRes.agents || {})) {
          if (normalizeSessionMode(managedInfo.sessionMode) !== "managed") continue;
          if ((managedInfo.managedBy || "") !== agentId) continue;
          if ((managedInfo.machineId || "") !== resolvedMachineId) continue;
          const managedRuntimeState = { ...(managedInfo.runtimeState || {}), bridgeInstanceId: BRIDGE_INSTANCE_ID };
          try {
            await httpCall("PATCH", `/agents/${encodeURIComponent(managedId)}/runtime-state`, {
              runtimeState: managedRuntimeState,
            });
          } catch {
            // best effort
          }
          REMOTE_AGENT_STATE.set(managedId, {
            info: {
              agentId: managedId,
              role: managedInfo.role,
              name: managedInfo.name,
              cwd: managedInfo.cwd || DEFAULT_CWD,
              model: managedInfo.model || "",
              instructions: managedInfo.instructions || "",
              runtime: managedInfo.runtime || "generic",
              machineId: managedInfo.machineId || resolvedMachineId,
              launchMode: managedInfo.launchMode || "managed",
              sessionMode: managedInfo.sessionMode || "managed",
              sessionHandle: managedInfo.sessionHandle || "",
              managedBy: managedInfo.managedBy || agentId,
              capabilities: managedInfo.capabilities || [],
              runtimeConfig: managedInfo.runtimeConfig || {},
              runtimeState: managedRuntimeState,
            },
          });
          const active = ACTIVE_RUNS.get(managedId);
          if (active) {
            await reconcileLocalActiveRun(managedId, REMOTE_AGENT_STATE.get(managedId), active);
          }
        }
      } catch {
        // best effort
      }
      ensureDispatchLoop();
      return {
        content: [{
          type: "text",
          text:
            `Registered "${r.agentId}" (${resolvedSessionMode}, role: ${r.role}, runtime: ${resolvedRuntime}, machine: ${resolvedMachineId}).` +
            (resolvedSessionHandle ? ` Session: ${resolvedSessionHandle}` : "") +
            // A resident that registered from a session with no AIFY_AGENT_ID is registered but
            // structurally unable to report turns — say so HERE, the one moment the agent is
            // listening. See register-identity.js for why it cannot be fixed after launch.
            residentIdentityWarning({
              registeredAgentId: r.agentId,
              envAgentId: process.env.AIFY_AGENT_ID,
              sessionMode: resolvedSessionMode,
              runtime: resolvedRuntime,
            }) +
            (
              resolvedRuntime === "codex" &&
              hasCodexLiveAppServer(runtimeConfig) &&
              !resolvedSessionHandle
                ? ` Live Codex app-server detected, but no thread was auto-bound. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID") from that same codex-aify session.`
                : (
                  resolvedRuntime === "codex" &&
                  codexLiveBinding?.ambiguous
                    ? ` Multiple live codex-aify sessions matched this registration, so aify could not safely auto-bind one. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL") from that same live session.`
                    : ""
                )
            ),
        }],
      };
    }

    const registry = readAgents();
    registry.agents[agentId] = {
      role,
      name: name || agentId,
      cwd: resolvedCwd,
      model: model || "",
      instructions: instructions || "",
      runtime: resolvedRuntime,
      machineId: resolvedMachineId,
      launchMode: launchMode || "detached",
      sessionMode: resolvedSessionMode,
      sessionHandle: resolvedSessionHandle,
      managedBy: managedBy || "",
      capabilities,
      runtimeConfig,
      runtimeState: registry.agents[agentId]?.runtimeState || {},
      registeredAt: new Date().toISOString(),
      lastSeen: new Date().toISOString(),
    };
    writeAgents(registry);
    fs.mkdirSync(path.join(INBOX_DIR, agentId), { recursive: true });
    return {
      content: [{
        type: "text",
        text:
          `Registered "${agentId}" (${resolvedSessionMode}, role: ${role}, cwd: ${resolvedCwd}, runtime: ${resolvedRuntime}).` +
          (resolvedSessionHandle ? ` Session: ${resolvedSessionHandle}` : "") +
          (
            resolvedRuntime === "codex" &&
            hasCodexLiveAppServer(runtimeConfig) &&
            !resolvedSessionHandle
              ? ` Live Codex app-server detected, but no thread was auto-bound. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID") from that same codex-aify session.`
              : (
                resolvedRuntime === "codex" &&
                codexLiveBinding?.ambiguous
                  ? ` Multiple live codex-aify sessions matched this registration, so aify could not safely auto-bind one. Re-run comms_register(..., runtime="codex", sessionHandle="$CODEX_THREAD_ID", appServerUrl="$AIFY_CODEX_APP_SERVER_URL") from that same live session.`
                  : ""
              )
          ),
      }],
    };
  }
);

registerEnvironmentTools(server, z);






registerUsageTool(server, z);


registerCompactTool(server, z);

registerAgentReportingTools(server, z);

registerSelfRecordTools(server, z);

// ═══════════════════════════════════════════════════════════════════════════════
// 3. comms_send -- Send message to agent by ID or role
// ═══════════════════════════════════════════════════════════════════════════════

export const COMMS_SEND_TOOL_DESCRIPTION =
  "Send a message to an agent by ID, or to all agents with a given role. " +
  "This is live-delivery gated: if the target is offline, stopped, or lacks a live wake path, the message is not written. A MANAGED agent resting at `available` (no live worker yet — including a hermes whose gateway died) IS deliverable: the send cold-starts/wakes it. `available` and `blocked` are both deliverable. If the target is busy and steer-capable, ordinary sends steer into the active run between tool calls. If the target is busy but cannot steer, ordinary sends queue or merge as next-turn work. Use queueIfBusy=true only when the message should run after the active turn even when steer is available; when queueIfBusy=true, the steer option is ignored. Agent-reported blocked/completed states are status notes, not delivery blockers. " +
  "The special target dashboard stores a message for the human/operator without trying to start a runtime. " +
  "Resident sessions trigger only when that exact runtime/session handle supports resident execution; environment-managed sessions remain the persistent fallback. " +
  "Agents should answer messages that owe a reply with a comms_send tool call: use comms_send(type=\"response\", inReplyTo=<the message id>) in BOTH resident/live CLI sessions AND dashboard-managed delivered runs. Requests, reviews, errors, dashboard asks, and explicit reply contracts normally owe replies. A completion response, approval, info, or acknowledgement with no new question/work is read context: do not send a courtesy acknowledgement. That tool call is the team/chat-visible reply and closes the run; your final plain text / stdout is your own working output, not the delivered reply. (Safety net: if managed_reply_capture_fallback is enabled, a delivered run that ends without an explicit reply has its summary auto-mirrored back; do not rely on it for messages that owe replies.) Genuinely-direct terminal input you type yourself is answered with direct output, not comms_send. " +
  "Reply tracking: omit requireReply for normal type-based behavior (`request`, `review`, and `error` owe replies; `info`, `response`, and `approval` do not). Set requireReply=true only when a normally optional message needs a tracked response. Set requireReply=false only for an intentionally fire-and-forget request/review/error whose body asks no question or action. requireReply changes the reply contract, not whether the target is woken. " +
  "Keep messages scoped to one topic, state what you checked when truth matters, ask one clear question when blocked, and avoid reviving unrelated older context.";

server.tool(
  "comms_send",
  COMMS_SEND_TOOL_DESCRIPTION,
  {
    from: z.string().describe("Your agent ID"),
    to: z.string().optional().describe("Target agent ID"),
    toRole: z.string().optional().describe("Send to all agents with this role"),
    type: z
      .enum(["request", "response", "info", "error", "review", "approval"])
      .describe("Message type"),
    subject: z.string().describe("Short subject"),
    body: z.string().describe("Message content"),
    priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
    inReplyTo: z.string().optional().describe("Message ID this replies to"),
    steer: z.boolean().optional().describe("When true and target is busy, deliver between tool calls when supported; otherwise queue/merge as next-turn work. Defaults to true. Ignored when queueIfBusy=true."),
    queueIfBusy: z.boolean().optional().describe("When true, force next-turn queue/merge behind the target's active/queued work instead of steering the active turn."),
    requireReply: z.boolean().optional().describe("Reply-contract override. Omit for type defaults (request/review/error=true; info/response/approval=false). Set true only to track a response to a normally optional message; set false only for intentional fire-and-forget."),
  },
  async ({ from, to, toRole, type, subject, body, priority, inReplyTo, steer, queueIfBusy, requireReply }) => {
    if (!to && !toRole) {
      return { content: [{ type: "text", text: "Error: need 'to' or 'toRole'" }], isError: true };
    }
    const shouldTrigger = true;
    const forceQueue = queueIfBusy === true;

    // -- Remote mode --
    if (IS_REMOTE) {
      // Stable idempotency key (#240): minted once per logical send so httpCall can retry
      // the POST safely on a transient socket error (the server collapses the retry to the
      // original message) instead of dropping it. One nonce per tool call — a real second
      // send is a new tool call with a fresh nonce.
      const clientNonce = randomUUID();
      const r = await httpCall("POST", "/messages/send", {
        from_agent: from, to, toRole, type, subject, body, priority: priority || "normal", inReplyTo, trigger: shouldTrigger, steer: forceQueue ? false : (steer ?? true), queueIfBusy: forceQueue, requireReply, clientNonce,
      });
      if (!r.ok) {
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}${x.recipientStatus ? ` (${x.recipientStatus})` : ""}`);
        return {
          content: [{
            type: "text",
            text: `${r.error || "Message was not sent."}${skipped.length ? `\nUnavailable: ${skipped.join("; ")}` : ""}`,
          }],
          isError: true,
        };
      }

      const dashboardOnly = (r.recipients || []).length > 0 && (r.recipients || []).every((rid) => rid === "dashboard");
      if (shouldTrigger && r.recipients?.length > 0 && !dashboardOnly) {
        const queued = (r.dispatchRuns || []).map((x) => formatQueuedRun(x));
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}`);
        return {
          content: [{
            type: "text",
            text:
              `Sent. Live handling: ${queued.join(", ") || "started"}. Use comms_run_status(...) only when you need operational progress details. Requests, reviews, and errors should receive an explicit reply.` +
              (skipped.length ? `\nNot started: ${skipped.join("; ")}` : ""),
          }],
        };
      }

      // Include recipient status in response
      const statusParts = (r.recipients || []).map(rid => {
        const info = r.recipientStatus?.[rid];
        if (info) return `${rid} [${info.status}, ${info.unread} unread]`;
        return rid;
      });
      return {
        content: [{ type: "text", text: `Sent (${r.messageId}) to ${statusParts.join(", ")}. Subject: ${subject}` }],
      };
    }

    // -- Local mode --
    const registry = readAgents();
    if (registry.agents[from]) {
      registry.agents[from].lastSeen = new Date().toISOString();
      writeAgents(registry);
    }

    const messageId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
    const message = { id: messageId, from, type, subject, body, priority: priority || "normal", inReplyTo };

    const recipients = [];
    if (to) recipients.push(to);
    if (toRole) {
      for (const [id, info] of Object.entries(registry.agents)) {
        if (info.role === toRole && id !== from) recipients.push(id);
      }
    }
    const uniqueRecipients = dedupePreserveOrder(recipients);
    if (!uniqueRecipients.length) {
      return { content: [{ type: "text", text: "No recipients found. Target may not be registered." }] };
    }

    for (const r of uniqueRecipients) deliverMessage(r, message);

    if (shouldTrigger && uniqueRecipients.length > 0) {
      const started = [];
      const skipped = [];
      for (const targetId of uniqueRecipients) {
        const targetInfo = registry.agents[targetId] || {};
        const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
        const runtime = normalizeRuntime(targetInfo.runtime || "generic");
        const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
        const residentRunnable = sessionMode === "resident" && capabilities.includes("resident-run") && targetInfo.sessionHandle;
        const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
        if (!residentRunnable && !managedRunnable) {
          skipped.push(
            sessionMode === "resident"
              ? `${targetId} (resident session has no triggerable session handle; re-register this live session)`
              : `${targetId} (managed session is missing launch capabilities)`,
          );
          continue;
        }
        if (!canLaunchRuntime(runtime)) {
          skipped.push(`${targetId} (${runtime})`);
          continue;
        }
        spawnTriggeredAgent({ targetId, targetInfo, from, type, subject, body });
        started.push(`${targetId} (${runtime})`);
      }
      return {
        content: [{
          type: "text",
          text:
            `Sent + triggered locally for ${started.join(", ") || "no launchable recipients"}. Reply handoff tracking is only available in remote server mode.` +
            (skipped.length ? `\nSkipped: ${skipped.join(", ")}` : ""),
        }],
      };
    }

    return {
      content: [{ type: "text", text: `Sent (${messageId}) to ${uniqueRecipients.join(", ")}. Subject: ${subject}` }],
    };
  }
);

registerDispatchTools(server, z);

// ═══════════════════════════════════════════════════════════════════════════════
// comms_console_tail / comms_console_input -- read & unstick a managed agent's console
// ═══════════════════════════════════════════════════════════════════════════════

registerConsoleTools(server, z);

// comms_run_steer removed from stdio — ordinary comms_send does not require
// knowing the runId, creates an inbox message, and steers busy steer-capable
// targets unless queueIfBusy=true. Busy non-steer targets queue/merge instead.

/**
 * Spawn a local runtime instance to handle a triggered message.
 * Fire-and-forget: the result is delivered back to the sender's inbox.
 */
function spawnTriggeredAgent({ targetId, targetInfo, from, type, subject, body }) {
  const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
  const runtime = normalizeRuntime(targetInfo.runtime || "generic");
  const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
  const residentRunnable =
    sessionMode === "resident" &&
    runtime === "codex" &&
    capabilities.includes("resident-run") &&
    targetInfo.sessionHandle;
  const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
  if (!residentRunnable && !managedRunnable) {
    const reason =
      sessionMode === "resident"
        ? `Agent "${targetId}" is a resident session without a triggerable session handle. Re-register that live session first.`
        : `Agent "${targetId}" is not configured as a launchable managed session.`;
    deliverMessage(from, {
      id: `${Date.now()}-${randomUUID().slice(0, 8)}`,
      from: targetId,
      type: "error",
      subject: `[FAILED] ${subject}`,
      body: reason,
    });
    return;
  }
  if (!canLaunchRuntime(runtime)) {
    deliverMessage(from, {
      id: `${Date.now()}-${randomUUID().slice(0, 8)}`,
      from: targetId,
      type: "error",
      subject: `[FAILED] ${subject}`,
      body: `Runtime "${runtime}" does not support active dispatch`,
    });
    return;
  }

  const run = {
    id: `local-${Date.now()}-${randomUUID().slice(0, 8)}`,
    from,
    targetAgentId: targetId,
    type,
    subject,
    body,
    mode: "require_start",
    executionMode: residentRunnable ? "resident" : "managed",
  };
  const baseState = parseJson(targetInfo.runtimeState, {});
  const runtimeState = { ...baseState, ...(LOCAL_RUNTIME_STATE.get(targetId) || {}) };

  const controller = launchRuntimeRun({
    agentId: targetId,
    agentInfo: { ...targetInfo, runtime },
    run,
    runtimeState,
    callbacks: {
      // Plan 4 Task 13: same ready surface as the main dispatch loop.
      onReady: () => {
        httpCall("PATCH", `/agents/${encodeURIComponent(targetId)}/ready`, {
          ready: true,
          requestedBy: "controller-handshake",
        }).catch(() => { /* best-effort */ });
      },
      onRuntimeState: (nextState) => {
        const merged = { ...(LOCAL_RUNTIME_STATE.get(targetId) || {}), ...nextState };
        LOCAL_RUNTIME_STATE.set(targetId, merged);
        const registry = readAgents();
        if (registry.agents[targetId]) {
          registry.agents[targetId].runtimeState = merged;
          writeAgents(registry);
        }
      },
      onEvent: () => {},
      onRefs: () => {},
    },
  });
  // Plan 4 Task 13: track this controller's work promise so the turn-busy
  // heartbeat fires while it's unresolved.
  __markControllerStart(controller.promise);

  controller.promise
    .then(() => {})
    .catch((err) => {
      console.error("[aify] local triggered run failed:", err);
    });
}

registerInboxTools(server, z);

registerSearchTool(server, z);




// ═══════════════════════════════════════════════════════════════════════════════
// 6. comms_share -- Share text content or file to shared space
// ═══════════════════════════════════════════════════════════════════════════════

registerArtifactTools(server, z);

registerChannelTools(server, z);


// ═══════════════════════════════════════════════════════════════════════════════
// 11. comms_channel_send -- Send message to channel
// ═══════════════════════════════════════════════════════════════════════════════

server.tool(
  "comms_channel_send",
  "Send a message to a channel. This is live-delivery gated for channel members: if any recipient is offline, stale, stopped, or lacks a live wake path, the channel message is not written. Busy steer-capable members receive the channel update as steer into their active run; busy non-steer members queue or merge as next-turn work. Use queueIfBusy=true only to force next-turn delivery; when queueIfBusy=true, the steer option is ignored. Agent-reported blocked/completed states are status notes, not delivery blockers.",
  {
    channel: z.string().describe("Channel name"),
    from: z.string().describe("Your agent ID"),
    body: z.string().describe("Message content"),
    type: z
      .enum(["info", "request", "response", "error", "review", "approval"])
      .optional()
      .describe("Message type (default: info)"),
    priority: z.enum(["normal", "high", "urgent"]).optional().describe("Message priority (default: normal)"),
    steer: z.boolean().optional().describe("When true and members are busy, deliver between tool calls when supported; otherwise queue/merge as next-turn work. Defaults to true. Ignored when queueIfBusy=true."),
    queueIfBusy: z.boolean().optional().describe("When true, force this channel update behind active/queued work instead of steering active turns."),
  },
  async ({ channel, from, body, type, priority, steer, queueIfBusy }) => {
    try { validateName(channel, "channel name"); } catch (e) { return { content: [{ type: "text", text: e.message }], isError: true }; }
    const shouldTrigger = true;
    const forceQueue = queueIfBusy === true;
    const subject = `#${channel}: ${body.slice(0, 80)}`;

    if (IS_REMOTE) {
      const r = await httpCall("POST", `/channels/${encodeURIComponent(channel)}/send`, {
        from_agent: from, channel, body, type: type || "info", priority: priority || "normal", trigger: shouldTrigger, steer: forceQueue ? false : (steer ?? true), queueIfBusy: forceQueue,
      });
      if (!r.ok) {
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}${x.recipientStatus ? ` (${x.recipientStatus})` : ""}`);
        return {
          content: [{
            type: "text",
            text: `${r.error || `Channel message to #${channel} was not sent.`}${skipped.length ? `\nUnavailable: ${skipped.join("; ")}` : ""}`,
          }],
          isError: true,
        };
      }
      if (shouldTrigger && (r.dispatchRuns?.length || r.notStarted?.length)) {
        const queued = (r.dispatchRuns || []).map((x) => formatQueuedRun(x));
        const skipped = (r.notStarted || []).map((x) => `${x.targetAgentId}: ${x.reason}`);
        return {
          content: [{
            type: "text",
            text:
              `Sent to #${channel}. Live handling: ${queued.join(", ") || "started"}. Use comms_run_status(...) to inspect progress.` +
              (skipped.length ? `\nNot started: ${skipped.join("; ")}` : ""),
          }],
        };
      }
      return { content: [{ type: "text", text: `Sent to #${channel} (${r.members.length} members).` }] };
    }

    const chFile = path.join(MESSAGES_DIR, "channels", `${channel}.json`);
    if (!fs.existsSync(chFile)) {
      return { content: [{ type: "text", text: `Channel #${channel} not found.` }], isError: true };
    }
    const ch = JSON.parse(fs.readFileSync(chFile, "utf-8"));
    if (!ch.members.includes(from)) {
      return { content: [{ type: "text", text: `Not a member of #${channel}. Join first.` }], isError: true };
    }
    const msgId = `${Date.now()}-${randomUUID().slice(0, 8)}`;
    ch.messages.push({
      id: msgId, from, type: type || "info", body, timestamp: Date.now(),
    });
    fs.writeFileSync(chFile, JSON.stringify(ch, null, 2));
    // Deliver to each member's inbox (except sender) so notifications work
    const recipients = [];
    for (const member of ch.members) {
      if (member !== from) {
        recipients.push(member);
        deliverMessage(member, {
          id: msgId, from, type: type || "info", source: "channel", channel, subject, body, priority: priority || "normal",
        });
      }
    }
    if (shouldTrigger && recipients.length > 0) {
      const started = [];
      const skipped = [];
      const registry = readAgents();
      for (const targetId of recipients) {
        const targetInfo = registry.agents[targetId] || {};
        const sessionMode = normalizeSessionMode(targetInfo.sessionMode);
        const runtime = normalizeRuntime(targetInfo.runtime || "generic");
        const capabilities = Array.isArray(targetInfo.capabilities) ? targetInfo.capabilities : [];
        const residentRunnable = sessionMode === "resident" && capabilities.includes("resident-run") && targetInfo.sessionHandle;
        const managedRunnable = sessionMode === "managed" && capabilities.includes("managed-run");
        if (!residentRunnable && !managedRunnable) {
          skipped.push(
            sessionMode === "resident"
              ? `${targetId} (resident session has no triggerable session handle; re-register that live session)`
              : `${targetId} (managed session is missing launch capabilities)`,
          );
          continue;
        }
        if (!canLaunchRuntime(runtime)) {
          skipped.push(`${targetId} (${runtime})`);
          continue;
        }
        spawnTriggeredAgent({ targetId, targetInfo, from, type: type || "info", subject, body });
        started.push(`${targetId} (${runtime})`);
      }
      return {
        content: [{
          type: "text",
          text:
            `Sent to #${channel} + triggered locally for ${started.join(", ") || "no launchable recipients"}.` +
            (skipped.length ? `\nSkipped: ${skipped.join(", ")}` : ""),
        }],
      };
    }
    return { content: [{ type: "text", text: `Sent to #${channel} (${ch.members.length} members).` }] };
  }
);



registerLifecycleTools(server, z);

registerDashboardTool(server, z);

// ── Entrypoint ───────────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("aify-comms-mcp v4.0.0 running on stdio");
  console.error(`Mode: ${IS_REMOTE ? "REMOTE (" + SERVER_URL + ")" : "LOCAL (" + MESSAGES_DIR + ")"}`);
  console.error(`Working dir: ${DEFAULT_CWD}`);
  // "Never leave a bridge child behind." For an MCP-CHILD bridge (loaded by a
  // claude/codex/hermes harness), poll the controlling harness — the parent pid
  // captured at startup. When it dies, shut down gracefully (same teardown as
  // SIGTERM) instead of lingering as an orphan the server has to reap. (Found 6
  // of these server.js children reparented to the WSL init relay for ~10h.) We
  // poll the ORIGINAL parent pid, so reparenting (ppid -> init/Relay after the
  // harness dies) doesn't hide the death. stdin-EOF would be cleaner, but the MCP
  // SDK transport reads stdin via 'data' only and never propagates EOF (verified).
  // EXCLUDED for the environment bridge: top-level process, its own lifecycle.
  if (!IS_ENVIRONMENT_BRIDGE && ORIGINAL_PARENT_PID > 1) {
    let parentMisses = 0;
    const harnessGuard = setInterval(() => {
      let alive = true;
      try { process.kill(ORIGINAL_PARENT_PID, 0); } catch (e) { alive = (e && e.code === "EPERM"); }
      if (alive) { parentMisses = 0; return; }
      if (++parentMisses < 2) return; // tolerate one transient miss (~3s)
      clearInterval(harnessGuard);
      try { console.error(`[aify] controlling harness pid=${ORIGINAL_PARENT_PID} gone; MCP-child bridge shutting down`); } catch { /* best effort */ }
      shutdownWithStatus(0); // idempotent via shutdownStarted; same teardown as SIGTERM
    }, 3000);
    if (typeof harnessGuard.unref === "function") harnessGuard.unref();
  }
  // Codex app-server waits for its MCP servers to finish initializing while
  // registration discovers the live thread through that same app-server.
  // Do not deadlock MCP startup on the discovery round-trip.
  autoRegisterConfiguredAgent().catch((err) => {
    console.error(`[aify] auto-registration failed: ${err?.message || err}`);
  });
}

// Plan 6 A2 (2026-05-26): only auto-run main() when this file is the
// process entrypoint. Tests that import named helpers (e.g.
// computeInitialSessionHandle) from this module otherwise hang because
// main() blocks on stdin via StdioServerTransport. The guard is safe —
// real bridge launches always invoke server.js directly via the wrapper
// shebang or `node mcp/stdio/server.js`.
const __isEntrypoint = (() => {
  try {
    return process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
  } catch { return true; }
})();
if (__isEntrypoint) main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
