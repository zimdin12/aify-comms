#!/usr/bin/env node
// Per-agent managed-hermes HIDDEN HELPER (the visible-TUI delivery model).
//
// VERIFIED BLUEPRINT (docs/superpowers/plans/2026-05-31-managed-hermes-visible-tui-and-governance.md):
// per managed hermes agent there are TWO hidden processes + aify's own WS client.
// This module is the per-agent helper that owns #1 and #3:
//   1. GATEWAY HOST: a HIDDEN `hermes dashboard --tui --port <P> --host
//      127.0.0.1 --no-open --skip-build` child (windowsHide:true — no popup
//      window). It is the ONLY server of the JSON-RPC WS `/api/ws`. `--tui` is
//      REQUIRED or `/api/ws` closes 4403. Auth token is scraped from the
//      dashboard index HTML (`__HERMES_SESSION_TOKEN__`).
//   2. (the VISIBLE Ink TUI in the bridge node-pty is started by the wrapper —
//      NOT here; that is install.sh's job in a separate task.)
//   3. DELIVERY: aify opens its OWN WS to the same gateway, discovers the
//      visible TUI's EPHEMERAL runtime sid via `session.active_list`
//      (pickSessionForKey on the STABLE key `aify-<agentId>`), then
//      `prompt.submit {session_id, text}` (fallback `session.steer` on 4009
//      busy). Events route to the TUI's transport (owner) so the TUI renders;
//      aify's submit does NOT displace it.
//
// WAKE-ONLY (symmetric with claude-channel.js / hermes-channel.js): this helper
// authors NO reply. The in-session hermes agent has the aify-comms comms_*
// tools loaded and self-replies via comms_send + inReplyTo, which closes the
// require_reply run. After a successful submit the run is left `delivered`.
//
// The runtime sid is EPHEMERAL (`uuid4().hex[:8]` per attach). It is NEVER
// cached — every delivery re-runs `session.active_list`.

import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { defaultMachineId } from "./runtimes.js";
import { agentPort } from "./hermes-endpoint.js";
import { dispatchContent } from "./claude-channel.js";
import {
  buildSessionActiveListFrame,
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  pickSessionForKey,
  isSessionBusyError,
} from "./hermes-gateway-protocol.js";

loadSettingsEnv();

const IS_MAIN =
  Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

// Windows + Docker Desktop: force IPv4 loopback (see claude-channel.js).
function coerceLoopbackToIPv4(url) {
  return String(url || "").replace(/^(https?:\/\/)localhost(?=[:\/]|$)/i, "$1127.0.0.1");
}

const AIFY_SERVER_URL = coerceLoopbackToIPv4(
  process.env.CLAUDE_MCP_SERVER_URL || process.env.AIFY_SERVER_URL || "",
).replace(/\/+$/, "");
const AIFY_API_KEY = process.env.CLAUDE_MCP_API_KEY || process.env.AIFY_API_KEY || "";

const MACHINE_ID = defaultMachineId();
const CHANNEL_BRIDGE_ID = `hermes-managed-host-${MACHINE_ID}`;
const POLL_MS = Math.max(
  500,
  Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_HERMES_CHANNEL_POLL_MS || 3000),
);
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));
const READY_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_GATEWAY_READY_MS || 60000));
const RPC_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_RPC_TIMEOUT_MS || 60000));
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const RUNTIME = "hermes";
const HERMES_CMD = String(process.env.AIFY_HERMES_COMMAND || "hermes").trim() || "hermes";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The STABLE resume key the visible TUI attaches under. Its runtime id is
// ephemeral; we match on this stable key in session.active_list.
function sessionKeyFor(agentId) {
  return `aify-${String(agentId || "").trim()}`;
}

// Resolve the bound agentId from the PID-keyed temp file (same mechanism as
// claude-channel.js), falling back to AIFY_AGENT_ID.
function readBoundAgentId() {
  try {
    const binding = readAgentBindingFile({ pid: process.ppid || process.pid, dir: TMP_DIR });
    if (binding.agentId) return binding.agentId;
  } catch {
    /* fall through */
  }
  return String(process.env.AIFY_AGENT_ID || "").trim();
}

// Default aify httpCall(method, endpoint, body) against ${baseUrl}/api/v1.
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

// ---------------------------------------------------------------------------
// 1. GATEWAY HOST — hidden `hermes dashboard --tui` child + token scrape.
// ---------------------------------------------------------------------------

