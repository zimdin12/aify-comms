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
  API_KEY,
  HTTP_TIMEOUT_MS,
  IS_REMOTE,
  SERVER_URL,
  httpCall,
} from "./aify-service-endpoint.mjs";
import { makeAutoRegister } from "./auto-registration.mjs";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { reportResidentLost } from "./resident-lost.mjs";
import {
  makeResidentGatewayStatusReader,
  shouldArmResidentHermesTurnDetector,
} from "./resident-gateway-status.mjs";
import {
  environmentHeartbeatPayload,
} from "./environment-identity.mjs";
import {
  INBOX_DIR, MESSAGES_DIR, SHARED_DIR,
} from "./local-store.mjs";
import {
  ACTIVE_RUNS,
  interruptActiveRuns,
} from "./bridge-agent-state.mjs";
import { anyControllerActive } from "./controller-activity.mjs";
import { DEFAULT_CWD } from "./registration-inputs.mjs";
import { AIFY_HERMES_GATEWAY_URL } from "./hermes-gateway-config.mjs";
import { BRIDGE_INSTANCE_ID } from "./bridge-instance.mjs";
import { armClaudeTurnEndDetector, stopClaudeTurnEndDetector } from "./claude-turn-detector-state.mjs";
import { __runtimeAdapter } from "./runtime-adapter.mjs";
import { AIFY_AGENT_ID, IS_ENVIRONMENT_BRIDGE, cleanEnvPlaceholder } from "./launch-identity.mjs";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { removeAgentBindingFile } from "./binding-file.js";
import { writeRuntimeMarker, removeRuntimeMarker } from "./runtime-markers.js";
import {
  defaultMachineId,
  normalizeRuntime,
  extractRuntimeSessionHandleFromCommand,
  terminateProcessTree,
} from "./runtimes.js";

import { shutdownAllPiSessions } from "./pi-session-pool.mjs";
import { shutdownAllCodexSessions } from "./codex-session.js";
import { shutdownAllHermesSessions } from "./hermes-session.js";
import { shutdownAllHermesGatewaySessions } from "./hermes-managed-gateway-session.js";
import { bridgeTerminalSupported } from "./terminal-runtime.js";
import {
  bootstrapManagedEnvironmentBridge,
  localAgentNeedsDispatchHosting,
} from "./managed-teardown-ownership.js";
import { startSessionHandleHeartbeat, makeDefaultHandlePoster } from "./session-handle-heartbeat.js";
import { startTurnBusyHeartbeat, makeDefaultTurnBusyPoster } from "./turn-busy-heartbeat.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { startGatewayLivenessProbe } from "./hermes-gateway-liveness.js";
import { gatewayIndexUrlFromWs, makeGatewayReachabilityProbe, reportGatewayDead } from "./hermes-gateway.mjs";
import { startHermesGatewayTurnDetector } from "./hermes-gateway-turn-detector.js";
import { startClaudeTurnEndDetector } from "./claude-turn-end-detector.js";
import { collectOnce as collectUsageOnce, collectConsumptionOnce } from "./usage-collector.js";
import { AIFY_VERSION } from "./version.js";
import { createManagedOwnershipReader } from "./managed-ownership.mjs";
import {
  noteControlClaimFailure,
} from "./claim-failure-tracker.mjs";
import { createManagedTeardownSweeps } from "./managed-teardown-sweeps.mjs";
import { shouldSkipLoop } from "./loop-gate.mjs";
import { reportResidentRuntimeLost as reportResidentRuntimeLostImpl } from "./resident-runtime-lost.mjs";
import { registerAllTools } from "./register-tools.mjs";
import { syncManagedEnvironmentAgentsPass } from "./managed-environment-sync.mjs";
import { runDispatchPass } from "./dispatch-loop.mjs";
import { runTerminalControlPass } from "./terminal-control-loop.mjs";
import { runSpawnPass } from "./spawn-loop.mjs";
import { runEnvironmentControlPass } from "./environment-control-loop.mjs";
import {
  DISPATCH_POLL_MS,
  TERMINAL_CONTROL_POLL_MS,
  __HEARTBEAT_MS,
  __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE,
  __RESIDENT_GATEWAY_TURN_POLL_MS,
} from "./poll-intervals.mjs";
import { VIRTUAL_RPC_RUNTIMES, VIRTUAL_TERMINALS_BY_AGENT, VIRTUAL_TERMINAL_INPUT, createVirtualTerminalSink, ensureVirtualTerminal, findAgentIdForVirtualTerminal, handleVirtualTerminalControl, updateTerminalControl } from './virtual-terminals.mjs';
import { ensureRequiredReplyHandoff } from './required-reply-handoff.mjs';
import { TERMINAL_MANAGER, reportDeadOwnedTerminals } from './terminal-manager.mjs';
import { runBootTombstonedMarkerSweep } from './boot-marker-sweep.mjs';
import { residentRuntimeBindingLost } from './resident-binding-health.mjs';

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


