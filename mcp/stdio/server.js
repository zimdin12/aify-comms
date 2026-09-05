#!/usr/bin/env node
//
// aify-comms-mcp -- MCP server for inter-agent communication between coding-agent runtimes.
//
// Modes:
//   - Remote: set AIFY_SERVER_URL (or legacy CLAUDE_MCP_SERVER_URL) to use HTTP server
//   - Local: filesystem-based message bus in .messages/ directory
//


import {
  API_KEY,
  HTTP_TIMEOUT_MS,
  IS_REMOTE,
  SERVER_URL,
  httpCall,
} from "./aify-service-endpoint.mjs";
import { BRIDGE_BUILD_TAG } from "./bridge-build.mjs";
import { reportResidentLost } from "./resident-lost.mjs";
import {
  makeResidentGatewayStatusReader,
  shouldArmResidentHermesTurnDetector,
} from "./resident-gateway-status.mjs";
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
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

// Plan 6 A2 (2026-05-26), hoisted here in v0.6 Phase 1: is this file the process entrypoint?
//
// DECLARED BEFORE ANY SIDE EFFECT, which is the whole reason it moved up. It used to sit ~700 lines
// lower, just above main(), so the heartbeat starts and the boot block below could not reference it —
// a `const` in the temporal dead zone throws. Everything that STARTS something now reads this.
//
// The guard is safe by its own evidence: real bridge launches invoke server.js directly via the wrapper
// shebang or `node mcp/stdio/server.js`, and main() has depended on exactly this check since v0.5.4. If
// it were ever wrong for a real launch, main() would not run and the bridge would already be dead.
const __isEntrypoint = (() => {
  try {
    return process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1]);
  } catch { return true; }
})();

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
import { AIFY_AGENT_ID, cleanEnvPlaceholder } from "./launch-identity.mjs";
import { localAgentNeedsDispatchHosting } from "./session-predicates.mjs";
import { startSessionHandleHeartbeat, makeDefaultHandlePoster } from "./session-handle-heartbeat.js";
import {
  TURN_BUSY_HEARTBEAT_MS,
  makeDefaultTurnBusyPoster,
  startTurnBusyHeartbeat,
} from "./turn-busy-heartbeat.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { startGatewayLivenessProbe } from "./hermes-gateway-liveness.js";
import { gatewayIndexUrlFromWs, makeGatewayReachabilityProbe, reportGatewayDead } from "./hermes-gateway.mjs";
import { startHermesGatewayTurnDetector } from "./hermes-gateway-turn-detector.js";
import { startClaudeTurnEndDetector } from "./claude-turn-end-detector.js";
import { AIFY_VERSION } from "./version.js";
import { shouldSkipLoop } from "./loop-gate.mjs";
import { main } from "./bridge-main.mjs";
import { runDispatchLoop } from "./dispatch-loop.mjs";
import { reportResidentRuntimeLost as reportResidentRuntimeLostImpl } from "./resident-runtime-lost.mjs";
import { registerAllTools } from "./register-tools.mjs";
import {
  DISPATCH_POLL_MS,
  __HEARTBEAT_MS,
  __RESIDENT_GATEWAY_TURN_IDLE_DEBOUNCE,
  __RESIDENT_GATEWAY_TURN_POLL_MS,
} from "./poll-intervals.mjs";
import { VIRTUAL_TERMINALS_BY_AGENT, VIRTUAL_TERMINAL_INPUT } from './virtual-terminals.mjs';


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
const __stopHandleHeartbeat = IS_REMOTE && __isEntrypoint
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
const __stopTurnBusyHeartbeat = (!IS_REMOTE || !__isEntrypoint) ? () => {} : startTurnBusyHeartbeat({
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
// Gating the START, not the beat: this file's own comment above the session-handle heartbeat says
// why — "the `!__serverUrl` guards elsewhere were written to prevent exactly this and could not
// fire. Gating the START is what actually stops it: nothing runs, nothing ticks." A no-op stopper
// keeps `cleanupOnExit` calling the same slots in the same order.
const __stopLivenessHeartbeat = !__isEntrypoint ? () => {} : startLivenessHeartbeat({
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
    intervalMs: TURN_BUSY_HEARTBEAT_MS,
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
// DELETED IN v0.6.2 with the environment bridge: confirmedManagedTeardownAgentIds, and the two managed-teardown
// sweeps that were its only users. A resident owns no managed workers, so there is nothing to reap.

// interruptActiveRuns moved to ./bridge-agent-state.mjs in v0.5.4.

function cleanupOnExit() {
  for (const run of ACTIVE_RUNS.values()) {
    try { run?.controller?.interrupt?.("Bridge process exiting"); } catch { /* best effort */ }
  }
  try { __stopHandleHeartbeat(); } catch { /* best effort */ }
  try { __stopTurnBusyHeartbeat(); } catch { /* best effort */ }
  try { __stopLivenessHeartbeat(); } catch { /* best effort */ }
  try { __stopGatewayProbe(); } catch { /* best effort */ }
  try { __stopResidentHermesTurnDetector(); } catch { /* best effort */ }
  try { stopClaudeTurnEndDetector(); } catch { /* best effort */ }
  try { __stopCodexTurnDetector(); } catch { /* best effort */ }
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
  // WS2: restart = clean slate. After the in-memory PTYs are stopped, reap every
  // DETACHED managed-hermes triad survivor (gateway host, delivery loop, daemon)
  // this env bridge owns — the processes engineered to outlive the launcher.
  // Scoped strictly to ownedManagedAgentIds(); never a resident/other-env process.
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
// VIRTUAL_TERMINALS_BY_AGENT moved to ./virtual-terminals.mjs in v0.5.4.
// VIRTUAL_TERMINAL_INPUT moved to ./virtual-terminals.mjs in v0.5.4.

// VIRTUAL_RPC_RUNTIMES moved to ./virtual-terminals.mjs in v0.5.4.

// findAgentIdForVirtualTerminal moved to ./virtual-terminals.mjs in v0.5.4.
// DISPATCH_POLL_MS moved to ./poll-intervals.mjs in v0.5.4.
// TERMINAL_CONTROL_POLL_MS moved to ./poll-intervals.mjs in v0.5.4.
let dispatchLoopTimer = null;
const AUTO_REREGISTER_AFTER_FAILURES = 4;
// RESIDENT_BINDING_FAILURES moved to ./resident-binding-health.mjs in v0.5.4.
// RESIDENT_BINDING_LOST_AFTER_FAILURES moved to ./resident-binding-health.mjs in v0.5.4.
// DELETED IN v0.6.2 with the environment bridge: the console turn-busy and console-working pulses, with
// `terminal-manager.mjs` itself. Only the bridge held PTYs to pulse for; aify-env owns them now and
// reports their state through its own plugin.
// ensureVirtualTerminal moved to ./virtual-terminals.mjs in v0.5.4.

// dispatchVirtualTerminalLine moved to ./virtual-terminals.mjs in v0.5.4.

// createVirtualTerminalSink moved to ./virtual-terminals.mjs in v0.5.4.

// DELETED IN v0.6.2 with the environment bridge: TERMINAL_MANAGER. It was only ever populated by cluster code.

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

// are the two functions above, so they own it.



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
// DELETED IN v0.6.2 with the environment bridge: every managed-teardown sweep -- graceful, sync-exit,
// single-agent and boot-marker -- with the ownership reader that fed them. They reaped the workers a
// DYING BRIDGE owned, and a resident owns none. aify-env holds the processes and reaps its own.
//
// THE REASONING IS NOT LOST, it moved with the code: aify-env's teardown keys ownerLive on owning-
// bridge freshness rather than on agent status, for the reason this block used to give -- a crashed
// owner's detached loop keeps heartbeating, so a status-based signal skips exactly the orphans the
// sweep exists to kill.

// readManagedViaWrapperRuntimes moved to ./managed-wrapper-cache.mjs in v0.5.4.

// _replyCaptureFallbackCache moved to ./required-reply-handoff.mjs in v0.5.4.
// readReplyCaptureFallback moved to ./required-reply-handoff.mjs in v0.5.4.



// updateTerminalControl moved to ./virtual-terminals.mjs in v0.5.4.

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
}

function extractTerminalSessionHandle(runtime = "", command = "") {
  return extractRuntimeSessionHandleFromCommand(runtime, command);
}

// handleVirtualTerminalControl moved to ./virtual-terminals.mjs in v0.5.4.

// DELETED IN v0.6.2 with the environment bridge: reportDeadOwnedTerminals.

// DELETED IN v0.6.2 with the environment bridge: the terminal-control loop.



// isActiveManagedSessionStatus moved to ./session-predicates.mjs in v0.5.4.

// DELETED IN v0.6.2 with the environment bridge: the managed-environment sync loop.

// DELETED IN v0.6.2 with the environment bridge: the spawn loop. aify-env claims spawns, PROVEN on real
// hardware 2026-09-03 with no bridge running at all.

function ensureDispatchLoop() {
  if (shouldSkipLoop({ eligible: IS_REMOTE, alreadyActive: Boolean(dispatchLoopTimer), shuttingDown: shutdownStarted })) return;
  if (!localAgentNeedsDispatchHosting({
    agentId: AIFY_AGENT_ID,
    channelsEnabled: String(process.env.AIFY_CHANNELS_ENABLED || "").trim() === "1",
  })) return;
  dispatchLoopTimer = setInterval(() => {
    runDispatchLoop({ AUTO_REREGISTER_AFTER_FAILURES, CLAIM_OPTS, CLAIM_WAIT_MS, MACHINE_ID, reportResidentRuntimeLost, shutdownStarted, terminateResidentHost }).catch((error) => console.error("[aify] dispatch loop error:", error));
  }, DISPATCH_POLL_MS);
}

// THE BOOT BLOCK IS GONE, v0.6.2. It ran UNCONDITIONALLY at import until v0.6 Phase 1, which is why
// nothing in this file could ever be tested: importing it started four loops and registered this
// process as the ENVIRONMENT BRIDGE, superseding the live one and reaping its managed workers. That is
// the standing rule "never run a bare `aify-comms`", and what took the whole managed fleet down on
// 2026-08-11. Phase 1 put it under the guard that already gates main(); v0.6.1 made the command refuse;
// v0.6.2 deleted the block and every module it started, because aify-env is the host tier and there is
// no second bridge for this process to be.
//
// `tests/server-import-does-not-boot-a-bridge.test.js` is the receipt and OUTLIVES the deletion: it
// imports this module in a child process and requires zero timers and zero calls to the service. The
// property it measures is the one that mattered — what an import DOES — and it holds for a file with
// nothing left to gate exactly as it held for a file with a guarded block.

// runDispatchLoop's shell and its busy flag moved to ./dispatch-loop.mjs in v0.5.4; the timer stays here.

// ── MCP Server ───────────────────────────────────────────────────────────────

const server = new McpServer({
  name: "aify-comms-mcp",
  version: AIFY_VERSION,
});

registerAllTools(server, z, { ensureDispatchLoop });

// main() moved to ./bridge-main.mjs in v0.5.4; the five names it needs are server.js's own.

if (__isEntrypoint) main({
  ORIGINAL_PARENT_PID,
  StdioServerTransport,
  ensureDispatchLoop,
  server,
  shutdownWithStatus,
}).catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