// Fetch the dashboard index and scrape __HERMES_SESSION_TOKEN__. Returns the
// token string or throws. fetchImpl is injectable (tests pass a fake).
async function scrapeToken(indexUrl, fetchImpl) {
  const res = await fetchImpl(indexUrl, { method: "GET" });
  if (!res || res.ok === false) {
    const status = res?.status ?? "?";
    throw new Error(`dashboard index ${indexUrl} returned ${status}`);
  }
  const body = await res.text();
  const match = String(body).match(/__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/);
  if (!match) throw new Error(`__HERMES_SESSION_TOKEN__ not found in ${indexUrl}`);
  return match[1];
}

// Poll the dashboard index until it responds (and carries a token), or the
// deadline elapses. Returns the token.
async function waitForIndexToken(indexUrl, fetchImpl, { deadlineMs, intervalMs }) {
  const deadline = Date.now() + deadlineMs;
  let lastErr = null;
  for (;;) {
    try {
      return await scrapeToken(indexUrl, fetchImpl);
    } catch (err) {
      lastErr = err;
      if (Date.now() > deadline) {
        throw new Error(
          `hermes dashboard at ${indexUrl} did not become ready within ${deadlineMs}ms: ` +
            (lastErr?.message || String(lastErr)),
        );
      }
      await sleep(intervalMs);
    }
  }
}

// Spawn (idempotently) the hidden gateway host and return its coordinates.
//   { port, token, wsUrl, child }
// - `hermes dashboard --tui --port <port> --host 127.0.0.1 --no-open --skip-build`
// - detached:true, windowsHide:true (CRITICAL — no popup OS window).
// - When probeFirst is set we probe the index first; if a host is already
//   serving (token scrape succeeds) we DON'T spawn (idempotent re-attach).
export async function ensureGatewayHost({
  agentId,
  port,
  hermesCmd = HERMES_CMD,
  spawn,
  fetchImpl = (typeof fetch !== "undefined" ? fetch : undefined),
  probeFirst = true,
  readyTimeoutMs = READY_TIMEOUT_MS,
  readyIntervalMs = 250,
} = {}) {
  if (!spawn) throw new Error("ensureGatewayHost requires an injected spawn");
  if (!fetchImpl) throw new Error("ensureGatewayHost requires a fetch implementation");
  const indexUrl = `http://127.0.0.1:${port}/`;
  const wsUrlFor = (token) => `ws://127.0.0.1:${port}/api/ws?token=${token}`;

  // Idempotent probe: a host already serving the index → reuse it, no spawn.
  if (probeFirst) {
    try {
      const token = await scrapeToken(indexUrl, fetchImpl);
      return { port, token, wsUrl: wsUrlFor(token), child: null, reused: true };
    } catch {
      /* not up yet → spawn below */
    }
  }

  const args = [
    "dashboard",
    "--tui",
    "--port",
    String(port),
    "--host",
    "127.0.0.1",
    "--no-open",
    "--skip-build",
  ];
  const child = spawn(hermesCmd, args, {
    stdio: "ignore",
    detached: true,
    windowsHide: true, // CRITICAL: no popup window on Windows (ConPTY-less child).
    env: { ...process.env },
  });
  // Don't let the gateway host keep the helper alive on its own; we manage its
  // lifecycle explicitly via teardown.
  if (typeof child.unref === "function") child.unref();

  const token = await waitForIndexToken(indexUrl, fetchImpl, {
    deadlineMs: readyTimeoutMs,
    intervalMs: readyIntervalMs,
  });
  return { port, token, wsUrl: wsUrlFor(token), child, reused: false };
}

// ---------------------------------------------------------------------------
// WS client — a thin JSON-RPC request/response wrapper over `ws`.
// ---------------------------------------------------------------------------