// __HEARTBEAT_MS moved to ./poll-intervals.mjs in v0.5.4.
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
// __RESIDENT_GATEWAY_TURN_POLL_MS moved to ./poll-intervals.mjs in v0.5.4.
// __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE moved to ./poll-intervals.mjs in v0.5.4.
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
// confirmedManagedTeardownAgentIds moved to ./managed-teardown-sweeps.mjs in v0.5.4 — the two
// sweeps that write and read it are its only users, so they own it.

// interruptActiveRuns moved to ./bridge-agent-state.mjs in v0.5.4.

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
// LOCAL_RUNTIME_STATE moved to ./spawn-triggered-agent.mjs in v0.5.4 — all three of its uses
// are inside that function, so it owns the Map.
// agentId → { terminalId, runtime } for the bridge's synthesized RPC
// terminal. Cached so subsequent dispatches reuse the same virtual
// terminal_session row. Covers both managed pi (persistent omp --mode rpc
// child) and managed hermes (per-dispatch `hermes chat -q -Q` with a
// synthesized request/response feed).
// VIRTUAL_TERMINALS_BY_AGENT moved to ./virtual-terminals.mjs in v0.5.4.
// Dashboard input buffering for synthesized pi RPC terminals. See
// virtual-terminal-input.js for the buffer-and-dispatch semantics.
// VIRTUAL_TERMINAL_INPUT moved to ./virtual-terminals.mjs in v0.5.4.

// Bridge-side runtimes that own a synthesized virtual rpc
// terminal_session. Must stay aligned with the service-side
// VIRTUAL_RPC_COMMANDS_BY_RUNTIME in api_v2.py — when a new runtime
// is added there, add it here too so the bridge's terminal-control
// router routes synth-terminal controls (input/resize/stop) through
// handleVirtualTerminalControl instead of the legacy node-pty path
// (which marks the row stopped because no real PTY exists).
// VIRTUAL_RPC_RUNTIMES moved to ./virtual-terminals.mjs in v0.5.4.

// findAgentIdForVirtualTerminal moved to ./virtual-terminals.mjs in v0.5.4.
// DISPATCH_POLL_MS moved to ./poll-intervals.mjs in v0.5.4.
// Terminal-control loop polls separately and much tighter: console input is
// latency-sensitive (operator typing), and the terminal_controls query is
// small + indexed, so a sub-second cadence is perf-safe. Dispatch/spawn
// polling stays at the heavier DISPATCH_POLL_MS.
// TERMINAL_CONTROL_POLL_MS moved to ./poll-intervals.mjs in v0.5.4.
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
// spawnClaimFailureCount / spawnClaimLastLogAt moved to ./claim-failure-tracker.mjs in v0.5.4.
let remoteEffectiveCwdRoots = null;
const AUTO_REREGISTER_AFTER_FAILURES = 4;
// RESIDENT_BINDING_FAILURES moved to ./resident-binding-health.mjs in v0.5.4.
// RESIDENT_BINDING_LOST_AFTER_FAILURES moved to ./resident-binding-health.mjs in v0.5.4.
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
// TERMINAL_TURN_BUSY_REMIT_MS moved to ./terminal-manager.mjs in v0.5.4.
// TERMINAL_TURN_BUSY_QUIET_MS moved to ./terminal-manager.mjs in v0.5.4.
// TERMINAL_TURN_BUSY_TIMERS moved to ./terminal-manager.mjs in v0.5.4.
// pulseTerminalTurnBusy moved to ./terminal-manager.mjs in v0.5.4.


