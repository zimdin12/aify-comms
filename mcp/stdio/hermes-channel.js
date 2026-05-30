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
// Reuse claude-channel.js's dispatch content/prompt builder verbatim so the
// hermes agent receives the same priority framing + inReplyTo guidance.
import { dispatchContent } from "./claude-channel.js";

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

// hermes api_server daemon coordinates.
const HERMES_BASE_URL = (process.env.AIFY_HERMES_APISERVER_URL || HERMES_DEFAULT_BASE_URL).replace(/\/+$/, "");
const HERMES_KEY = process.env.AIFY_HERMES_APISERVER_KEY || "";
const HERMES_SESSION_KEY = process.env.AIFY_HERMES_SESSION_KEY || "";

const MACHINE_ID = defaultMachineId();
const CHANNEL_BRIDGE_ID = `hermes-channel-${MACHINE_ID}`;
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
    bridgeId: CHANNEL_BRIDGE_ID,
    turnBusy: !!busy,
    turnRunId: runId,
    turnRuntime: RUNTIME,
  });
}

async function clearTurn(httpCall, agentId) {
  await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/turn-end`, {
    bridgeId: CHANNEL_BRIDGE_ID,
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
    summary: "Delivered to hermes api_server session; awaiting explicit reply",
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
  baseUrl = HERMES_BASE_URL,
  key = HERMES_KEY,
  sessionKey = HERMES_SESSION_KEY,
}) {
  const sessionId = pinnedSessionId(agentId);
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
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
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

// One poll cycle: claim up to a small batch of channel/resident runs for the
// agent and process each. Returns the number of runs processed. NEVER throws.
export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = CHANNEL_BRIDGE_ID,
  httpCall,
  apiClient,
  baseUrl = HERMES_BASE_URL,
  key = HERMES_KEY,
  sessionKey = HERMES_SESSION_KEY,
  maxBatch = 20,
} = {}) {
  let processed = 0;
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
      const run = claim?.run;
      const mode = String(run?.executionMode || "").trim().toLowerCase();
      if (!run || !["channel", "resident"].includes(mode)) break;
      await processClaimedRun({ run, agentId, httpCall, apiClient, baseUrl, key, sessionKey });
      processed++;
    }
  } catch (error) {
    console.error("[hermes-channel] poll cycle error:", error?.message || String(error));
  }
  return processed;
}

async function pollLoop() {
  const httpCall = makeAifyHttpCall(AIFY_SERVER_URL, AIFY_API_KEY);
  const apiClient = createHermesApiServerClient();
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
      await runPollCycle({ agentId, httpCall, apiClient });
    } catch (error) {
      // Belt-and-suspenders: runPollCycle already swallows, but never let the
      // loop die on an unexpected throw.
      console.error("[hermes-channel] tick error:", error?.message || String(error));
    }
    await sleep(POLL_MS);
  }
}

if (IS_MAIN) {
  // Fail loud at startup if the daemon isn't reachable — a silent no-op channel
  // is exactly the historical failure mode this design eliminates.
  const probe = await probeApiServer({ baseUrl: HERMES_BASE_URL, key: HERMES_KEY });
  assertApiServer(probe);
  pollLoop().catch((error) => {
    console.error("[hermes-channel] fatal:", error);
    process.exit(1);
  });
}