// Open a WS client to the gateway and return { request(frame), close() }.
// `WebSocketImpl` is injectable for tests; production uses the bundled `ws`.
export async function openGatewayWsClient(wsUrl, { WebSocketImpl, timeoutMs = RPC_TIMEOUT_MS } = {}) {
  const WS = WebSocketImpl || (await import("ws")).default;
  const socket = new WS(wsUrl);
  const pending = new Map();
  let nextId = 100;

  await new Promise((resolve, reject) => {
    socket.once("open", resolve);
    socket.once("error", reject);
  });

  socket.on("message", (raw) => {
    let msg;
    try {
      msg = JSON.parse(String(raw));
    } catch {
      return;
    }
    if (msg.id !== undefined && pending.has(msg.id)) {
      const p = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) p.reject(msg.error);
      else p.resolve(msg.result ?? msg);
    }
    // Inbound events (deltas, tool frames, etc.) are owned by the TUI's
    // transport — this client ignores them; we only care about RPC replies.
  });
  socket.on("close", () => {
    for (const [, p] of pending) p.reject(new Error("hermes gateway WS closed"));
    pending.clear();
  });

  return {
    request(frame) {
      return new Promise((resolve, reject) => {
        if (socket.readyState !== 1 /* OPEN */) {
          reject(new Error("hermes gateway WS not open"));
          return;
        }
        const id = frame.id ?? nextId++;
        frame.id = id;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`hermes RPC ${frame.method} timed out`));
        }, timeoutMs);
        pending.set(id, {
          resolve: (v) => {
            clearTimeout(timer);
            resolve(v);
          },
          reject: (e) => {
            clearTimeout(timer);
            reject(e);
          },
        });
        socket.send(JSON.stringify(frame));
      });
    },
    close() {
      try {
        socket.close();
      } catch {
        /* ignore */
      }
    },
    _socket: socket,
  };
}

// ---------------------------------------------------------------------------
// aify dispatch reporting helpers (mirror hermes-channel.js).
// ---------------------------------------------------------------------------

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

async function markRunDelivered(httpCall, run) {
  const runId = String(run?.id || "");
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "delivered",
    summary: "Delivered to managed hermes visible TUI (prompt.submit); awaiting explicit reply",
    runtime: RUNTIME,
    agentStatus: "active",
    appendEvent: "Delivered to managed-hermes visible TUI (agent self-replies)",
    eventType: "delivered",
  });
}

async function markRunFailed(httpCall, run, error) {
  const runId = String(run?.id || "");
  const cause = error?.message || String(error);
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "failed",
    error: cause,
    summary: `managed hermes delivery failed: ${cause}`,
    runtime: RUNTIME,
    agentStatus: "active",
    appendEvent: `managed hermes delivery failed: ${cause}`,
    eventType: "failed",
  });
}

// ---------------------------------------------------------------------------
// 3. DELIVERY — claim → active_list → prompt.submit (steer on busy) → delivered.
// ---------------------------------------------------------------------------

// Drive ONE claimed run end-to-end (WAKE-ONLY). NEVER throws.
//   reportTurnBusy(true) → session.active_list → pickSessionForKey(aify-<id>)
//   → prompt.submit{session_id, text} → (4009 busy → session.steer) →
//   markRunDelivered → clearTurn. On any failure: markRunFailed + clearTurn.
// The runtime sid is re-discovered here every call — never cached.
export async function deliverRun({ run, agentId, httpCall, wsClient, rpcId } = {}) {
  const key = sessionKeyFor(agentId);
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
  let id = typeof rpcId === "number" ? rpcId : Date.now() % 100000;
  try {
    // Re-discover the visible TUI's EPHEMERAL runtime sid every delivery.
    const listResp = await wsClient.request(buildSessionActiveListFrame({ id: id++, currentSessionId: "" }));
    const sessionId = pickSessionForKey(listResp, key);
    if (!sessionId) {
      throw new Error(`no active hermes session matching '${key}' (visible TUI not attached?)`);
    }

    const text = dispatchContent(agentId, run || {});
    try {
      await wsClient.request(buildPromptSubmitFrame({ id: id++, sessionId, text }));
    } catch (err) {
      if (isSessionBusyError(err)) {
        // Mid-run: steer into the running turn instead of submitting a new one.
        await wsClient.request(buildSessionSteerFrame({ id: id++, sessionId, text }));
      } else {
        throw err;
      }
    }

    await markRunDelivered(httpCall, run);
  } catch (error) {
    console.error(
      `[hermes-managed-host] run ${run?.id || "?"} delivery failed:`,
      error?.message || String(error),
    );
    await markRunFailed(httpCall, run, error).catch(() => {});
  } finally {
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

// One poll cycle: claim a small batch of channel/resident runs and deliver each.
// Returns { processed, released }. NEVER throws.
export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = CHANNEL_BRIDGE_ID,
  httpCall,
  wsClient,
  maxBatch = 20,
} = {}) {
  let processed = 0;
  let released = false;
  try {
    for (let i = 0; i < maxBatch; i++) {
      const claim = await httpCall("POST", "/dispatch/claim", {
        agentId,
        machineId,
        bridgeId,
        // Standalone channel sidecar (NOT a wrapper-PTY child): the service gate
        // accepts a channel-sidecar claim for managed hermes the same way it
        // accepts claude's.
        bridgeKind: "channel-sidecar",
        executionModes: ["channel", "resident"],
      });
      // Mode FSM release: operator switched this agent to resident — stop driving.
      if (claim?.release) {
        console.error(
          `[hermes-managed-host] released: agent '${agentId}' switched to resident; helper stopping.`,
        );
        released = true;
        break;
      }
      const run = claim?.run;
      const mode = String(run?.executionMode || "").trim().toLowerCase();
      if (!run || !["channel", "resident"].includes(mode)) break;
      await deliverRun({ run, agentId, httpCall, wsClient });
      processed++;
    }
  } catch (error) {
    console.error("[hermes-managed-host] poll cycle error:", error?.message || String(error));
  }
  return { processed, released };
}