// CONSOLE_WORKING_REMIT_MS moved to ./terminal-manager.mjs in v0.5.4.
// How recently a console-working pulse must have fired for a subsequent "unknown" footer frame
// to count as mid-turn (and thus refresh the lease). Shorter than the server console-working
// lease so a genuinely ended turn still lets the lease lapse rather than self-extending forever.
// CONSOLE_WORKING_TURN_WINDOW_MS moved to ./terminal-manager.mjs in v0.5.4.
// CONSOLE_WORKING_TIMERS moved to ./terminal-manager.mjs in v0.5.4.

// Refresh the server-side console-working lease while the claude spinner footer is
// visible. Debounced to ~once / CONSOLE_WORKING_REMIT_MS so a per-second spinner redraw
// does not spam the endpoint. No clear timer: the lease self-expires server-side (TTL).
// pulseConsoleWorking moved to ./terminal-manager.mjs in v0.5.4.
// ensureVirtualTerminal moved to ./virtual-terminals.mjs in v0.5.4.

// dispatchVirtualTerminalLine moved to ./virtual-terminals.mjs in v0.5.4.

// createVirtualTerminalSink moved to ./virtual-terminals.mjs in v0.5.4.

// TERMINAL_MANAGER moved to ./terminal-manager.mjs in v0.5.4.

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


// CONTROL_CLAIM_FAILURES moved to ./claim-failure-tracker.mjs in v0.5.4 — its only direct readers
// are the two functions above, so they own it.

// noteControlClaimFailure moved to ./claim-failure-tracker.mjs in v0.5.4.

// noteControlClaimSuccess moved to ./claim-failure-tracker.mjs in v0.5.4.







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

// ensureRequiredReplyHandoff moved to ./required-reply-handoff.mjs in v0.5.4.

// ── Local filesystem helpers ─────────────────────────────────────────────────






// ── Message safety ───────────────────────────────────────────────────────────
// Messages from other agents are UNTRUSTED DATA. Wrap in code fences so
// Claude Code treats them as data, not instructions to follow.





// residentRuntimeBindingLost moved to ./resident-binding-health.mjs in v0.5.4.

// The IMPLEMENTATION lives in ./resident-runtime-lost.mjs; this is the binding that supplies the two
// names server.js owns — the shutdown chain and this machine's id. Deliberately NOT written as a
// `moved to` marker: `moved-names-resolve` treats a marker plus a local declaration as a fork, and
// it is right to — this is a borrow shim, and calling it a move would be a claim the file disproves.
const reportResidentRuntimeLost = (agentId, info, reason) =>
  reportResidentRuntimeLostImpl(agentId, info, reason, { MACHINE_ID, shutdownWithStatus });


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





// Tear down every managed-hermes triad survivor (gateway host, delivery loop,
// daemon, console PTY) this env bridge owns. Targets come from a FRESH service
// ownership read — NEVER the long-lived REMOTE_AGENT_STATE cache, because a
// managed→resident switch can make that cache stale until the next heartbeat.
// async: awaits the port-kill/stopDaemon promises so the kills land before
// process.exit. If the service is unavailable, fail safe and reap nothing; the
// next boot sweep handles genuine managed survivors after ownership is readable.
// runManagedTeardownForBridge moved to ./managed-teardown-sweeps.mjs in v0.5.4.

