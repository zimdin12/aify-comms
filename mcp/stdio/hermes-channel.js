#!/usr/bin/env node
// Per-agent hermes api_server "channel" sidecar — the hermes analogue of
// claude-channel.js, made to work the claude-aify way.
//
// claude-channel.js claims dispatch runs over HTTP by agentId and PUSHES them
// into the same claude process via an MCP notification. hermes can't be woken
// by a server push, so this sidecar instead DRIVES the agent's pinned hermes
// session via the api_server platform of a per-agent long-lived `hermes gateway
// run` daemon (POST /api/sessions/{id}/chat/stream, SSE reply) to RUN the turn
// to completion.
//
// WAKE-ONLY (symmetric with claude-channel.js): this sidecar does NOT author or
// post a reply. The in-session hermes agent has the aify-comms comms_* tools
// loaded and authors its OWN reply via comms_send + inReplyTo, which closes the
// require_reply run later — exactly the threading claude gets. After a
// successful wake the run is left in status `delivered`; the agent's reply
// closes it. (The earlier "sidecar posts the captured reply" model is removed.)
//
// Shape mirrored from claude-channel.js: bound-agentId from the PID-keyed temp
// file, httpCall(method, endpoint, body) against ${baseUrl}/api/v1, claim via
// POST /dispatch/claim (executionModes incl. "channel"), turn_busy pulse via
// POST /agents/{id}/heartbeat, run PATCH via PATCH /dispatch/runs/{id}, and
// turn clear via POST /agents/{id}/turn-end. The poll loop is resilient: a
// chatStream failure PATCHes the run to a failed state with the cause and the
// loop continues — it must never die.
//
// Contract: docs/superpowers/specs/2026-05-30-hermes-apiserver-contract.md.

import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { defaultMachineId } from "./runtimes.js";
import { createHermesApiServerClient, DEFAULT_BASE_URL as HERMES_DEFAULT_BASE_URL } from "./hermes-apiserver-client.js";
import { probeApiServer, assertApiServer } from "./hermes-version.js";
import { pinnedSessionId } from "./hermes-session-id.js";
import { agentEndpoint } from "./hermes-endpoint.js";
import { stopDaemon as defaultStopDaemon } from "./hermes-daemon.js";
// Reuse claude-channel.js's dispatch content/prompt builder verbatim so the
// hermes agent receives the same priority framing + inReplyTo guidance.
import { dispatchContent } from "./claude-channel.js";
import { startInFlightRepulse } from "./hermes-turn-repulse.js";

// In-flight re-pulse cadence (#172). chatStream can run a turn well past the
// server's 120s TURN_BUSY_STALE_SECONDS window; re-pulse turn_busy while the
// chatStream promise is pending so the agent keeps showing `working`. Anchored
// on the pending promise (a REAL bridge-owned signal), NOT derived status.
const REPULSE_MS = Math.max(
  5000,
  Number(process.env.AIFY_HERMES_TURN_REPULSE_MS || 45000),
);

loadSettingsEnv();

const IS_MAIN =
  Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

// Windows + Docker Desktop: force IPv4 loopback (see claude-channel.js).
function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(/^(https?:\/\/)localhost(?=[:\/]|$)/i, "$1127.0.0.1");
}

// Same aify base-url env vars claude-channel.js honors.
const AIFY_SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
).replace(/\/+$/, "");
const AIFY_API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";

// hermes api_server daemon coordinates. When the operator/wrapper passes
// AIFY_HERMES_APISERVER_URL/_KEY they win (explicit override, back-compat). When
// ABSENT, the endpoint is resolved per-agent via agentEndpoint(agentId) — the
// SAME deterministic {port,key} the daemon was launched with — so the wrapper
// doesn't have to thread the api_server URL/key through the environment.
const HERMES_ENV_BASE_URL = process.env.AIFY_HERMES_APISERVER_URL
  ? process.env.AIFY_HERMES_APISERVER_URL.replace(/\/+$/, "")
  : "";
const HERMES_ENV_KEY = process.env.AIFY_HERMES_APISERVER_KEY || "";
const HERMES_SESSION_KEY = process.env.AIFY_HERMES_SESSION_KEY || "";

// Resolve {baseUrl, key} for an agent: explicit env override wins; otherwise
// derive the per-agent endpoint deterministically from agentId. Exported so the
// env-absent fallback is unit-testable.
export function resolveHermesEndpoint(agentId) {
  if (HERMES_ENV_BASE_URL || HERMES_ENV_KEY) {
    return {
      baseUrl: HERMES_ENV_BASE_URL || HERMES_DEFAULT_BASE_URL,
      key: HERMES_ENV_KEY,
    };
  }
  const ep = agentEndpoint(agentId);
  return { baseUrl: ep.baseUrl, key: ep.key };
}