// ---------------------------------------------------------------------------
// Teardown — kill the gateway-host child on shutdown / release.
// ---------------------------------------------------------------------------

const _teardownState = { done: false };

// Kill the gateway-host child. Best-effort + idempotent (a shared `state` flag
// guards double teardown). NEVER throws.
export async function teardownGatewayHost({ child, state = _teardownState } = {}) {
  if (state.done) return;
  state.done = true;
  try {
    if (child && typeof child.kill === "function") child.kill("SIGTERM");
  } catch (error) {
    console.error(
      "[hermes-managed-host] gateway-host teardown failed (best-effort):",
      error?.message || String(error),
    );
  }
}

// Wire SIGTERM/SIGINT → teardownGatewayHost → exit. `getChild` returns the
// current gateway-host child (it's spawned after handler install). `proc` is
// injectable for tests.
export function installShutdownTeardown({
  getChild,
  proc = process,
  state = _teardownState,
} = {}) {
  const onSignal = async () => {
    const child = typeof getChild === "function" ? getChild() : null;
    await teardownGatewayHost({ child, state });
    try {
      proc.exit(0);
    } catch {
      /* test fake / already exiting */
    }
  };
  proc.once("SIGTERM", onSignal);
  proc.once("SIGINT", onSignal);
}

// ---------------------------------------------------------------------------
// Main loop.
// ---------------------------------------------------------------------------

async function mainLoop() {
  const httpCall = makeAifyHttpCall(AIFY_SERVER_URL, AIFY_API_KEY);
  const agentId = readBoundAgentId();
  if (!agentId) {
    console.error("[hermes-managed-host] no bound agentId; nothing to drive.");
    return;
  }
  const port = agentPort(agentId);

  let gatewayChild = null;
  installShutdownTeardown({ getChild: () => gatewayChild });

  // Bring up (or attach to) the hidden gateway host.
  const host = await ensureGatewayHost({ agentId, port, spawn: (await import("node:child_process")).spawn });
  gatewayChild = host.child;

  let wsClient = null;
  const ensureWs = async () => {
    if (wsClient) return wsClient;
    wsClient = await openGatewayWsClient(host.wsUrl);
    return wsClient;
  };

  for (;;) {
    try {
      if (!AIFY_SERVER_URL) {
        await sleep(POLL_MS);
        continue;
      }
      const ws = await ensureWs().catch((err) => {
        console.error("[hermes-managed-host] WS connect failed:", err?.message || String(err));
        return null;
      });
      if (!ws) {
        await sleep(POLL_MS);
        continue;
      }
      const result = await runPollCycle({ agentId, httpCall, wsClient: ws });
      if (result.released) {
        await teardownGatewayHost({ child: gatewayChild });
        return;
      }
    } catch (error) {
      console.error("[hermes-managed-host] tick error:", error?.message || String(error));
      // Drop a dead WS so the next tick reconnects.
      try {
        wsClient?.close();
      } catch {
        /* ignore */
      }
      wsClient = null;
    }
    await sleep(POLL_MS);
  }
}

if (IS_MAIN) {
  mainLoop().catch((error) => {
    console.error("[hermes-managed-host] fatal:", error);
    process.exit(1);
  });
}