// Tear down ONE managed-hermes agent's triad (gateway host, delivery loop,
// daemon, console PTY) — the agent-scoped reaper for a Dashboard STOP/REMOVE of
// a managed hermes agent (fix/hermes-leak P2). Scoped strictly to the single
// agentId passed in: enumeration keys on the delivery-loop cmdline + the agent's
// own port/daemon-pid markers, so another agent's or a resident operator's
// processes can NEVER be enumerated. async: awaits the port-kill/stopDaemon
// promises. Best-effort; never throws.
// runSingleAgentManagedTeardown moved to ./single-agent-teardown.mjs in v0.5.4.

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
// runManagedTeardownSync moved to ./managed-teardown-sweeps.mjs in v0.5.4.

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
// fetchManagedOwnershipForEnv moved to ./managed-ownership.mjs in v0.5.4.
// `effectiveEnvironmentPayload` is injected: it reads `remoteEffectiveCwdRoots`, whose only writer
// is `heartbeatEnvironment` below, so the state stays here with its writer.
const fetchManagedOwnershipForEnv = createManagedOwnershipReader({ effectiveEnvironmentPayload });

// Env-bridge BOOT survivor sweep (before ensureSpawnLoop). Reaps managed-triad
// survivors of dead/crashed predecessors so "restart = zero survivors" holds
// even after SIGKILL — while NEVER touching an agent owned by a currently-live
// different bridge. Fail-safe: if ownership can't be fetched, reaps nothing.
// runBootSurvivorSweep moved to ./managed-teardown-sweeps.mjs in v0.5.4.
// The factory call sits HERE, after the fetchManagedOwnershipForEnv binding it consumes: a const
// cannot be read before its initialiser, and the two markers above are earlier in the file.
const { runManagedTeardownForBridge, runManagedTeardownSync, runBootSurvivorSweep } =
  createManagedTeardownSweeps({ fetchManagedOwnershipForEnv });

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
// runBootTombstonedMarkerSweep moved to ./boot-marker-sweep.mjs in v0.5.4.




// readManagedViaWrapperRuntimes moved to ./managed-wrapper-cache.mjs in v0.5.4.

// Reply contract toggle (managed_reply_capture_fallback). True (default) =
// safety-net: auto-mirror the run summary when a delivered run ends without an
// explicit comms_send reply. False = strict: never fabricate a reply from final
// text; leave the run reply-owed. 5s cache to avoid hammering /settings.
// _replyCaptureFallbackCache moved to ./required-reply-handoff.mjs in v0.5.4.
// readReplyCaptureFallback moved to ./required-reply-handoff.mjs in v0.5.4.




function effectiveEnvironmentPayload() {
  const payload = environmentHeartbeatPayload();
  if (remoteEffectiveCwdRoots && remoteEffectiveCwdRoots.length) {
    return { ...payload, cwdRoots: remoteEffectiveCwdRoots };
  }
  return payload;
}


async function heartbeatEnvironment({ syncManaged = true } = {}) {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: false, shuttingDown: shutdownStarted })) return false;
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
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: Boolean(environmentHeartbeatTimer), shuttingDown: shutdownStarted })) return;
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
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: Boolean(usageCollectorTimer), shuttingDown: shutdownStarted })) return;
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
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: Boolean(environmentControlTimer), shuttingDown: shutdownStarted })) return;
  runEnvironmentControlLoop().catch((error) => console.error("[aify] environment control loop error:", error));
  environmentControlTimer = setInterval(() => {
    runEnvironmentControlLoop().catch((error) => console.error("[aify] environment control loop error:", error));
  }, DISPATCH_POLL_MS);
}

async function runEnvironmentControlLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: environmentControlBusy, shuttingDown: shutdownStarted })) return;
  environmentControlBusy = true;
  try {
    await runEnvironmentControlPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      MACHINE_ID,
      effectiveEnvironmentPayload,
      shutdownWithStatus,
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
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: Boolean(spawnLoopTimer), shuttingDown: shutdownStarted })) return;
  runSpawnLoop().catch((error) => console.error("[aify] spawn loop error:", error));
  spawnLoopTimer = setInterval(() => {
    runSpawnLoop().catch((error) => console.error("[aify] spawn loop error:", error));
  }, DISPATCH_POLL_MS);
}

function ensureTerminalControlLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE && bridgeTerminalSupported(), alreadyActive: Boolean(terminalControlTimer), shuttingDown: shutdownStarted })) return;
  runTerminalControlLoop().catch((error) => console.error("[aify] terminal control loop error:", error));
  terminalControlTimer = setInterval(() => {
    runTerminalControlLoop().catch((error) => console.error("[aify] terminal control loop error:", error));
  }, TERMINAL_CONTROL_POLL_MS);
}

// updateTerminalControl moved to ./virtual-terminals.mjs in v0.5.4.

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function extractTerminalSessionHandle(runtime = "", command = "") {
  return extractRuntimeSessionHandleFromCommand(runtime, command);
}

// handleVirtualTerminalControl moved to ./virtual-terminals.mjs in v0.5.4.

// WS4 Task 4.2: host-reported dead-PTY marking. The server cannot probe a
// remote host pid; only the OWNING env bridge can. For each console PTY this
// bridge owns in-memory that is still `attached` but whose local pid is no
// longer alive, POST /terminals/{id}/report-dead so the server marks the row
// stopped + invalidates live-state (a frozen/crashed console can otherwise keep
// manufacturing presence). Best-effort; never throws. Does NOT kill anything —
// the in-memory exit path owns real teardown; this only reconciles stale rows.
// reportDeadOwnedTerminals moved to ./terminal-manager.mjs in v0.5.4.

async function runTerminalControlLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE && bridgeTerminalSupported(), alreadyActive: terminalControlBusy, shuttingDown: shutdownStarted })) return;
  terminalControlBusy = true;
  try {
    await runTerminalControlPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      effectiveEnvironmentPayload,
      extractTerminalSessionHandle,
    });
  } catch (error) {
    if (error?.status !== 404) {
      noteControlClaimFailure("terminal controls", error);
    }
  } finally {
    terminalControlBusy = false;
  }
}

// noteSpawnClaimFailure moved to ./claim-failure-tracker.mjs in v0.5.4.

// noteSpawnClaimSuccess moved to ./claim-failure-tracker.mjs in v0.5.4.

// isActiveManagedSessionStatus moved to ./session-predicates.mjs in v0.5.4.

async function syncManagedEnvironmentAgents() {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: managedEnvironmentSyncBusy, shuttingDown: shutdownStarted })) return;
  managedEnvironmentSyncBusy = true;
  try {
    await syncManagedEnvironmentAgentsPass({
      MACHINE_ID,
      effectiveEnvironmentPayload,
      ensureDispatchLoop,
    });
  } catch (error) {
    if (error?.status !== 404) {
      console.error("[aify] managed environment sync failed:", error?.message || error);
    }
  } finally {
    managedEnvironmentSyncBusy = false;
  }
}

async function runSpawnLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE && IS_ENVIRONMENT_BRIDGE, alreadyActive: spawnLoopBusy, shuttingDown: shutdownStarted })) return;
  spawnLoopBusy = true;
  try {
    await runSpawnPass({
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      MACHINE_ID,
      effectiveEnvironmentPayload,
      ensureDispatchLoop,
    });
  } finally {
    spawnLoopBusy = false;
  }
}

function ensureDispatchLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE, alreadyActive: Boolean(dispatchLoopTimer), shuttingDown: shutdownStarted })) return;
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
  if (shouldSkipLoop({ eligible: IS_REMOTE, alreadyActive: dispatchLoopBusy, shuttingDown: shutdownStarted })) return;
  dispatchLoopBusy = true;
  try {
    await runDispatchPass({
      AUTO_REREGISTER_AFTER_FAILURES,
      CLAIM_OPTS,
      CLAIM_WAIT_MS,
      MACHINE_ID,
      reportResidentRuntimeLost,
      terminateResidentHost,
    });
  } finally {
    dispatchLoopBusy = false;
  }
}


// ── MCP Server ───────────────────────────────────────────────────────────────

const server = new McpServer({
  name: "aify-comms-mcp",
  version: AIFY_VERSION,
});

registerAllTools(server, z, { ensureDispatchLoop });

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
  makeAutoRegister({ ensureDispatchLoop })().catch((err) => {
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