const MACHINE_ID = defaultMachineId();
// Per-agent channel-sidecar bridge id (holistic-review F1, 2026-05-31). A
// machine-global `hermes-channel-<machine>` id collided across co-located hermes
// agents because bridge_instances.id is the PRIMARY KEY — only one agent could
// own the row, starving the others' liveness heartbeats. Scope by agentId.
const CHANNEL_BRIDGE_PREFIX = `hermes-channel-${MACHINE_ID}`;
function channelBridgeId(agentId) {
  const id = String(agentId || "").trim();
  return id ? `${CHANNEL_BRIDGE_PREFIX}-${id}` : CHANNEL_BRIDGE_PREFIX;
}
const POLL_MS = Math.max(
  500,
  Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_HERMES_CHANNEL_POLL_MS || 3000),
);
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const RUNTIME = "hermes";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Resolve the bound agentId from the PID-keyed temp file (same mechanism as
// claude-channel.js — server.js writes aify-agent-<ppid> on comms_register),
// falling back to AIFY_AGENT_ID.
function readBoundAgentId() {
  try {
    const binding = readAgentBindingFile({ pid: process.ppid || process.pid, dir: TMP_DIR });
    if (binding.agentId) return binding.agentId;
  } catch {
    // fall through
  }
  return String(process.env.AIFY_AGENT_ID || "").trim();
}

// Default aify httpCall(method, endpoint, body) against ${baseUrl}/api/v1.
// Tests inject their own; the production loop uses this one.
function makeAifyHttpCall(baseUrl, apiKey) {
  return async function httpCall(method, endpoint, body = null) {
    if (!baseUrl) return null;
    const url = `${baseUrl}/api/v1${endpoint}`;
    const options = { method, headers: {} };
    if (apiKey) options.headers["X-API-Key"] = apiKey;
    if (body) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), HTTP_TIMEOUT_MS);
    try {
      const res = await fetch(url, { ...options, signal: controller.signal });
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        const error = new Error(`HTTP ${res.status}: ${text}`);
        error.status = res.status;
        throw error;
      }
      return res.json().catch(() => ({}));
    } finally {
      clearTimeout(timeout);
    }
  };
}

function isChannelRun(run) {
  return String(run?.executionMode || "").trim().toLowerCase() === "channel";
}

async function reportTurnBusy(httpCall, agentId, { busy, runId = "" } = {}) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/heartbeat`, {
    bridgeId: channelBridgeId(agentId),
    turnBusy: !!busy,
    turnRunId: runId,
    turnRuntime: RUNTIME,
  });
}

async function clearTurn(httpCall, agentId) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/turn-end`, {
    bridgeId: channelBridgeId(agentId),
    turnRuntime: RUNTIME,
  });
}

// WAKE-ONLY: after a successful turn the run is left `delivered` (mirrors
// claude-channel.js's require_reply semantics). The in-session hermes agent's
// own comms_send + inReplyTo closes it. We never mark it completed/answered
// here — the sidecar authored no reply.
async function markRunDelivered(httpCall, run) {
  const runId = String(run?.id || "");
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "delivered",
    // D2 (#162): routine delivery is normal-path — no summary so the Runs audit
    // view stays clean. The 'delivered' event below carries the audit signal;
    // meaningful summaries are reserved for failures (see markRunFailed).
    summary: "",
    runtime: RUNTIME,
    agentStatus: "active",
    appendEvent: "Delivered to hermes channel sidecar (agent self-replies)",
    eventType: "delivered",
  });
}

async function markRunFailed(httpCall, run, error) {
  const runId = String(run?.id || "");
  const cause = error?.message || String(error);
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "failed",
    error: cause,
    summary: `hermes channel delivery failed: ${cause}`,
    runtime: RUNTIME,
    agentStatus: "active",
    appendEvent: `hermes channel delivery failed: ${cause}`,
    eventType: "failed",
  });
}

