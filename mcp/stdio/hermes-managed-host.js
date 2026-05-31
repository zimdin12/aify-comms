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
import fs from "fs";
import { spawnSync as nodeSpawnSync } from "node:child_process";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { defaultMachineId } from "./runtimes.js";
import { resolveGatewayPort } from "./hermes-endpoint.js";
import { pinnedSessionId } from "./hermes-session-id.js";
import { dispatchContent } from "./claude-channel.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
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
// Per-agent channel-sidecar bridge id (holistic-review F1, 2026-05-31). A
// machine-global `hermes-managed-host-<machine>` id collided across co-located
// managed hermes agents because bridge_instances.id is the PRIMARY KEY — only
// one agent could own the row, starving the others' liveness heartbeats and
// letting two detached delivery loops fight over one row. Scope by agentId.
const CHANNEL_BRIDGE_PREFIX = `hermes-managed-host-${MACHINE_ID}`;
function channelBridgeId(agentId) {
  const id = String(agentId || "").trim();
  return id ? `${CHANNEL_BRIDGE_PREFIX}-${id}` : CHANNEL_BRIDGE_PREFIX;
}
const POLL_MS = Math.max(
  500,
  Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_HERMES_CHANNEL_POLL_MS || 3000),
);
const HTTP_TIMEOUT_MS = Math.max(1000, Number(process.env.AIFY_HTTP_TIMEOUT_MS || 20000));
const READY_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_GATEWAY_READY_MS || 60000));
const RPC_TIMEOUT_MS = Math.max(5000, Number(process.env.AIFY_HERMES_RPC_TIMEOUT_MS || 60000));
// COLD-START DELIVERY RACE (2026-05-31): on the first dispatch after a cold
// (re)launch, the delivery loop can claim + try to deliver BEFORE the visible
// TUI has finished resuming `aify-<agentId>` into the gateway, so
// session.active_list returns no matching key yet. Wait (bounded) for the
// session to attach before submitting; if it never attaches in time, REQUEUE
// the run (leave it claimable) rather than failing it permanently.
const ATTACH_WAIT_MS = Math.max(2000, Number(process.env.AIFY_HERMES_ATTACH_WAIT_MS || 25000));
const ATTACH_POLL_MS = Math.max(100, Number(process.env.AIFY_HERMES_ATTACH_POLL_MS || 750));
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const RUNTIME = "hermes";
const HERMES_CMD = String(process.env.AIFY_HERMES_COMMAND || "hermes").trim() || "hermes";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The STABLE resume key the visible TUI attaches under. Its runtime id is
// ephemeral; we match on this stable key in session.active_list. This MUST be
// byte-identical to what the install.sh wrapper passes as HERMES_TUI_RESUME, so
// it reuses the SAME sanitization scheme (pinnedSessionId from
// hermes-session-id.js, which the wrapper mirrors via `tr -c 'a-zA-Z0-9_-'`).
function sessionKeyFor(agentId) {
  return pinnedSessionId(agentId);
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
// Stable session pre-seed — guarantee `aify-<agentId>` exists in hermes' DB so
// the visible TUI's `--resume aify-<agentId>` resolves on the VERY FIRST launch.
// ---------------------------------------------------------------------------
//
// WHY: `hermes --tui --resume <id>` calls the gateway `session.resume`, which
// returns 4007 "session not found" when <id> matches neither a session id nor a
// title (tui_gateway/server.py session.resume). On a fresh agent `aify-<id>`
// doesn't exist yet → first launch would land on "error: session not found"
// with no live session. We pre-create a persisted row with the EXPLICIT id
// `aify-<id>` (INSERT OR IGNORE — idempotent, never duplicates) so resume
// always succeeds. This mirrors how the api_server/resident path pins an
// explicit `aify-<id>` session id. Best-effort: any failure here is swallowed
// so a missing/old hermes never breaks the TUI launch (the TUI then forges a
// session exactly as it does today — no regression).

// Resolve the hermes venv python interpreter next to the hermes executable.
// hermesCmd is typically an absolute path to .../venv/Scripts/hermes(.exe) or
// .../venv/bin/hermes; the python sibling lives in the same dir. Returns the
// python path if found on disk, else "python" (PATH fallback).
export function resolveHermesPython(hermesCmd = HERMES_CMD) {
  const cmd = String(hermesCmd || "").trim();
  try {
    if (cmd && (cmd.includes("/") || cmd.includes("\\"))) {
      const dir = path.dirname(cmd);
      const candidates = [
        path.join(dir, "python.exe"),
        path.join(dir, "python3.exe"),
        path.join(dir, "python"),
        path.join(dir, "python3"),
      ];
      for (const c of candidates) {
        try {
          if (fs.existsSync(c)) return c;
        } catch {
          /* ignore */
        }
      }
    }
  } catch {
    /* ignore */
  }
  return process.platform === "win32" ? "python.exe" : "python3";
}

// Create-or-ignore the stable `aify-<agentId>` session row via the hermes
// SessionDB. Idempotent (INSERT OR IGNORE) + best-effort (never throws). Returns
// true when the row is known to exist afterward, false on any failure.
// `spawnSync` is injectable for tests.
export function ensureStableSession({
  agentId,
  hermesCmd = HERMES_CMD,
  spawnSync,
} = {}) {
  const id = String(agentId || "").trim();
  if (!id) return false;
  const key = sessionKeyFor(id);
  const py = resolveHermesPython(hermesCmd);
  // One-shot python: create the row with the explicit id, title it, confirm.
  const code = [
    "import sys",
    "try:",
    "    from hermes_state import SessionDB",
    "    db = SessionDB()",
    "    db.create_session(sys.argv[1], source='aify-managed')",
    "    try:",
    "        db.set_session_title(sys.argv[1], sys.argv[1])",
    "    except Exception:",
    "        pass",
    "    ok = bool(db.get_session(sys.argv[1]))",
    "    try:",
    "        db.close()",
    "    except Exception:",
    "        pass",
    "    sys.exit(0 if ok else 1)",
    "except Exception as exc:",
    "    sys.stderr.write('ensure-session failed: %s\\n' % exc)",
    "    sys.exit(2)",
  ].join("\n");
  try {
    const runner = spawnSync || nodeSpawnSync;
    const res = runner(py, ["-c", code, key], {
      stdio: ["ignore", "ignore", "pipe"],
      encoding: "utf8",
      timeout: 30000,
      env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
    });
    if (res && res.status === 0) return true;
    if (res && res.stderr) {
      console.error(`[hermes-managed-host] ensureStableSession('${key}'): ${String(res.stderr).trim()}`);
    }
  } catch (error) {
    console.error(
      `[hermes-managed-host] ensureStableSession('${key}') failed (best-effort):`,
      error?.message || String(error),
    );
  }
  return false;
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

// COLD-START requeue: the visible TUI has not (yet) attached its `aify-<id>`
// session to the gateway, so this is a TRANSIENT not-yet-ready condition, NOT a
// permanent failure. Put the run back to `queued` (claimable) so the very next
// poll delivers once the TUI finishes resuming. Never markRunFailed for this.
async function markRunRequeued(httpCall, run, reason) {
  const runId = String(run?.id || "");
  await httpCall("PATCH", `/dispatch/runs/${encodeURIComponent(runId)}`, {
    status: "queued",
    runtime: RUNTIME,
    agentStatus: "active",
    appendEvent: `managed hermes delivery deferred (requeued): ${reason}`,
    eventType: "requeued",
  });
}

// ---------------------------------------------------------------------------
// 3. DELIVERY — claim → active_list → prompt.submit (steer on busy) → delivered.
// ---------------------------------------------------------------------------

// Poll session.active_list until the visible TUI's `aify-<agentId>` session is
// attached to the gateway (returns its EPHEMERAL runtime sid), or the deadline
// elapses (returns null). This closes the COLD-START race: the delivery loop
// can claim before the TUI has finished `--resume aify-<id>`, so we WAIT for the
// attach instead of failing immediately. `nextId` advances the RPC id across
// polls. `wsClient`, `sleepImpl`, and the timing are injectable for tests.
export async function waitForActiveSession({
  wsClient,
  key,
  nextId,
  deadlineMs = ATTACH_WAIT_MS,
  intervalMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
  now = Date.now,
  log = (msg) => console.error(msg),
} = {}) {
  const deadline = now() + deadlineMs;
  let attempts = 0;
  for (;;) {
    attempts += 1;
    let listResp = null;
    try {
      listResp = await wsClient.request(
        buildSessionActiveListFrame({ id: nextId(), currentSessionId: "" }),
      );
    } catch (err) {
      // active_list itself failed (e.g. gateway hiccup) — treat as not-ready and
      // keep polling within the deadline.
      listResp = null;
      if (attempts === 1) {
        log(`[hermes-managed-host] session.active_list error while awaiting attach: ${err?.message || String(err)}`);
      }
    }
    const sessionId = pickSessionForKey(listResp, key);
    if (sessionId) {
      if (attempts > 1) {
        log(`[hermes-managed-host] visible TUI session '${key}' attached after ${attempts} poll(s); delivering.`);
      }
      return sessionId;
    }
    if (now() >= deadline) return null;
    if (attempts === 1) {
      log(`[hermes-managed-host] visible TUI session '${key}' not attached yet; waiting up to ${deadlineMs}ms for resume…`);
    }
    await sleepImpl(intervalMs);
  }
}

// Drive ONE claimed run end-to-end (WAKE-ONLY). NEVER throws.
//   reportTurnBusy(true) → WAIT for active session (cold-start race) →
//   prompt.submit{session_id, text} → (4009 busy → session.steer) →
//   markRunDelivered → clearTurn. If the visible TUI never attaches within the
//   bounded window, REQUEUE the run (claimable) — NOT markRunFailed — so the
//   next poll delivers once the TUI resumes. On real failure: markRunFailed.
// The runtime sid is re-discovered here every call — never cached.
export async function deliverRun({
  run,
  agentId,
  httpCall,
  wsClient,
  rpcId,
  attachWaitMs = ATTACH_WAIT_MS,
  attachPollMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
} = {}) {
  const key = sessionKeyFor(agentId);
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
  let id = typeof rpcId === "number" ? rpcId : Date.now() % 100000;
  try {
    // Re-discover the visible TUI's EPHEMERAL runtime sid every delivery,
    // WAITING (bounded) for the cold-start attach to finish.
    const sessionId = await waitForActiveSession({
      wsClient,
      key,
      nextId: () => id++,
      deadlineMs: attachWaitMs,
      intervalMs: attachPollMs,
      sleepImpl,
    });
    if (!sessionId) {
      // Transient: TUI not attached yet → requeue so the next poll delivers.
      console.error(
        `[hermes-managed-host] run ${run?.id || "?"}: visible TUI '${key}' did not attach within ${attachWaitMs}ms — requeuing (will retry).`,
      );
      await markRunRequeued(
        httpCall,
        run,
        `visible TUI session '${key}' not attached within ${attachWaitMs}ms`,
      ).catch(() => {});
      // No delivery happened — clear the turn_busy pulse so the agent does not
      // falsely show "working" while the run sits requeued.
      await clearTurn(httpCall, agentId).catch(() => {});
      return;
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
    // SUCCESS: do NOT clear turn_busy. prompt.submit is FIRE-AND-FORGET (it
    // returns on accept, not turn completion), so the visible-TUI turn is only
    // just STARTING — clearing here loses the "working" signal for the entire
    // turn (operator-reported 2026-05-31: managed hermes never showed working).
    // Mirror claude-channel.js: leave turn_busy set and let the server's 120s
    // TURN_BUSY_STALE_SECONDS window close it, while the agent's own reply
    // (require_reply → _mark_dispatch_run_answered) clears it precisely on
    // completion. The blocking hermes-channel.js path DOES clear because its
    // chatStream runs the turn to completion first; this fire-and-forget path
    // must NOT.
  } catch (error) {
    console.error(
      `[hermes-managed-host] run ${run?.id || "?"} delivery failed:`,
      error?.message || String(error),
    );
    await markRunFailed(httpCall, run, error).catch(() => {});
    // Delivery failed → not working. Clear the pulse we set above.
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

// One poll cycle: claim a small batch of channel/resident runs and deliver each.
// Returns { processed, released }. NEVER throws.
export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = channelBridgeId(agentId),
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
// Delivery loop (the `run` CLI mode).
// ---------------------------------------------------------------------------

// Drive the claim/deliver loop for `agentId`. Assumes (or brings up via the
// idempotent probe) the gateway host, opens a WS to it, polls /dispatch/claim,
// and installs teardown so the gateway host dies when this process exits.
// Returns when the agent is released to resident (loopOnce returns released).
// `deps` is injectable for tests:
//   spawnImpl, fetchImpl, openWs, httpCall, installTeardown, sleepImpl,
//   maxIterations (test bound; undefined → infinite).
export async function runDeliveryLoop(agentId, deps = {}) {
  const {
    httpCall = makeAifyHttpCall(AIFY_SERVER_URL, AIFY_API_KEY),
    spawnImpl,
    fetchImpl,
    openWs = openGatewayWsClient,
    installTeardown = installShutdownTeardown,
    sleepImpl = sleep,
    maxIterations,
    serverUrl = AIFY_SERVER_URL,
  } = deps;
  const id = String(agentId || "").trim();
  if (!id) {
    console.error("[hermes-managed-host] run: no bound agentId; nothing to drive.");
    return { released: false, processed: 0 };
  }
  const port = await resolveGatewayPort(id, { tempDir: TMP_DIR });

  let gatewayChild = null;
  installTeardown({ getChild: () => gatewayChild });

  const spawn = spawnImpl || (await import("node:child_process")).spawn;
  // Idempotent: if the wrapper's `ensure-host` already started it, probeFirst
  // reuses it (child=null); otherwise we (re)spawn it ourselves.
  const host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl });
  gatewayChild = host.child;

  let wsClient = null;
  const ensureWs = async () => {
    if (wsClient) return wsClient;
    wsClient = await openWs(host.wsUrl);
    return wsClient;
  };

  // A3 (status-liveness): unconditional liveness beat so an idle-but-alive
  // managed-hermes sidecar keeps its bridge_instances.last_seen fresh and is
  // not reaped as dead. Stopped on either return path via the finally below.
  const stopLiveness = startLivenessHeartbeat({
    intervalMs: 30_000,
    beat: async () => {
      if (!serverUrl) return;
      await httpCall("POST", `/agents/${encodeURIComponent(id)}/heartbeat`, {
        bridgeId: channelBridgeId(id),
        bridgeKind: "channel-sidecar",
        liveness: true,
      });
    },
  });

  let totalProcessed = 0;
  try {
    for (let iter = 0; maxIterations === undefined || iter < maxIterations; iter++) {
      try {
        if (!serverUrl) {
          await sleepImpl(POLL_MS);
          continue;
        }
        const ws = await ensureWs().catch((err) => {
          console.error("[hermes-managed-host] WS connect failed:", err?.message || String(err));
          return null;
        });
        if (!ws) {
          await sleepImpl(POLL_MS);
          continue;
        }
        const result = await runPollCycle({ agentId: id, httpCall, wsClient: ws });
        totalProcessed += result.processed || 0;
        if (result.released) {
          await teardownGatewayHost({ child: gatewayChild });
          return { released: true, processed: totalProcessed };
        }
      } catch (error) {
        console.error("[hermes-managed-host] tick error:", error?.message || String(error));
        try {
          wsClient?.close();
        } catch {
          /* ignore */
        }
        wsClient = null;
      }
      await sleepImpl(POLL_MS);
    }
    return { released: false, processed: totalProcessed };
  } finally {
    stopLiveness();
  }
}

// ---------------------------------------------------------------------------
// `ensure-host` CLI mode — bring the hidden gateway host up (or reuse) and
// print ONE JSON line {port, token, wsUrl} to stdout for the wrapper to parse.
// ---------------------------------------------------------------------------

// Resolve+ensure the gateway host and emit {port, token, wsUrl}. `deps` is
// injectable (spawnImpl, fetchImpl, out/err writers). Returns the coords.
export async function runEnsureHostCli(agentId, deps = {}) {
  const {
    spawnImpl,
    fetchImpl,
    out = (s) => process.stdout.write(s),
    err = (s) => process.stderr.write(s),
  } = deps;
  const id = String(agentId || "").trim();
  if (!id) throw new Error("ensure-host requires an agentId");
  const port = await resolveGatewayPort(id, { tempDir: TMP_DIR });
  // Pre-seed the stable `aify-<id>` DB session so the wrapper's
  // `--resume aify-<id>` resolves on the very first launch (else the gateway
  // returns 4007 and the TUI lands on "session not found"). Best-effort.
  if (deps.ensureSession !== false) {
    ensureStableSession({ agentId: id, spawnSync: deps.spawnSyncImpl });
  }
  const spawn = spawnImpl || (await import("node:child_process")).spawn;
  const host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl });
  // The gateway host must OUTLIVE this short-lived CLI process (the delivery
  // loop + the visible TUI attach to it). It was spawned detached+unref'd.
  // `resumeKey` is the canonical pinnedSessionId — the wrapper should set
  // HERMES_TUI_RESUME to THIS exact value (not reimplement sanitization in
  // shell) so the TUI's resumed session matches the loop's pickSessionForKey.
  const payload = {
    port: host.port,
    token: host.token,
    wsUrl: host.wsUrl,
    resumeKey: sessionKeyFor(id),
  };
  out(JSON.stringify(payload) + "\n");
  err(`[hermes-managed-host] gateway host ready for '${id}' on port ${host.port}\n`);
  return payload;
}

// ---------------------------------------------------------------------------
// argv dispatch.
// ---------------------------------------------------------------------------

// Dispatch on argv. Modes:
//   ensure-host <agentId> → runEnsureHostCli (prints JSON line, exits 0)
//   run <agentId>         → runDeliveryLoop (claim/deliver loop + teardown)
//   (none)                → legacy resident-driven loop using the bound agent.
// `deps` is injectable for tests.
export async function runCli(argv, deps = {}) {
  const mode = String(argv[0] || "").trim();
  if (mode === "ensure-host") {
    const agentId = String(argv[1] || "").trim() || readBoundAgentId();
    await runEnsureHostCli(agentId, deps);
    return { mode: "ensure-host", agentId };
  }
  if (mode === "run") {
    const agentId = String(argv[1] || "").trim() || readBoundAgentId();
    await runDeliveryLoop(agentId, deps);
    return { mode: "run", agentId };
  }
  // No subcommand: behave like the old main loop (resolve the bound agent and
  // drive it). Both spawns the host and runs the loop.
  const agentId = readBoundAgentId();
  await runDeliveryLoop(agentId, deps);
  return { mode: "loop", agentId };
}

if (IS_MAIN) {
  runCli(process.argv.slice(2)).catch((error) => {
    console.error("[hermes-managed-host] fatal:", error?.message || error);
    process.exit(1);
  });
}