// Drive one claimed run end-to-end (WAKE-ONLY):
//   ensureSession (idempotent) → turn_busy=true → chatStream (RUN the turn to
//   completion so the in-session agent can call comms_send) → PATCH run
//   `delivered` → turn-end. The sidecar authors NO reply — the agent
//   self-replies (symmetric with claude-channel.js). On chatStream failure:
//   PATCH the run failed with the cause and clear the turn. NEVER throws —
//   resilience is the whole point of the channel loop.
export async function processClaimedRun({
  run,
  agentId,
  httpCall,
  apiClient,
  baseUrl,
  key,
  sessionKey = HERMES_SESSION_KEY,
}) {
  // Resolve the per-agent endpoint when the caller didn't pass one explicitly
  // (tests pass baseUrl/key directly; the production loop relies on this).
  if (baseUrl === undefined || key === undefined) {
    const resolved = resolveHermesEndpoint(agentId);
    if (baseUrl === undefined) baseUrl = resolved.baseUrl;
    if (key === undefined) key = resolved.key;
  }
  const sessionId = pinnedSessionId(agentId);
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
  // In-flight re-pulse (#172): while chatStream is pending (turn running), keep
  // turn_busy fresh past the server's 120s window. `inFlight` is the
  // bridge-owned signal — flipped false in the finally below the moment the
  // turn settles — NOT the server's derived status (anti-feedback-loop).
  let inFlight = true;
  const stopRepulse = startInFlightRepulse({
    intervalMs: REPULSE_MS,
    isInFlight: () => inFlight,
    pulse: () => reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }),
  });
  try {
    // Idempotent: 201 (created) and 409 (already exists) both resolve.
    await apiClient.ensureSession({ baseUrl, key, id: sessionId });

    const text = dispatchContent(agentId, run || {});
    // Run the turn to completion so the in-session agent gets the chance to
    // call comms_send. The returned assistant text is discarded for reply
    // purposes (kept only for a debug log) — the agent owns the reply.
    const reply = await apiClient.chatStream({
      baseUrl,
      key,
      sessionId,
      sessionKey: sessionKey || undefined,
      text,
    });
    if (process.env.AIFY_HERMES_CHANNEL_DEBUG) {
      console.error(
        `[hermes-channel] run ${run?.id || "?"} turn completed (reply authored in-session); ` +
          `discarded assistant text length=${String(reply ?? "").length}`,
      );
    }

    await markRunDelivered(httpCall, run);
  } catch (error) {
    // Loud, recorded, non-fatal: the operator sees a failed run with the cause
    // (e.g. model not authenticated / 401), the loop keeps polling.
    console.error(
      `[hermes-channel] run ${run?.id || "?"} delivery failed:`,
      error?.message || String(error),
    );
    await markRunFailed(httpCall, run, error).catch(() => {});
  } finally {
    // Turn settled (completed or failed): stop the in-flight re-pulse FIRST so
    // no stray beat races the clear, then clear turn_busy.
    inFlight = false;
    stopRepulse();
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

// One poll cycle: claim up to a small batch of channel/resident runs for the
// agent and process each. Returns the number of runs processed. NEVER throws.
export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = channelBridgeId(agentId),
  httpCall,
  apiClient,
  baseUrl,
  key,
  sessionKey = HERMES_SESSION_KEY,
  maxBatch = 20,
} = {}) {
  // Resolve the per-agent endpoint when not explicitly provided (env-absent
  // fallback derives it deterministically from agentId).
  if (baseUrl === undefined || key === undefined) {
    const resolved = resolveHermesEndpoint(agentId);
    if (baseUrl === undefined) baseUrl = resolved.baseUrl;
    if (key === undefined) key = resolved.key;
  }
  let processed = 0;
  let released = false;
  try {
    for (let i = 0; i < maxBatch; i++) {
      const claim = await httpCall("POST", "/dispatch/claim", {
        agentId,
        machineId,
        bridgeId,
        // Standalone channel sidecar (NOT a wrapper-PTY child). The service
        // gate (_bridge_claim_block_reason) accepts a channel-sidecar claim
        // for managed hermes the same way it accepts claude's — without it,
        // hermes (which also has a legacy wrapper-PTY path) would be rejected
        // with managed_wrapper_child_required and delivery would silently
        // never happen.
        bridgeKind: "channel-sidecar",
        executionModes: ["channel", "resident"],
      });
      // Mode FSM release signal (Task 4.1): the operator switched this agent to
      // resident — this managed sidecar is no longer the driver. Stop driving
      // (symmetric with claude-channel.js). The release bit propagates to the
      // caller so the poll loop can go idle / exit (one-driver invariant).
      if (claim?.release) {
        console.error(
          `[hermes-channel] released: agent '${agentId}' switched to resident; sidecar stopping.`,
        );
        released = true;
        break;
      }
      const run = claim?.run;
      const mode = String(run?.executionMode || "").trim().toLowerCase();
      if (!run || !["channel", "resident"].includes(mode)) break;
      await processClaimedRun({ run, agentId, httpCall, apiClient, baseUrl, key, sessionKey });
      processed++;
    }
  } catch (error) {
    console.error("[hermes-channel] poll cycle error:", error?.message || String(error));
  }
  // Return shape: callers that only count work can read `.processed`; the poll
  // loop reads `.released` to stop driving when the agent flips to resident.
  return { processed, released };
}

// SYMMETRIC TEARDOWN (Plan 1.x): the sidecar that ensured the per-agent daemon
// tears it down. Called on EVERY exit path — SIGTERM/SIGINT (bridge kills the
// wrapper PTY), the mode-FSM release signal (agent switched to resident), and a
// natural poll-loop end. Best-effort + idempotent: a shared `state` flag guards
// against double teardown so the signal handler and the loop-end path don't both
// kill (and so a second signal is a no-op). NEVER throws — a failed reap is
// logged, not fatal, and the wrapper's kill-prior reaper covers the SIGKILL case
// (SIGKILL can't be trapped, so no handler runs).
const _teardownState = { done: false };

export async function teardownDaemon({
  agentId,
  stopDaemon = defaultStopDaemon,
  state = _teardownState,
} = {}) {
  if (state.done) return;
  state.done = true;
  try {
    const result = await stopDaemon({ agentId });
    if (process.env.AIFY_HERMES_CHANNEL_DEBUG) {
      console.error(
        `[hermes-channel] daemon teardown for '${agentId}': ` +
          `${result && result.stopped ? `stopped pid ${result.pid ?? "?"}` : "no daemon to stop"}`,
      );
    }
  } catch (error) {
    console.error(
      `[hermes-channel] daemon teardown for '${agentId}' failed (best-effort):`,
      error?.message || String(error),
    );
  }
}

// Wire SIGTERM/SIGINT → teardownDaemon → exit. `proc` and `stopDaemon` are
// injectable for tests (so we never send real signals to the test process).
// The handler is fast: it kicks off teardown and exits — it does not block exit
// indefinitely.
export function installShutdownTeardown({
  agentId,
  proc = process,
  stopDaemon = defaultStopDaemon,
  state = _teardownState,
} = {}) {
  const onSignal = async () => {
    await teardownDaemon({ agentId, stopDaemon, state });
    try {
      proc.exit(0);
    } catch {
      /* test fake / already exiting */
    }
  };
  proc.once("SIGTERM", onSignal);
  proc.once("SIGINT", onSignal);
}

async function pollLoop() {
  const httpCall = makeAifyHttpCall(AIFY_SERVER_URL, AIFY_API_KEY);
  const apiClient = createHermesApiServerClient();
  // Track the last bound agent so the poll-end / release teardown knows which
  // per-agent daemon to reap.
  let lastAgentId = "";
  while (true) {
    try {
      if (!AIFY_SERVER_URL) {
        await sleep(POLL_MS);
        continue;
      }
      const agentId = readBoundAgentId();
      if (!agentId) {
        await sleep(POLL_MS);
        continue;
      }
      lastAgentId = agentId;
      const result = await runPollCycle({ agentId, httpCall, apiClient });
      // Mode FSM (Task 4.1): a release signal means the operator switched this
      // agent to resident — stop driving and exit so the resident TUI/CLI owns
      // the session (one-driver invariant; symmetric with claude-channel.js).
      // Tear down THIS agent's daemon before exiting: the resident TUI brings up
      // its own gateway, so the managed daemon must not linger.
      if (result && result.released) {
        await teardownDaemon({ agentId });
        return;
      }
    } catch (error) {
      // Belt-and-suspenders: runPollCycle already swallows, but never let the
      // loop die on an unexpected throw.
      console.error("[hermes-channel] tick error:", error?.message || String(error));
    }
    await sleep(POLL_MS);
  }
  // Unreachable today (loop only returns via the release path above, which tears
  // down inline), but if a future change ends the loop without releasing, reap
  // the last agent's daemon so it never leaks.
  // eslint-disable-next-line no-unreachable
  await teardownDaemon({ agentId: lastAgentId });
}

if (IS_MAIN) {
  // Fail loud at startup if the daemon isn't reachable — a silent no-op channel
  // is exactly the historical failure mode this design eliminates. Resolve the
  // endpoint from the bound agentId (env override still wins inside
  // resolveHermesEndpoint).
  const bootAgentId = readBoundAgentId();
  // Symmetric teardown: when the bridge SIGTERMs this sidecar (wrapper PTY kill)
  // or the operator Ctrl-C's it, tear down the per-agent daemon we ensured.
  installShutdownTeardown({ agentId: bootAgentId });
  const { baseUrl: bootBaseUrl, key: bootKey } = resolveHermesEndpoint(bootAgentId);
  const probe = await probeApiServer({ baseUrl: bootBaseUrl, key: bootKey });
  assertApiServer(probe);
  pollLoop().catch((error) => {
    console.error("[hermes-channel] fatal:", error);
    process.exit(1);
  });
}
