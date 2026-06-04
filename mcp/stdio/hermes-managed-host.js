#!/usr/bin/env node
// Per-agent managed-hermes HIDDEN HELPER (the visible-TUI delivery model).
//
// VERIFIED BLUEPRINT (docs/superpowers/plans/2026-05-31-managed-hermes-visible-tui-and-governance.md):
// per managed hermes agent there are TWO hidden processes + aify's own WS client.
// This module is the per-agent helper that owns #1 and #3:
//   1. GATEWAY HOST: a HIDDEN `hermes dashboard --port <P> --host 127.0.0.1
//      --no-open --skip-build` child (windowsHide:true — no popup window). It is
//      the ONLY server of the JSON-RPC WS `/api/ws`. (hermes 0.15.1 dropped the
//      `dashboard --tui` form — `--tui` is now a rejected arg on the subcommand;
//      see ensureGatewayHost.) Auth token is scraped from the dashboard index
//      HTML (`__HERMES_SESSION_TOKEN__`).
//   2. (the VISIBLE Ink TUI in the bridge node-pty is started by the wrapper —
//      NOT here; that is install.sh's job. It attaches to THIS gateway host via
//      HERMES_TUI_GATEWAY_URL.)
//   3. DELIVERY: aify opens its OWN WS to the same gateway and resolves the
//      agent's session via `session.active_list` — NATIVE-SESSION-ID model
//      (2026-06-03): target the agent's bound REAL session id (the marker
//      written at launch/register), with a most-recent-live-session fallback
//      (symmetric with claude UUID / codex thread id). NOT the retired synthetic
//      `aify-<agentId>` key. Then `prompt.submit {session_id, text}` (fallback
//      `session.steer` on 4009 busy). Events route to the TUI's transport
//      (owner) so the TUI renders; aify's submit does NOT displace it.
//
// WAKE-ONLY (symmetric with claude-channel.js): this helper authors NO reply.
// The in-session hermes agent has the aify-comms comms_* tools loaded and
// self-replies via comms_send + inReplyTo, which closes the require_reply run.
// After a successful submit the run is left `delivered`.
//
// The real session id is re-discovered every delivery (the most-recent fallback
// may rebind it), so it is NEVER cached — every delivery re-runs
// `session.active_list`.

import os from "os";
import path from "path";
import fs from "fs";
import { spawnSync as nodeSpawnSync } from "node:child_process";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import { defaultMachineId } from "./runtimes.js";
import {
  resolveGatewayPort,
  writeGatewayUrlMarker,
  clearGatewayMarkers as defaultClearGatewayMarkers,
  clearSessionMarker as defaultClearSessionMarker,
  readSessionIdMarker,
  writeSessionIdMarker,
} from "./hermes-endpoint.js";
import { defaultKillByPort } from "./hermes-daemon.js";
import { writeLoopReady, clearLoopReady } from "./hermes-loop-ready.js";
import { pinnedSessionId } from "./hermes-session-id.js";
import { dispatchContent } from "./claude-channel.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";
import { startGatewayLivenessProbe } from "./hermes-gateway-liveness.js";
import {
  startInFlightRepulse,
  shouldManagedHostRepulse,
  shouldLatchComplete,
} from "./hermes-turn-repulse.js";
import {
  buildSessionActiveListFrame,
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildRenderNoticeFrame,
  pickSessionById,
  pickMostRecentSession,
  pickSessionStatusForKey,
  pickSessionStatusById,
  isGatewaySessionIdle,
  isGatewaySessionWorking,
  isSessionBusyError,
} from "./hermes-gateway-protocol.js";
import {
  startHermesGatewayTurnDetector,
  DEFAULT_IDLE_DEBOUNCE_TICKS,
} from "./hermes-gateway-turn-detector.js";

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
// TUI has finished resuming its real session into the gateway, so
// session.active_list returns no matching session yet. Wait (bounded) for the
// session to attach before submitting; if it never attaches in time, REQUEUE
// the run (leave it claimable) rather than failing it permanently.
const ATTACH_WAIT_MS = Math.max(2000, Number(process.env.AIFY_HERMES_ATTACH_WAIT_MS || 25000));
const ATTACH_POLL_MS = Math.max(100, Number(process.env.AIFY_HERMES_ATTACH_POLL_MS || 750));
// BOUNDED NO-ATTACH FAIL (Task 2.3, 2026-06-03). The cold-start requeue (above) is
// correct for a genuine cold start — the visible `hermes --tui` is still resuming
// its session into the gateway, so a poll or two finds active_list empty and the
// run is requeued (claimable) so the NEXT poll delivers. But when the visible TUI
// NEVER attaches to THIS loop's gateway host (it ran its OWN tui_gateway instead,
// or no TUI is running at all), active_list stays empty FOREVER and the old code
// requeued the SAME run on every poll silently — the operator's message stranded
// with no signal (the ci-senior-dev gateway-9136 active_list=0 incident). After
// N CONSECUTIVE empty-active_list requeues for the SAME run, FAIL it with an
// actionable "no visible TUI attached to gateway <url>; relaunch hermes-aify"
// message (mirrored to the sender) instead of requeuing forever. A successful
// attach (delivery) resets the per-run counter, so a slow-but-eventual cold start
// is never penalized. Configurable; default 5 (≈5×POLL_MS ≈ 15s of no-TUI).
const EMPTY_ATTACH_FAIL_THRESHOLD = Math.max(
  1,
  Number(process.env.AIFY_HERMES_EMPTY_ATTACH_FAIL_THRESHOLD || 5),
);
// STALE-SESSION BIND-RACE GRACE (FIX A, 2026-06-03). The freshness floor used to
// PERMANENTLY reject any fallback session that started before the delivery attempt
// — which meant an IDLE, already-attached session (the exact one ready to receive
// work) was skipped on EVERY poll, the loop hit its deadline, and the run requeued
// FOREVER (the operator only ever saw the placeholder). The floor's real purpose is
// only to win the RELAUNCH race (don't bind a being-torn-down prior session before
// the fresh `hermes --tui` re-attaches). That race resolves in a couple of seconds,
// so the floor only needs to hold for an INITIAL grace window, not forever. After
// the grace elapses, an attached (present-in-active_list) session is accepted even
// if its stamp predates delivery. Default: 45% of the attach deadline (configurable),
// clamped to a sane floor/ceiling.
const ATTACH_FRESH_GRACE_FRACTION = (() => {
  const raw = Number(process.env.AIFY_HERMES_ATTACH_FRESH_GRACE_FRACTION);
  if (Number.isFinite(raw) && raw >= 0 && raw <= 1) return raw;
  return 0.45;
})();
const TMP_DIR = process.env.TEMP || process.env.TMP || os.tmpdir();
const RUNTIME = "hermes";
// In-flight re-pulse cadence + bounded window (#172). prompt.submit is
// FIRE-AND-FORGET (returns on accept, not turn completion) and the managed-host
// WS client cannot reliably observe the gateway turn-complete event, so we
// re-pulse turn_busy on an interval for a BOUNDED window after each submit so a
// long managed-hermes turn keeps showing `working` past the server's 120s
// window. The window is hard-capped so a missed completion CANNOT stick
// `working` forever (anti-feedback-loop guard for this completion-event-less
// path). The agent's own reply closing the run is the precise clear; the next
// submit resets the window.
const REPULSE_MS = Math.max(5000, Number(process.env.AIFY_HERMES_TURN_REPULSE_MS || 45000));
// Continuous gateway turn-state detector cadence (fix/hermes-working-debounce).
// A faster, dedicated poll of the gateway session["running"] status that drives
// the BIDIRECTIONAL turn-state detector (sets working on a gateway-running turn,
// clears on sustained idle). Faster than REPULSE_MS so turn-state reflects
// promptly; the idle→end debounce (GATEWAY_TURN_IDLE_DEBOUNCE ticks at this
// cadence) is what prevents the mid-turn-idle flap. ~3s × 3 ticks = ~9s of
// sustained idle before a turn-end — well under the 120s server backstop.
const GATEWAY_TURN_POLL_MS = Math.max(
  1000,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_POLL_MS || 3000),
);
const GATEWAY_TURN_IDLE_DEBOUNCE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE || DEFAULT_IDLE_DEBOUNCE_TICKS),
);
const REPULSE_WINDOW_MS = Math.max(
  REPULSE_MS,
  Number(process.env.AIFY_HERMES_TURN_REPULSE_WINDOW_MS || 15 * 60 * 1000),
);
const HERMES_CMD = String(process.env.AIFY_HERMES_COMMAND || "hermes").trim() || "hermes";
// Proactive gateway-liveness probe cadence (status-liveness, 2026-06-02). Every
// interval the delivery loop probes the gateway HOST (dashboard index) for
// reachability; after GATEWAY_PROBE_THRESHOLD consecutive failures it reports
// the gateway dead (resident-lost) so the agent stops showing `available` while
// the gateway host is actually gone — the complement to the reactive
// connect-refused self-correct in deliverRun/runDeliveryLoop.
const GATEWAY_PROBE_MS = Math.max(
  5000,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_MS || 30000),
);
const GATEWAY_PROBE_THRESHOLD = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_THRESHOLD || 3),
);
// NO-TUI TEARDOWN BACKSTOP (FIX SET A2, 2026-06-03). A1 makes the wrapper SIGTERM
// the delivery loop when the visible TUI closes (trap on EXIT/INT/TERM), and the
// loop's installTeardown reaps the gateway host it owns. But a SIGKILL'd terminal
// (or a hard `kill -9` of the wrapper) BYPASSES that trap, so the loop must
// self-detect "no visible TUI is attached anymore" and tear itself down. The
// gateway-liveness probe above only catches an UNREACHABLE gateway host — here the
// gateway host is still reachable (often because the orphaned host is keeping its
// OWN headless session alive) but session.active_list shows ZERO attached sessions
// (no visible TUI / no non-loop WS client). After this many CONSECUTIVE poll
// cycles with zero attached sessions, report resident-lost + teardown so the agent
// flips offline and the orphaned gateway host is reaped. Default ~10 cycles
// (≈10×POLL_MS ≈ 30s) so a brief relaunch gap (TUI detaching then re-attaching)
// never trips it; configurable.
const NO_TUI_TEARDOWN_CYCLES = Math.max(
  1,
  Number(process.env.AIFY_HERMES_NO_TUI_TEARDOWN_CYCLES || 10),
);
// NO-TUI COLD-START GRACE (FIX, 2026-06-03). The delivery loop is spawned BEFORE
// the visible `hermes --tui` attaches to the gateway, so the no-TUI teardown
// backstop above (NO_TUI_TEARDOWN_CYCLES empties) could false-fire during a slow
// cold start — a first-launch TUI build on a loaded WSL2 host can take >30s
// (10 cycles × ~3s POLL_MS) to attach, and the loop would tear itself down right
// after launch before the TUI ever showed up. The grace + a "have I ever seen
// the TUI" latch (hasSeenAttachedTui) fix this: an empty active_list only counts
// toward teardown ONCE the TUI has attached at least once OR the cold-start grace
// has elapsed. After a genuine attach-then-leave, the latch keeps the backstop
// firing after N empties as before. Default 90s; configurable.
const NO_TUI_GRACE_MS = Math.max(
  0,
  Number(process.env.AIFY_HERMES_NO_TUI_GRACE_MS || 90000),
);
// Per-probe HTTP timeout — short so a slow probe still completes within the
// interval and counts as ONE failure (not a hang). Debounced by the threshold.
const GATEWAY_PROBE_TIMEOUT_MS = Math.max(
  500,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_TIMEOUT_MS || 5000),
);

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Flatten the many session.active_list envelope shapes into a row array. Mirrors
// hermes-gateway-protocol.js's internal normalizer (kept local so the freshness
// guard can read a chosen row's timestamp without an extra protocol export).
function activeListRowsLocal(activeListResponse) {
  return Array.isArray(activeListResponse)
    ? activeListResponse
    : Array.isArray(activeListResponse?.result?.sessions)
    ? activeListResponse.result.sessions
    : Array.isArray(activeListResponse?.sessions)
    ? activeListResponse.sessions
    : Array.isArray(activeListResponse?.result)
    ? activeListResponse.result
    : [];
}

// Freshness epoch (ms) of a session.active_list row — the SAME key precedence
// pickMostRecentSession orders by (last_active → started_at → created_at). 0 when
// no parseable timestamp is present (a row with no stamp can never clear a floor
// that is > 0, so it's treated as not-fresh — the safe default for the guard).
function rowFreshnessStamp(row) {
  return (
    Number(
      Date.parse(
        row?.last_active ||
          row?.lastActive ||
          row?.started_at ||
          row?.startedAt ||
          row?.created_at ||
          row?.createdAt ||
          0,
      ),
    ) || 0
  );
}

// The real id off an active_list row (`id` / `session_id` / `sessionId`).
function rowRealIdLocal(row) {
  return String(row?.id || row?.session_id || row?.sessionId || "").trim();
}

// Seed the per-agent active-session file (FIX C) in the SAME shape hermes' TUI
// writes (useSessionLifecycle.ts writeActiveSessionFile → {"session_id": "..."}),
// and that hermes' Python wrapper reads (_read_tui_active_session_file → .session_id).
// Byte-compatible so the in-session bridge reads a real handle at launch instead of
// the stale launch-time id. Best-effort; the caller swallows throws.
function defaultWriteActiveSessionFile(filePath, sessionId) {
  const p = String(filePath || "").trim();
  const sid = String(sessionId || "").trim();
  if (!p || !sid) return;
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify({ session_id: sid }), { mode: 0o600 });
}

// Freshness stamp of the row whose real id is `recentId` within an active_list
// response (so the fallback can compare the CHOSEN most-recent session against
// the freshness floor). 0 when the row can't be found / has no stamp.
function stampForSessionId(activeListResponse, recentId) {
  const wanted = String(recentId || "").trim();
  if (!wanted) return 0;
  for (const r of activeListRowsLocal(activeListResponse)) {
    if (rowRealIdLocal(r) === wanted) return rowFreshnessStamp(r);
  }
  return 0;
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
// - `hermes dashboard --port <port> --host 127.0.0.1 --no-open --skip-build`
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

  // hermes 0.15.1 (2026.5.29) moved `--tui` to a TOP-LEVEL flag; the `dashboard`
  // subcommand now REJECTS it ("unrecognized arguments: --tui"), which killed the
  // gateway host at spawn → ensure-host's 60s readiness timeout → every managed
  // hermes dispatch reaped as "no live claimer". So the `--tui` CLI flag is dropped.
  //
  // BUT (2026-06-04, root-caused from the operator's "gateway websocket connection
  // failed" incident): `--tui` did MORE than the index — it enabled the dashboard's
  // EMBEDDED-CHAT feature, which gates the `/api/ws` JSON-RPC WebSocket the bridge +
  // visible TUI attach to (`web_server.py`: `if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
  // ws.close(code=4403)` on `/api/ws`). Plain `hermes dashboard` serves the index
  // TOKEN but its `/api/ws` CLOSES (empirically: code=1006/4403) — so the earlier
  // "plain dashboard serves /api/ws (verified)" claim was WRONG; it only verified the
  // index, not the socket. `_DASHBOARD_EMBEDDED_CHAT_ENABLED` is set by `--tui` OR the
  // `HERMES_DASHBOARD_TUI=1` env (hermes_cli/web_server.start_server). Since the flag
  // is rejected on the subcommand, we set the ENV instead (verified: `/api/ws` -> OPEN
  // with it, CLOSE without). This is the crash-safe equivalent of the old `--tui`.
  const args = [
    "dashboard",
    "--port",
    String(port),
    "--host",
    "127.0.0.1",
    "--no-open",
    "--skip-build",
  ];
  // Capture the gateway host's stderr (was `stdio:"ignore"`, which SILENTLY hid
  // spawn/arg errors — the 0.15.1 `--tui` rejection took a full manual repro to
  // surface). stdin/stdout stay ignored; stderr → a per-port log so the next
  // gateway failure is one `tail` away.
  let gwErrFd = "ignore";
  try {
    const logDir = path.join(os.homedir(), ".local", "state", "aify-comms");
    fs.mkdirSync(logDir, { recursive: true });
    gwErrFd = fs.openSync(path.join(logDir, `hermes-gateway-host-${port}.log`), "a");
  } catch {
    gwErrFd = "ignore";
  }
  const child = spawn(hermesCmd, args, {
    stdio: ["ignore", "ignore", gwErrFd],
    detached: true,
    windowsHide: true, // CRITICAL: no popup window on Windows (ConPTY-less child).
    // Managed agents run unattended — there is no operator at the wheel to answer
    // tool-approval prompts (execute_code, etc.). hermes freezes YOLO at import from
    // HERMES_YOLO_MODE (tools/approval.py), so the gateway HOST that actually runs
    // the dispatch turn must carry it — the wrapper's `--yolo` only reaches the
    // visible TUI *client*, which does NOT govern the gateway-hosted turn's approvals.
    // `hermes dashboard` REJECTS a `--yolo` flag (unrecognized arg, like the 0.15.1
    // `--tui` rejection), so the env var is the correct, crash-safe lever.
    //
    // HERMES_DASHBOARD_TUI=1 enables the dashboard EMBEDDED-CHAT feature that gates
    // the `/api/ws` WebSocket the bridge + visible TUI attach to (see the args comment
    // above). Without it `/api/ws` closes 4403 → "gateway websocket connection failed"
    // across all managed hermes agents → headless orphans. It is the crash-safe env
    // equivalent of the `--tui` flag the dashboard subcommand rejects (verified:
    // `/api/ws` OPENs with it, CLOSEs without).
    env: { ...process.env, HERMES_YOLO_MODE: "1", HERMES_DASHBOARD_TUI: "1" },
  });
  if (typeof gwErrFd === "number") {
    try { fs.closeSync(gwErrFd); } catch {}
  }
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

  // CONNECT TIMEOUT (2026-06-02 hotfix): a gateway that accepts the socket but
  // never completes the WS upgrade would otherwise hang this await FOREVER (the
  // open/error promise never settles), silently wedging the whole delivery loop
  // — it never claims, never writes its ready marker, and the agent looks dead.
  // Reject after `timeoutMs` so the caller treats it like a dead gateway (retry
  // / self-correct) instead of hanging.
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try { socket.terminate?.() ?? socket.close?.(); } catch { /* ignore */ }
      reject(new Error(`hermes gateway WS connect timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    socket.once("open", () => { clearTimeout(timer); resolve(); });
    socket.once("error", (err) => { clearTimeout(timer); reject(err); });
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

// Read the dispatch run's current status + require_reply flag (the
// host-observable turn-end signals). Best-effort: any error → status "" (treated
// as not-yet-terminal, requireReply false). The run's terminal status
// (`completed` when the agent self-replies, or failed/cancelled/stopped) is one
// real "this turn finished" signal; a `delivered` run with require_reply=0 is
// the OTHER (a delivery-only nudge owes no turn) — see shouldLatchComplete.
// GET /dispatch/runs/{id} already exposes `requireReply` via
// _serialize_dispatch_run_row (no server change needed).
async function fetchRunStatus(httpCall, runId) {
  const id = String(runId || "").trim();
  if (!id) return { status: "", requireReply: false };
  try {
    const resp = await httpCall("GET", `/dispatch/runs/${encodeURIComponent(id)}`);
    return {
      status: String(resp?.run?.status || "").trim(),
      requireReply: !!resp?.run?.requireReply,
    };
  } catch {
    return { status: "", requireReply: false };
  }
}

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
// GATEWAY LIVENESS GAP — reactive mitigation (status-liveness).
//
// runtimes.js compute-capabilities grants `resident-run` (→ agent shows
// `available`) to a hermes agent whenever runtimeConfig.gatewayUrl is a
// non-empty string — that is a PRESENCE check, not a LIVENESS check. After the
// ephemeral gateway host on that port dies, the bridge keeps heartbeating, so
// the agent stays `available` for the whole ~150s lease and the dispatcher
// accepts a run that only discovers the dead port at connect time
// (ECONNREFUSED). The durable fix (probe gateway reachability in the capability
// check) is delicate (async probe / flap risk) and is deferred — see
// KNOWN_ISSUES.md. This is the REACTIVE half: when an INITIAL gateway connect
// is refused/unreachable, fail the run cleanly and self-correct availability.

// Classify an error as an INITIAL gateway-connect refusal/unreachability — the
// dead-ephemeral-port incident shape. This is deliberately narrow: it matches
// the socket-level connect codes (ECONNREFUSED / host- or net-unreachable /
// connect-timeout / DNS) but NOT the mid-stream errors the WS client throws
// AFTER a healthy connect (`hermes gateway WS closed`, `... WS not open`, RPC
// timeouts, or a 4009 busy). Only the connect failure should self-correct the
// agent off `available`; a transient mid-turn blip must not.
export function isGatewayConnectRefused(err) {
  if (!err) return false;
  const code = String(err.code || "").toUpperCase();
  if (
    code === "ECONNREFUSED" ||
    code === "EHOSTUNREACH" ||
    code === "ENETUNREACH" ||
    code === "ETIMEDOUT" ||
    code === "ENOTFOUND" ||
    code === "EAI_AGAIN"
  ) {
    return true;
  }
  const message = String(err.message || err || "");
  // WS open errors surface only the message on some impls. Exclude the known
  // post-connect / RPC messages so a mid-stream drop never trips the signal.
  if (/WS closed|WS not open|timed out|session busy/i.test(message)) return false;
  return /ECONNREFUSED|connection refused|connect ETIMEDOUT|EHOSTUNREACH|ENETUNREACH|ENOTFOUND|getaddrinfo/i.test(
    message,
  );
}

// Build the actionable run-failure message for a dead gateway port.
export function gatewayUnreachableMessage(gatewayUrl) {
  const url = String(gatewayUrl || "").trim() || "(unknown)";
  return (
    `Hermes gateway unreachable at ${url} (connection refused). ` +
    `The gateway host likely died; restart this agent's hermes-aify session to get a fresh gateway.`
  );
}

// Build the actionable run-failure message for the BOUNDED no-attach case (Task
// 2.3): the gateway host is REACHABLE (we polled session.active_list) but NO
// visible hermes TUI ever attached to it — active_list stayed empty across the
// bounded requeue budget. This is distinct from gatewayUnreachableMessage (a dead
// PORT): here the host is alive but no TUI is a WS client of it (it spun up its own
// tui_gateway, or no `hermes --tui` is running), so injected prompts have nowhere
// to render. The fix is to relaunch hermes-aify so the visible TUI attaches to THIS
// gateway via HERMES_TUI_GATEWAY_URL. `attempts` is the consecutive empty-poll count.
export function noTuiAttachedMessage(gatewayUrl, attempts) {
  const url = String(gatewayUrl || "").trim() || "(unknown)";
  const n = Number(attempts) || 0;
  return (
    `No visible hermes TUI attached to gateway ${url} ` +
    `(session.active_list empty across ${n} consecutive delivery attempts). ` +
    `The visible TUI is not a client of this gateway — relaunch this agent's ` +
    `hermes-aify session so the TUI attaches (HERMES_TUI_GATEWAY_URL) and can render messages.`
  );
}

// Self-correct off `available` via the EXISTING resident-lost signal (the same
// host→server path server.js uses when a resident Codex app-server is
// unreachable). The server transitions the agent resident→managed (or stopped),
// which immediately drops `resident-run`/`available` instead of waiting out the
// ~150s heartbeat lease. NOTE: deliberately NO bridgeId — the managed-host's
// channel-sidecar bridge id is not the resident MCP bridge that owns
// runtime_state.bridgeInstanceId, so sending it would hit the server's
// bridge_not_current guard and be ignored. Best-effort: never throws.
export async function reportGatewayDead({
  httpCall,
  agentId,
  runtime = RUNTIME,
  machineId = MACHINE_ID,
  gatewayUrl = "",
  reason = "",
} = {}) {
  const id = String(agentId || "").trim();
  if (!httpCall || !id) return;
  const why = reason || gatewayUnreachableMessage(gatewayUrl);
  try {
    await httpCall("POST", `/agents/${encodeURIComponent(id)}/resident-lost`, {
      machineId,
      runtime,
      reason: why,
    });
  } catch (error) {
    console.error(
      `[hermes-managed-host] resident-lost self-correct for '${id}' failed (best-effort):`,
      error?.message || String(error),
    );
  }
}

// Derive the gateway HOST index URL (`http://127.0.0.1:<port>/`) from the
// gateway WS URL (`ws://127.0.0.1:<port>/api/ws?token=...`). The index is the
// cheapest reachability signal for the hidden `hermes dashboard --tui` host —
// the same surface ensureGatewayHost's idempotent probe uses. Returns "" when
// the URL can't be parsed (probe then treats the gateway as unreachable).
export function gatewayIndexUrlFromWs(wsUrl) {
  const raw = String(wsUrl || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    const proto = u.protocol === "wss:" ? "https:" : "http:";
    return `${proto}//${u.host}/`;
  } catch {
    const m = raw.match(/^wss?:\/\/([^/]+)/i);
    if (!m) return "";
    const proto = /^wss:/i.test(raw) ? "https:" : "http:";
    return `${proto}//${m[1]}/`;
  }
}

// Build a single-shot gateway reachability probe for the proactive liveness
// checker. Resolves `{ alive: boolean }`; a non-OK response or a thrown
// fetch/timeout counts as not-alive (a failure). `fetchImpl` + `timeoutMs` are
// injectable so the driver is unit-testable with no real sockets.
export function makeGatewayReachabilityProbe({
  indexUrl,
  fetchImpl = (typeof fetch !== "undefined" ? fetch : undefined),
  timeoutMs = GATEWAY_PROBE_TIMEOUT_MS,
} = {}) {
  return async function probe() {
    const url = String(indexUrl || "").trim();
    if (!url || typeof fetchImpl !== "function") return { alive: false };
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    const timer = controller
      ? setTimeout(() => {
          try {
            controller.abort();
          } catch {
            /* ignore */
          }
        }, timeoutMs)
      : null;
    try {
      const res = await fetchImpl(url, {
        method: "GET",
        signal: controller ? controller.signal : undefined,
      });
      // A live dashboard host returns the index (200). Any HTTP response at all
      // means the port is bound + serving, so treat ok===false defensively but
      // a thrown connect (ECONNREFUSED) below is the real dead-gateway signal.
      return { alive: !!(res && res.ok !== false) };
    } catch {
      return { alive: false };
    } finally {
      if (timer) clearTimeout(timer);
    }
  };
}

// COLD-START requeue: the visible TUI has not (yet) attached its real session
// to the gateway, so this is a TRANSIENT not-yet-ready condition, NOT a
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

// Native-session-id delivery (2026-06-03): poll session.active_list until the
// agent's session is attached to the gateway, then return its REAL session id to
// submit against. Resolution, per poll:
//   (a) PRIMARY — the agent's bound REAL session id. `wantId` is read from the
//       agent-keyed marker (`aify-hermes-session-<agentId>`, written at launch /
//       comms_register). If non-empty AND a live active_list row carries that id,
//       target it. This is symmetric with claude (UUID) / codex (thread id).
//   (b) FALLBACK — no bound id yet, OR the bound id isn't (yet) in active_list:
//       target the gateway's MOST-RECENT live session (this active_list is the
//       agent's OWN gateway host, so the freshest row is the visible TUI). On a
//       successful fallback we PERSIST that real id via the marker so the next
//       launch/loop/bridge all agree on the same session (best-effort write).
//   (c) keep the bounded-wait/poll loop: if nothing is attached yet, keep polling
//       within the deadline; return null only when the window elapses (cold-start
//       requeue, never a hard fail).
// `nextId` advances the RPC id across polls. `wsClient`, `sleepImpl`, the timing,
// and the marker read/write are injectable for tests.
export async function waitForActiveSession({
  wsClient,
  agentId,
  // The agent's bound real session id. When omitted it's read from the marker.
  wantId,
  // Legacy: the old synthetic `aify-<agentId>` key. No longer the primary match
  // path; retained only so existing callers/tests that pass `key` don't break and
  // so a key-titled row can still resolve when no real id is known.
  key,
  nextId,
  tempDir = TMP_DIR,
  deadlineMs = ATTACH_WAIT_MS,
  intervalMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
  now = Date.now,
  // STALE-SESSION BIND-RACE GRACE (FIX A, 2026-06-03): on a RELAUNCH the per-agent
  // gateway host is REUSED (ensureGatewayHost → child=null), so the loop can poll
  // `session.active_list` BEFORE the freshly-relaunched `hermes --tui` re-attaches.
  // The pickMostRecentSession FALLBACK would then bind a STALE prior session (the
  // one being torn down) and persist it to the marker. The freshness floor (`since`,
  // the loop/poll start epoch captured at entry) protects against that — BUT as a
  // BOUNDED GRACE, not a permanent rejection. For the INITIAL grace window we prefer
  // a fresh row (stamp >= floor) and keep WAITING when only stale rows are present
  // (winning the relaunch race). ONCE the grace elapses we ACCEPT the most-recent
  // attached row even if its stamp predates delivery — presence in active_list means
  // the session is live/attached (a torn-down session leaves the list), so an idle
  // attached session must be delivered to, never requeued forever. The marker-matched
  // real id (PRIMARY) is ALWAYS accepted regardless of freshness/grace — it's the
  // intended session. `since` and `graceMs` are injectable for tests.
  since,
  // Bounded grace window (ms) during which a stale-stamped fallback row is still
  // skipped (relaunch race). Default: a fraction of the attach deadline. After this
  // elapses, a stale-but-attached fallback row is accepted. Injectable for tests.
  graceMs,
  // Marker read/write seams (best-effort; never throw in the delivery path).
  readMarker = readSessionIdMarker,
  writeMarker = writeSessionIdMarker,
  log = (msg) => console.error(msg),
} = {}) {
  // Freshness floor for the most-recent fallback. Captured ONCE at entry so it is
  // the moment this (relaunched) delivery attempt began — any session that
  // started before this is a stale pre-attach leftover.
  const freshnessFloor = Number.isFinite(Number(since)) ? Number(since) : now();
  // The grace window is bounded: prefer-fresh-and-wait until `graceUntil`, then
  // accept the most-recent attached row even if stale. Default to a fraction of the
  // deadline so the relaunch race has a window but delivery is never blocked forever.
  const resolvedGraceMs = Number.isFinite(Number(graceMs))
    ? Math.max(0, Number(graceMs))
    : Math.max(0, Math.round(deadlineMs * ATTACH_FRESH_GRACE_FRACTION));
  const graceUntil = freshnessFloor + resolvedGraceMs;
  const id = String(agentId || "").trim();
  // Resolve the wanted real id once: explicit arg wins, else the marker.
  let wanted = String(wantId || "").trim();
  if (!wanted && id) {
    try {
      wanted = String(readMarker(id, { tempDir }) || "").trim();
    } catch {
      wanted = "";
    }
  }
  const label = wanted || key || id || "(unbound)";

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

    // (a) PRIMARY: the agent's bound real session id is live.
    let sessionId = wanted ? pickSessionById(listResp, wanted) : null;

    // (b) FALLBACK: no bound id, or the bound id isn't live yet → most-recent
    // live session for this gateway. Persist it so subsequent launches agree.
    // STALE-SESSION BIND-RACE GRACE (FIX A): a fresh row (stamp >= floor) is bound
    // immediately. A STALE row (stamp < floor) is only SKIPPED during the initial
    // grace window — that buys time for a freshly-relaunched `hermes --tui` to
    // re-attach so we don't bind the being-torn-down prior session. ONCE the grace
    // has elapsed, an attached row is the live session that's ready for work (a
    // torn-down session would have left active_list), so we ACCEPT it even though
    // its stamp predates delivery — otherwise an idle attached session requeues
    // forever. The PRIMARY marker-matched id above bypasses this entirely.
    if (!sessionId) {
      const recent = pickMostRecentSession(listResp);
      if (recent) {
        const stamp = stampForSessionId(listResp, recent);
        const fresh = stamp >= freshnessFloor;
        const graceElapsed = now() >= graceUntil;
        if (fresh || graceElapsed) {
          sessionId = recent;
          if (id && recent !== wanted) {
            // Capture the real id we fell back to (best-effort; never throws).
            try {
              writeMarker(id, recent, { tempDir });
            } catch {
              /* best-effort marker write — never break delivery */
            }
            wanted = recent; // subsequent polls now treat this as the bound id.
            log(
              fresh
                ? `[hermes-managed-host] '${id}': bound real session id ${recent} from gateway's most-recent live session (fallback).`
                : `[hermes-managed-host] '${id}': relaunch grace elapsed; binding most-recent ATTACHED session ${recent} despite stale stamp (idle-session delivery).`,
            );
          }
        } else if (attempts === 1) {
          log(
            `[hermes-managed-host] '${id}': most-recent gateway session ${recent} is stale (started before this delivery attempt); waiting up to ${resolvedGraceMs}ms (relaunch grace) for a fresh attach before binding it.`,
          );
        }
      }
    }

    if (sessionId) {
      if (attempts > 1) {
        log(`[hermes-managed-host] visible TUI session '${label}' attached after ${attempts} poll(s); delivering.`);
      }
      return sessionId;
    }
    if (now() >= deadline) return null;
    if (attempts === 1) {
      log(`[hermes-managed-host] visible TUI session '${label}' not attached yet; waiting up to ${deadlineMs}ms for resume…`);
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
  // In-flight tracker (#172): the delivery loop owns a single object whose
  // { submittedAt, completed } fields gate the re-pulse beat. deliverRun stamps
  // submittedAt on a successful submit (opens the window) and zeroes it on
  // requeue/failure (closes it). `now` is injectable for tests.
  inFlight = null,
  now = Date.now,
  // The gateway WS URL this delivery is connecting through. Used only to build
  // an actionable failure message + drive the self-correct when the connect is
  // refused (dead ephemeral port). Optional; "" → message says "(unknown)".
  gatewayUrl = "",
  // Temp dir holding the agent's real-session-id marker (native-session-id
  // model). Defaults to the process temp dir; injectable so tests isolate the
  // marker read/write from the shared tmp dir.
  tempDir = TMP_DIR,
  // BOUNDED NO-ATTACH FAIL (Task 2.3). A mutable per-LOOP map { runId -> count }
  // of CONSECUTIVE empty-active_list requeues for the same run, owned by
  // runDeliveryLoop so it persists across poll cycles (the same run is re-claimed
  // each cycle after a requeue). When a run's count reaches `emptyAttachFailThreshold`
  // we markRunFailed with noTuiAttachedMessage (the visible TUI never attached to
  // this gateway) INSTEAD of requeuing forever. A successful attach/delivery deletes
  // the run's entry (resets the streak), so a slow-but-eventual cold start is never
  // failed. Optional — defaults to a throwaway map (single-shot callers/tests keep
  // the legacy infinite-requeue cold-start behaviour until the threshold trips).
  emptyAttachCounter = new Map(),
  emptyAttachFailThreshold = EMPTY_ATTACH_FAIL_THRESHOLD,
} = {}) {
  await reportTurnBusy(httpCall, agentId, { busy: true, runId: run?.id || "" }).catch(() => {});
  let id = typeof rpcId === "number" ? rpcId : Date.now() % 100000;
  // STALE-SESSION BIND-RACE GUARD: capture WHEN this delivery attempt began so
  // waitForActiveSession's most-recent fallback only binds a session that started
  // at/after this point — never a stale pre-relaunch session the reused gateway is
  // still listing while the fresh `hermes --tui` re-attaches.
  const deliveryStartedAt = now();
  try {
    // Re-discover the agent's REAL session id every delivery (native-session-id
    // model, 2026-06-03), WAITING (bounded) for the cold-start attach to finish.
    // waitForActiveSession resolves by the bound real id (marker) with a
    // most-recent-live-session fallback — never the synthetic `aify-<agentId>`.
    const sessionId = await waitForActiveSession({
      wsClient,
      agentId,
      tempDir,
      nextId: () => id++,
      deadlineMs: attachWaitMs,
      intervalMs: attachPollMs,
      sleepImpl,
      now,
      since: deliveryStartedAt,
    });
    if (!sessionId) {
      // No visible TUI session attached this attempt. Count CONSECUTIVE empties
      // for this run (the per-loop map persists across poll cycles, since the same
      // run is re-claimed each cycle after a requeue). Below the bounded threshold
      // this is a genuine cold-start → REQUEUE (claimable) so the next poll delivers
      // once the TUI finishes resuming. At/above the threshold the visible TUI is
      // NOT a client of this gateway (it never attached) → FAIL with an actionable
      // message mirrored to the sender, instead of silently requeuing forever
      // (Task 2.3 / the ci-9136 active_list=0 strand).
      const runKey = String(run?.id || "");
      const prior = runKey ? Number(emptyAttachCounter.get(runKey) || 0) : 0;
      const attempts = prior + 1;
      if (runKey) emptyAttachCounter.set(runKey, attempts);

      if (attempts >= emptyAttachFailThreshold) {
        const message = noTuiAttachedMessage(gatewayUrl, attempts);
        console.error(
          `[hermes-managed-host] run ${run?.id || "?"}: ${message} — FAILING (bounded no-attach after ${attempts} attempts).`,
        );
        await markRunFailed(httpCall, run, new Error(message)).catch(() => {});
        if (runKey) emptyAttachCounter.delete(runKey);
        if (inFlight) {
          inFlight.submittedAt = 0;
          inFlight.runId = "";
        }
        await clearTurn(httpCall, agentId).catch(() => {});
        return;
      }

      // Transient (cold start): TUI not attached yet → requeue so the next poll
      // delivers once it finishes resuming.
      console.error(
        `[hermes-managed-host] run ${run?.id || "?"}: agent '${agentId}' session did not attach within ${attachWaitMs}ms — requeuing (attempt ${attempts}/${emptyAttachFailThreshold}, will retry).`,
      );
      await markRunRequeued(
        httpCall,
        run,
        `agent '${agentId}' session not attached within ${attachWaitMs}ms (attempt ${attempts}/${emptyAttachFailThreshold})`,
      ).catch(() => {});
      // No delivery happened — clear the turn_busy pulse so the agent does not
      // falsely show "working" while the run sits requeued, and close the
      // in-flight window so the re-pulse beat stops.
      if (inFlight) {
        inFlight.submittedAt = 0;
        inFlight.runId = "";
      }
      await clearTurn(httpCall, agentId).catch(() => {});
      return;
    }
    // The visible TUI session DID attach — reset this run's no-attach streak so a
    // future transient empty starts the bounded budget fresh (never inherits a
    // stale count from a prior cold start that ultimately delivered).
    if (run?.id) emptyAttachCounter.delete(String(run.id));

    const text = dispatchContent(agentId, run || {});

    // #3 COMPLEMENT: draw the INBOUND message as a boxed notice in the operator's
    // visible TUI BEFORE submitting the turn. prompt.submit is fire-and-forget,
    // so without this the operator never sees WHAT arrived — only the agent's
    // eventual reply (rendered via the plugin's prompt.submit transport-tee). The
    // plugin registers aify.session.render_notice on the gateway; a gateway
    // WITHOUT the plugin answers `unknown method`, so this is best-effort — any
    // failure is swallowed so delivery never regresses on the notice.
    try {
      const sender = String(run?.from || "").trim();
      const subject = String(run?.subject || "").trim();
      const notice = [
        sender ? `Incoming from ${sender}` : "Incoming aify-comms message",
        subject ? `Re: ${subject}` : "",
        "",
        text,
      ]
        .filter((line, idx, arr) => line !== "" || (idx > 0 && arr[idx - 1] !== ""))
        .join("\n")
        .trim();
      await wsClient.request(
        buildRenderNoticeFrame({
          id: id++,
          sessionId,
          notice,
          status: sender ? `aify-comms · ${sender}` : "aify-comms",
        }),
      );
    } catch {
      /* plugin-less gateway or transient render failure — never block delivery */
    }

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
    // Instead OPEN the in-flight re-pulse window (#172): stamp submittedAt so
    // the delivery loop's beat keeps turn_busy fresh past the 120s window for
    // the duration of a long managed turn (bounded by REPULSE_WINDOW_MS so a
    // missed completion can't stick `working` forever). The agent's own reply
    // (require_reply → _mark_dispatch_run_answered) clears it precisely on
    // completion. The blocking hermes-channel.js path uses a promise-anchored
    // beat instead because its chatStream runs the turn to completion inline.
    if (inFlight) {
      inFlight.submittedAt = now();
      inFlight.completed = false;
      // Track WHICH run opened this window so the re-pulse beat can poll that
      // run's status and detect the true turn-end (terminal status) — see
      // runDeliveryLoop's isInFlight probe. (#3)
      inFlight.runId = String(run?.id || "");
      // WS5 Task 5.2: re-arm the gateway idle-after-working guard for THIS turn.
      // The probe only reads `idle` as turn-END once it has observed `working`,
      // so a fresh submit must reset the flag (a stale true from a prior turn
      // could otherwise end the new turn on a momentary post-submit idle).
      inFlight.observedWorking = false;
    }
  } catch (error) {
    // Gateway connect-refused (dead ephemeral port): fail the run with an
    // ACTIONABLE message instead of a raw ECONNREFUSED, and self-correct the
    // agent off `available` (resident-lost) so the next capability computation
    // stops accepting runs against the dead gateway. Narrow classifier — a
    // mid-stream / RPC error falls through to the normal failure path.
    if (isGatewayConnectRefused(error)) {
      const message = gatewayUnreachableMessage(gatewayUrl);
      console.error(`[hermes-managed-host] run ${run?.id || "?"} delivery failed: ${message}`);
      await markRunFailed(httpCall, run, new Error(message)).catch(() => {});
      await reportGatewayDead({ httpCall, agentId, gatewayUrl, reason: message }).catch(() => {});
      if (inFlight) {
        inFlight.submittedAt = 0;
        inFlight.runId = "";
      }
      await clearTurn(httpCall, agentId).catch(() => {});
      return;
    }
    console.error(
      `[hermes-managed-host] run ${run?.id || "?"} delivery failed:`,
      error?.message || String(error),
    );
    await markRunFailed(httpCall, run, error).catch(() => {});
    // Delivery failed → not working. Close the in-flight window and clear the
    // pulse we set above.
    if (inFlight) {
      inFlight.submittedAt = 0;
      inFlight.runId = "";
    }
    await clearTurn(httpCall, agentId).catch(() => {});
  }
}

// Build the re-pulse beat's in-flight probe (#172 + #3 + the 2026-06-02
// false-busy fix). Returned async fn is passed to startInFlightRepulse as
// `isInFlight`; it returns true only when the bounded post-submit window is open
// AND the in-flight run has NOT reached a latch condition. On observing a latch
// condition it flips `inFlight.completed = true` (latching) so this and all
// future ticks stop. shouldLatchComplete latches on:
//   - a TERMINAL run status (completed/failed/cancelled/stopped) — the #3 fix;
//   - `delivered` + require_reply=0 — a delivery-only nudge owes no tracked turn
//     and otherwise lingers `delivered` for 24h, re-pulsing forever (false-busy
//     → blocked queued deliveries + skipped contract reminders).
// It deliberately KEEPS re-pulsing for `delivered` + require_reply=1 (a real
// turn the agent works before self-replying — the #172-safe behavior) and for
// claimed/running. Factored out so the wiring is unit-testable (the beat itself
// is time-driven via setInterval). `serverUrl`, `httpCall`, and `maxWindowMs`
// are injected; `fetchStatus` defaults to the live run reader and returns
// `{ status, requireReply }`.
// WS5 Task 5.2 (event-driven turn-END): in addition to the run-status latch, the
// probe can observe the GATEWAY's own session status (session.active_list →
// `status`, i.e. session["running"]) — the host-observable turn boundary. When the
// gateway reports the agent's `aify-<agent>` session has gone `idle` AFTER it was
// seen `working` (a real turn end), the probe latches completion AND fires the
// authoritative /turn-end (`clearTurnImpl`) so turn_busy clears IMMEDIATELY — the
// 120s TURN_BUSY_STALE_SECONDS window becomes a pure backstop for a DROPPED idle
// observation, not the primary transition (fixes Bug A: finished-but-stuck-working).
//
// SAFETY (anti-feedback-loop, mirrors decideRepulse): this keys on the GATEWAY's
// process truth (session["running"]), NEVER on the aify server's DERIVED status,
// and only ever CLEARS turn_busy (never re-arms it). The idle→end transition is
// gated on having first observed `working` (inFlight.observedWorking) so a
// momentary post-submit idle (before the turn thread flips running=True) cannot
// end the turn early (#172 under-show-working guard). A gateway read error is
// treated as NOT idle (best-effort: keep re-pulsing, fall through to run-status).
// `readGatewayStatus` and `clearTurnImpl` are optional: omitted → the original
// run-status-only behaviour, so existing callers are unaffected.
export function makeInFlightProbe({
  inFlight,
  serverUrl,
  httpCall,
  maxWindowMs = REPULSE_WINDOW_MS,
  fetchStatus = (runId) => fetchRunStatus(httpCall, runId),
  readGatewayStatus = null,
  clearTurnImpl = null,
  // DEBOUNCE (fix/hermes-working-debounce): require N CONSECUTIVE gateway-idle
  // reads before latching the turn-end. The hermes gateway session["running"]
  // flag flips False MID-TURN (between tool calls / generation gaps), so a
  // SINGLE idle read is NOT a real turn boundary — latching on it false-cleared
  // turn_busy mid-turn → the working↔online flap. Any "working" read resets the
  // streak. Tunable; defaults to the flap-safe DEFAULT_IDLE_DEBOUNCE_TICKS.
  idleDebounce = DEFAULT_IDLE_DEBOUNCE_TICKS,
} = {}) {
  const idleThreshold = Math.max(1, Number(idleDebounce) || DEFAULT_IDLE_DEBOUNCE_TICKS);
  return async function isInFlight() {
    if (!serverUrl || !inFlight) return false;
    if (
      !shouldManagedHostRepulse({
        submittedAt: inFlight.submittedAt,
        completed: inFlight.completed,
        maxWindowMs,
      })
    ) {
      return false;
    }
    // Primary turn-END: observe the gateway's own session["running"] state.
    if (typeof readGatewayStatus === "function") {
      let gwStatus = "";
      try {
        gwStatus = String((await readGatewayStatus()) || "");
      } catch {
        gwStatus = ""; // gateway hiccup → treat as not-idle; fall through.
      }
      if (isGatewaySessionWorking(gwStatus)) {
        inFlight.observedWorking = true;
        inFlight.idleStreak = 0; // a working read resets the idle streak (no flap).
      }
      // idle is the turn-end ONLY after we've seen working (submit-race guard)
      // AND only once a SUSTAINED run of idle reads confirms it (debounce). A
      // momentary mid-turn idle blip increments but never reaches the threshold,
      // and the next working read zeroes it — so it can never false-clear.
      if (inFlight.observedWorking && isGatewaySessionIdle(gwStatus)) {
        inFlight.idleStreak = (Number(inFlight.idleStreak) || 0) + 1;
        if (inFlight.idleStreak >= idleThreshold) {
          inFlight.completed = true; // latch: gateway sustained idle → turn ended.
          inFlight.runId = "";
          inFlight.observedWorking = false;
          inFlight.idleStreak = 0;
          if (typeof clearTurnImpl === "function") {
            // Authoritative /turn-end: clear turn_busy NOW, not on the 120s window.
            await clearTurnImpl();
          }
          return false;
        }
        // Below threshold: still in-flight, keep re-pulsing (no premature clear).
        return true;
      }
    }
    // Backstop turn-END: the in-flight run reaching a terminal status (the agent
    // self-replied, or the run failed/cancelled/stopped) — covers a dropped idle.
    const { status, requireReply } = await fetchStatus(inFlight.runId);
    if (shouldLatchComplete({ status, requireReply })) {
      inFlight.completed = true; // latch: observed turn-end → stop the beat.
      inFlight.runId = "";
      return false;
    }
    return true;
  };
}

// The re-pulse PULSE for the managed-host beat. Returns the `pulse` callback for
// startInFlightRepulse. CRITICAL: it threads the OPEN run's id
// (`inFlight.runId`) onto the busy heartbeat. The server heartbeat handler
// OVERWRITES agent_turn_state.turn_run_id from the body on every busy beat, so
// a re-pulse WITHOUT a runId would clear the run linkage on the first tick and
// drop the dashboard's "working on <run>" association mid-turn. `inFlight.runId`
// is stamped on submit and cleared on requeue/completion, so this always
// reflects the run that currently owns the window. Factored out so the wiring
// (runId threaded, not empty) is unit-testable independent of the time-driven
// setInterval beat. `reportTurnBusyImpl` is injectable for tests.
export function makeInFlightPulse({
  httpCall,
  agentId,
  inFlight,
  reportTurnBusyImpl = reportTurnBusy,
} = {}) {
  return async function pulse() {
    await reportTurnBusyImpl(httpCall, agentId, {
      busy: true,
      runId: (inFlight && inFlight.runId) || "",
    });
  };
}

// One poll cycle: claim a small batch of channel/resident runs and deliver each.
// Returns { processed, released, terminal? }. NEVER throws.
//
// TERMINAL self-exit (Task 1.3): a /dispatch/claim 410 (agent intentionally
// removed) — or a 404 sustained past the consecutive-count grace — is a TERMINAL
// condition for the triad's lifecycle owner. Instead of swallowing it (the
// orphan-loop defect #3, where the loop polled a tombstoned agent forever) we
// surface `terminal:"agent-removed"` so runDeliveryLoop breaks, tears down the
// gateway host, and self-exits. Transient WS/connect/RPC/5xx errors are still
// swallowed here so the loop keeps its existing retry behaviour. The
// `claimErrorCounter` (the 404 self-heal counter) is owned by the loop and
// passed in so it persists across poll cycles.
export async function runPollCycle({
  agentId,
  machineId = MACHINE_ID,
  bridgeId = channelBridgeId(agentId),
  httpCall,
  wsClient,
  maxBatch = 20,
  // In-flight tracker passed through to deliverRun so a submitted turn opens
  // the re-pulse window (#172).
  inFlight = null,
  // Gateway WS URL threaded to deliverRun for actionable connect-refused
  // failures + the gateway-dead self-correct.
  gatewayUrl = "",
  // Consecutive-404 self-heal counter (owned by the loop; persists across
  // cycles). `{ count }`. Optional — defaults to a fresh per-cycle counter.
  claimErrorCounter = { count: 0 },
  // Temp dir holding the agent's real-session-id marker (native-session-id
  // model). Threaded to deliverRun so the loop and tests agree on its location.
  tempDir = TMP_DIR,
  // BOUNDED NO-ATTACH FAIL counter (Task 2.3), owned by runDeliveryLoop so it
  // persists across poll cycles. Threaded straight through to deliverRun.
  emptyAttachCounter = new Map(),
  // Attach-window timing + sleep seam, forwarded verbatim to deliverRun. The
  // production loop passes none of these, so they default to the module
  // ATTACH_* constants and the real sleep — behaviour is unchanged. Tests inject
  // a no-op sleep / tiny deadline so the poll-cycle path doesn't wait the full
  // 25s attach window.
  attachWaitMs = ATTACH_WAIT_MS,
  attachPollMs = ATTACH_POLL_MS,
  sleepImpl = sleep,
} = {}) {
  let processed = 0;
  let released = false;
  let terminal = null;
  let claimOk = false;
  try {
    for (let i = 0; i < maxBatch; i++) {
      let claim;
      try {
        claim = await httpCall("POST", "/dispatch/claim", {
          agentId,
          machineId,
          bridgeId,
          // Standalone channel sidecar (NOT a wrapper-PTY child): the service gate
          // accepts a channel-sidecar claim for managed hermes the same way it
          // accepts claude's.
          bridgeKind: "channel-sidecar",
          executionModes: ["channel", "resident"],
        });
        // A successful claim round-trip resets the 404 grace counter and marks
        // the loop a LIVE CLAIMER (drives the ready-marker — Task 1.4).
        claimErrorCounter.count = 0;
        claimOk = true;
      } catch (claimErr) {
        const cls = classifyClaimError(claimErr, claimErrorCounter);
        if (cls.terminal) {
          console.error(
            `[hermes-managed-host] /dispatch/claim ${claimErr?.status} for '${agentId}' is TERMINAL (${cls.reason}); tearing down + exiting.`,
          );
          terminal = cls.reason;
          break;
        }
        // Transient (WS/connect/RPC/5xx, or pre-grace 404): swallow + retry on
        // the next cycle, preserving the loop's existing behaviour.
        console.error("[hermes-managed-host] poll cycle claim error:", claimErr?.message || String(claimErr));
        break;
      }
      // Phase H1 (status v2): the agent was explicitly DISABLED (server returns
      // a terminal `stopped` body on a SUCCESSFUL claim). Treat it like
      // agent-removed (TERMINAL → teardown + procExit(0) in runDeliveryLoop) so
      // the orphan worker + gateway reap themselves. UNLIKE agent-removed,
      // `stopped` is REVERSIBLE: the terminal handler skips the
      // agent-removed-only marker/session-binding clears (gated on
      // reason === "agent-removed"), so a re-enable + relaunch resumes the same
      // session. This is a success-body signal, NOT a claim error, so it is
      // handled here on the success path — not in classifyClaimError.
      if (claim?.stopped) {
        console.error(
          `[hermes-managed-host] agent '${agentId}' is stopped (disabled); helper tearing down + exiting.`,
        );
        terminal = "agent-stopped";
        break;
      }
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
      await deliverRun({ run, agentId, httpCall, wsClient, inFlight, gatewayUrl, tempDir, emptyAttachCounter, attachWaitMs, attachPollMs, sleepImpl });
      processed++;
    }
  } catch (error) {
    console.error("[hermes-managed-host] poll cycle error:", error?.message || String(error));
  }
  return terminal ? { processed, released, terminal, claimOk } : { processed, released, claimOk };
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

// Build a bound teardown callback that kills the gateway host the loop owns —
// by its OWNED CHILD HANDLE when one exists, else BY PORT (a reused host the
// loop attached to via the idempotent probe has child===null but still must be
// killed when this loop is its lifecycle owner). Best-effort + idempotent via a
// shared `state` flag. NEVER throws. `killByPort` defaults to the real
// port→PID→kill from hermes-daemon.js; `clearMarkers` (optional) runs after the
// kill so a teardown also drops the agent's port/key markers (Task 4.1 wiring).
export function makeTeardown({
  gatewayChild = null,
  clearMarkers,
  state = { done: false },
} = {}) {
  return async function teardown() {
    if (state.done) return;
    state.done = true;
    try {
      // Kill the gateway host ONLY if THIS loop itself spawned it (an owned
      // child handle). A REUSED gateway (gatewayChild===null) is the one the
      // wrapper's `ensure-host` started for the VISIBLE TUI — the TUI shares that
      // gateway, so the loop MUST NOT kill it. Port-killing a reused gateway here
      // dropped the TUI's WebSocket ("gateway websocket connection failed",
      // 2026-06-02). A reused/shared gateway is reaped by kill-prior on relaunch
      // and the env-bridge survivor sweep on restart — its lifetime ties to the
      // TUI/console, NOT this loop.
      if (gatewayChild && typeof gatewayChild.kill === "function") {
        gatewayChild.kill("SIGTERM");
      }
    } catch (error) {
      console.error(
        "[hermes-managed-host] gateway-host teardown failed (best-effort):",
        error?.message || String(error),
      );
    }
    if (typeof clearMarkers === "function") {
      try {
        await clearMarkers();
      } catch {
        /* best-effort */
      }
    }
  };
}

// Wire SIGTERM/SIGINT → teardown → exit. When the caller supplies a bound
// `teardown` (the port-aware makeTeardown from runDeliveryLoop, Task 1.2) it is
// used so a SIGTERM kills a reused (child===null) host BY PORT too; otherwise it
// falls back to the legacy child-only teardownGatewayHost. `getChild` returns
// the current gateway-host child (it's spawned after handler install). `proc` is
// injectable for tests.
export function installShutdownTeardown({
  getChild,
  teardown,
  proc = process,
  state = _teardownState,
} = {}) {
  const onSignal = async () => {
    if (typeof teardown === "function") {
      await teardown();
    } else {
      const child = typeof getChild === "function" ? getChild() : null;
      await teardownGatewayHost({ child, state });
    }
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
// Terminal-condition classifier for the delivery loop.
//
// The delivery loop is the triad's lifecycle OWNER: on a terminal condition it
// must break, tear down the gateway host (Task 1.2), and self-exit(0) — never
// swallow the signal and keep polling a dead/removed agent (the orphan-loop
// defect #3). A 410 from /dispatch/claim means the agent was intentionally
// removed (tombstoned) → terminal immediately. A 404 means the server has no
// such agent right now, which is also seen transiently during a service-restart
// window, so it is terminal only AFTER a small consecutive-count grace.
// Everything else (transient WS/connect drops, RPC timeouts, 5xx) is
// NON-terminal: the loop keeps its existing retry behaviour.
// ---------------------------------------------------------------------------

const CLAIM_404_GRACE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_CLAIM_404_GRACE || 3),
);

// Classify a /dispatch/claim error against a small mutable counter object
// `{ count }` (the consecutive-404 self-heal counter). Returns one of:
//   { terminal:true, reason:"agent-removed" }   — 410, or 404 past the grace
//   { terminal:false }                            — transient (retry)
// `grace` is injectable for tests.
export function classifyClaimError(err, counter = { count: 0 }, { grace = CLAIM_404_GRACE } = {}) {
  const status = Number(err?.status);
  if (status === 410) {
    return { terminal: true, reason: "agent-removed" };
  }
  if (status === 404) {
    counter.count = (counter.count || 0) + 1;
    if (counter.count >= grace) {
      return { terminal: true, reason: "agent-removed" };
    }
    return { terminal: false };
  }
  // Any non-404 success or non-terminal error resets the 404 grace counter.
  counter.count = 0;
  return { terminal: false };
}

// ---------------------------------------------------------------------------
// Delivery loop (the `run` CLI mode).
//
// runDeliveryLoop is the SINGLE lifecycle owner of the managed-hermes triad.
// Its inline loop intentionally carries the full production lifecycle, which has
// no clean home in a generic seam:
//   1. Register the channel-sidecar liveness heartbeat FIRST (before any gateway
//      await) so a fresh hermes never shows `online`/`available` with no live
//      claimer (defect #2).
//   2. Bring the gateway up inside a BOUNDED-RETRY path — a transient gateway
//      failure is non-fatal and never calls process.exit synchronously.
//   3. Run the reactive + proactive gateway-dead self-correct, the in-flight
//      re-pulse beat, runPollCycle (which classifies terminal/release/claimOk by
//      RETURN, not by throw), the loop-ready marker, and procExit(0) on terminal.
// These concurrent lifecycles (probe/re-pulse/connect-refused latch) and the
// return-based terminal contract are why the loop is NOT factored behind a
// generic claimOnce/onReady seam — doing so would require shim adapters + dead
// hooks (the exact test/prod drift such a seam is meant to prevent). The
// liveness-first / bounded-retry / terminal-exit / release-teardown / transient
// behaviors are unit-tested directly against runDeliveryLoop below.
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
    // Terminal-exit hook (Task 1.3). On a terminal /dispatch/claim signal (410
    // agent-removed) the loop tears down + self-exits(0). Injectable so tests
    // never actually exit the test process.
    procExit = (code) => process.exit(code),
    // Temp dir holding the agent's markers (port/key/daemon-pid/loop-ready).
    // Injectable so the ready-marker tests don't touch the real tmp dir.
    markerDir = TMP_DIR,
    // Ready-marker writer/clearer (Task 1.4). Injectable for tests.
    writeReady = writeLoopReady,
    clearReady = clearLoopReady,
    // Marker cleanup run on teardown. Clears the loop-ready marker (Task 1.4);
    // Task 4.1 extends this with the port/key marker clear. Injectable override.
    clearMarkers,
    // Port/key gateway-marker clear used by the default teardown cleanup (Task
    // 4.1). The delivery-loop teardown is the TERMINAL path (410 agent-removed /
    // dead gateway / release / SIGTERM) — NOT a transient gateway retry — so it
    // is correct to drop the agent's stable port + key markers here. Injectable.
    clearGatewayMarkers = defaultClearGatewayMarkers,
    // PERSISTENT session-id marker clear (2026-06-03). Distinct from
    // clearGatewayMarkers (which no longer touches the session marker): the
    // agent→real-session binding must survive a relaunch but be removed on a
    // TERMINAL agent removal (410) so a deleted agent leaves no stale session
    // binding behind. Called ONLY in the agent-removed branch below — NEVER on a
    // released/relaunch teardown. Injectable so tests assert it.
    clearSessionMarker = defaultClearSessionMarker,
    // Port→PID→kill primitive for a reused (child===null) gateway host (Task
    // 1.2). Injectable so tests assert the port-kill without touching a process.
    killByPort = defaultKillByPort,
    // Liveness-heartbeat factory (Task 1.1). Injectable so tests can assert the
    // beat is registered BEFORE the gateway bring-up; production uses the real
    // unconditional beat from liveness-heartbeat.js.
    startLivenessHeartbeat: startLivenessHeartbeatImpl = startLivenessHeartbeat,
    // NO-TUI TEARDOWN BACKSTOP threshold (FIX SET A2). Defaults to the env-overridable
    // module constant; injectable so tests can drive the backstop deterministically
    // without depending on the frozen-at-load env value (mirrors emptyAttachFailThreshold).
    noTuiTeardownCycles = NO_TUI_TEARDOWN_CYCLES,
    // NO-TUI COLD-START GRACE (FIX, 2026-06-03). Grace window during which an empty
    // active_list does NOT count toward the no-TUI teardown if the TUI has never yet
    // attached (the loop is spawned before the visible `hermes --tui` attaches). Once
    // the TUI is first seen attached, the latch (hasSeenAttachedTui) makes empties
    // count immediately regardless of grace. Injectable like noTuiTeardownCycles.
    noTuiGraceMs = NO_TUI_GRACE_MS,
    // Clock seam for the cold-start grace (loopStartedAt is captured from this).
    // Injectable so tests can drive the grace window deterministically.
    now = Date.now,
  } = deps;
  const id = String(agentId || "").trim();
  // Cold-start grace anchor: the moment the delivery loop began. An empty
  // active_list before (now() - loopStartedAt) > noTuiGraceMs — and before the TUI
  // has EVER attached — is the expected cold-start window, not a dead launch.
  const loopStartedAt = now();
  if (!id) {
    console.error("[hermes-managed-host] run: no bound agentId; nothing to drive.");
    return { released: false, processed: 0 };
  }
  const port = await resolveGatewayPort(id, { tempDir: TMP_DIR });

  // Task 1.1: register the channel-sidecar liveness heartbeat BEFORE the gateway
  // bring-up so the agent's bridge_instances row exists before any gateway await.
  // A fresh hermes can therefore never show `online`/`available` with NO live
  // claimer (defect #2). Stopped on either return path via the finally below.
  const stopLiveness = startLivenessHeartbeatImpl({
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

  // Teardown state shared between the SIGTERM handler and the terminal/release
  // self-exit so teardown runs at most once. `makeTeardown` kills the gateway
  // host ONLY when this loop SPAWNED it (an owned child handle). A REUSED gateway
  // (child===null, started by the wrapper for the visible TUI) is shared with the
  // TUI and is NEVER killed by the loop (2026-06-02 fix — killing it dropped the
  // TUI's WebSocket). The reused gateway is reaped by kill-prior on relaunch /
  // the env-bridge survivor sweep on restart, keyed off its persisted port marker.
  const teardownState = { done: false };
  let gatewayChild = null;
  // WS5 Task 5.1 (2026-06-02): the explicit claimer lease. POST `acquire` once
  // the loop is a live claimer (same point as the loop-ready marker) and
  // `release` in the terminal teardown. The lease is the server's POSITIVE
  // "a loop is a live claimer right now" signal that lets a send to a deaf
  // managed target fail fast (a released/stale lease ⇒ deaf) without breaking
  // lazy delivery (no lease ever ⇒ fall back). Best-effort/no-throw — a lease
  // POST failure must never crash the delivery loop. Only when serverUrl is set
  // and the lease was actually acquired do we bother releasing.
  let claimerLeaseAcquired = false;
  const postClaimerLease = async (action) => {
    if (!serverUrl) return;
    try {
      await httpCall("POST", `/agents/${encodeURIComponent(id)}/claimer-lease`, {
        action,
        bridgeId: channelBridgeId(id),
      });
      if (action === "acquire") claimerLeaseAcquired = true;
    } catch {
      /* best-effort: never throw from the lease post */
    }
  };
  // On teardown, drop the loop-ready marker (Task 1.4) so the wrapper's
  // health-gate never sees a stale "live claimer" for a torn-down loop. An
  // explicit clearMarkers override (Task 4.1 port/key clear) wins when provided.
  const effClearMarkers =
    typeof clearMarkers === "function"
      ? clearMarkers
      : async () => {
          // Terminal teardown: release the claimer lease (WS5 Task 5.1) so the
          // agent is IMMEDIATELY not-deliverable, and drop the loop's OWN
          // loop-ready marker (Task 1.4). Do NOT clear the gateway port/key
          // markers here (2026-06-02): they tie to the gateway host — which the
          // loop no longer kills — and kill-prior needs the persisted PORT marker
          // to reap that gateway on the next relaunch. Clearing them while the
          // gateway+TUI are still alive caused port-drift. Release even if the
          // acquire post failed, so a partial acquire never strands a phantom lease.
          await postClaimerLease("release");
          clearReady(id, markerDir);
        };
  const teardown = () =>
    makeTeardown({
      gatewayChild,
      clearMarkers: effClearMarkers,
      state: teardownState,
    })();
  installTeardown({ getChild: () => gatewayChild, teardown });

  const spawn = spawnImpl || (await import("node:child_process")).spawn;
  // Task 1.1: bring the gateway up inside a BOUNDED-RETRY path. A transient
  // failure (e.g. "index token timeout") is NON-fatal — the loop keeps its
  // liveness heartbeat and retries, rather than the old pre-loop throw →
  // process.exit(1) that left the agent `online` with no claimer (defect #2).
  // Idempotent: if the wrapper's `ensure-host` already started it, probeFirst
  // reuses it (child=null); otherwise we (re)spawn it ourselves.
  let host = null;
  for (let attempt = 0; maxIterations === undefined || attempt < maxIterations; attempt++) {
    try {
      host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl });
      break;
    } catch (error) {
      console.error(
        `[hermes-managed-host] gateway bring-up failed for '${id}' (attempt ${attempt + 1}); retrying:`,
        error?.message || String(error),
      );
      await sleepImpl(POLL_MS);
    }
  }
  if (!host) {
    // Exhausted the (test-bound) retry budget without a gateway. Not a terminal
    // removal; stop the heartbeat and return cleanly.
    stopLiveness();
    return { released: false, processed: 0 };
  }
  gatewayChild = host.child;
  // `host.child` is non-null ONLY when THIS loop spawned the gateway; on the
  // reused path (the wrapper/TUI's gateway) it is null, so the loop's teardown
  // leaves that shared gateway alone (2026-06-02 fix).

  let wsClient = null;
  const ensureWs = async () => {
    if (wsClient) return wsClient;
    wsClient = await openWs(host.wsUrl);
    return wsClient;
  };
  // Latch so the gateway-dead self-correct fires AT MOST once per loop lifetime —
  // a refused connect repeats every poll until the agent is torn down, and we
  // must not spam resident-lost (which the server treats as a state transition).
  // SHARED between the REACTIVE path (a run's connect-refusal, below) and the
  // PROACTIVE periodic probe (started further down) so the two never both fire.
  let gatewayDeadReported = false;
  const reportGatewayDeadOnce = async (reason) => {
    if (gatewayDeadReported) return;
    gatewayDeadReported = true;
    console.error(`[hermes-managed-host] ${reason || gatewayUnreachableMessage(host.wsUrl)}`);
    await reportGatewayDead({
      httpCall,
      agentId: id,
      gatewayUrl: host.wsUrl,
      reason,
    }).catch(() => {});
  };

  // PROACTIVE gateway-liveness probe (status-liveness, 2026-06-02). Every
  // GATEWAY_PROBE_MS, probe the gateway HOST's dashboard index for
  // reachability; after GATEWAY_PROBE_THRESHOLD consecutive failures, report the
  // gateway dead ONCE (resident-lost) so the agent stops showing `available`
  // even when NO run is pending (the reactive deliverRun path only triggers on
  // an actual claimed run). Debounced by the threshold so a single slow/transient
  // probe never flaps a healthy agent. Unref'd timer; swallows probe errors.
  const stopGatewayProbe = startGatewayLivenessProbe({
    intervalMs: GATEWAY_PROBE_MS,
    threshold: GATEWAY_PROBE_THRESHOLD,
    probe: makeGatewayReachabilityProbe({
      indexUrl: gatewayIndexUrlFromWs(host.wsUrl),
      fetchImpl,
    }),
    reportDead: async ({ consecutiveFailures } = {}) => {
      if (!serverUrl) return;
      await reportGatewayDeadOnce(
        `Hermes gateway unreachable at ${host.wsUrl} after ${consecutiveFailures} consecutive ` +
          `liveness probes; the gateway host likely died. Self-correcting off 'available' (resident-lost).`,
      );
    },
  });

  // (The unconditional liveness beat is started BEFORE the gateway bring-up
  // above — Task 1.1 — so the bridge row exists before any gateway await.)

  // In-flight turn re-pulse (#172): a single tracker for this agent. deliverRun
  // stamps `submittedAt` on a successful fire-and-forget prompt.submit; this
  // beat re-pulses turn_busy while shouldManagedHostRepulse says the bounded
  // window is open, so a managed-hermes turn longer than the server's 120s
  // TURN_BUSY_STALE_SECONDS keeps showing `working`. Anchored on the
  // bridge-owned submit timestamp + hard window cap — NEVER the server's
  // derived status (anti-feedback-loop; mirrors claude decideRepulse).
  const inFlight = { submittedAt: 0, completed: false, runId: "", observedWorking: false };
  // WS5 Task 5.2 (event-driven turn-END): read the gateway's OWN session status
  // (session.active_list → `status`, i.e. session["running"]) for this agent's
  // `aify-<agent>` session. This is the host-observable turn boundary the re-pulse
  // probe uses to clear turn_busy the instant a turn ends — demoting the 120s
  // server window to a backstop. Uses the CURRENT wsClient (re-created each tick);
  // best-effort: a missing WS / RPC error returns "" (read as not-idle, so the
  // turn is never ended early on a transient gateway hiccup).
  // Native-session-id model (2026-06-03): the turn-state detector reads the
  // gateway session status for the agent's OWN real session (resumed at launch /
  // captured at register), looked up by its real id. The synthetic `aify-<id>`
  // key is the LEGACY fallback only — used until the real id binds (a fresh agent
  // that hasn't been captured yet) so an old key-titled session still reports
  // status. The real id is re-read each tick because the delivery loop's
  // most-recent fallback may bind/refresh the marker between ticks.
  const managedSessionKey = sessionKeyFor(id);
  let statusRpcId = 700000;
  const readManagedSessionStatus = async () => {
    if (!wsClient) return "";
    const listResp = await wsClient.request(
      buildSessionActiveListFrame({ id: statusRpcId++, currentSessionId: "" }),
    );
    let realId = "";
    try {
      realId = String(readSessionIdMarker(id, { tempDir: markerDir }) || "").trim();
    } catch {
      realId = "";
    }
    if (realId) {
      const byId = pickSessionStatusById(listResp, realId);
      if (byId) return byId;
    }
    // Not bound yet (or the bound session isn't live): fall back to the legacy
    // synthetic-key title match so a pre-native session still reports status.
    return pickSessionStatusForKey(listResp, managedSessionKey);
  };
  // NO-TUI TEARDOWN BACKSTOP (FIX SET A2). Count the gateway host's ATTACHED
  // sessions via session.active_list. A visible TUI (or any non-loop WS client)
  // attaching to the gateway puts at least one row in active_list; ZERO rows means
  // no TUI is attached (the SIGKILL'd-terminal orphan case the A1 trap can't catch).
  // Returns the row count, or -1 on any RPC/parse error so a transient gateway
  // hiccup is NOT counted as "empty" (never tear down on a flaky read). Reuses the
  // SAME active_list machinery as readManagedSessionStatus.
  let attachRpcId = 750000;
  const countAttachedSessions = async () => {
    if (!wsClient) return -1;
    try {
      const listResp = await wsClient.request(
        buildSessionActiveListFrame({ id: attachRpcId++, currentSessionId: "" }),
      );
      return activeListRowsLocal(listResp).length;
    } catch {
      return -1;
    }
  };
  // Consecutive poll cycles observed with ZERO attached sessions. Promoted to a
  // LOOP-LEVEL resident-lost signal (independent of any pending run) so a hard
  // terminal kill that bypasses the A1 trap still self-tears-down.
  let noTuiCycles = 0;
  // COLD-START LATCH (FIX, 2026-06-03): have we EVER seen the TUI attached? Until we
  // have (and before the cold-start grace elapses), an empty active_list is the
  // expected pre-attach window — NOT a dead launch — so it must not count toward the
  // no-TUI teardown. Set true the first time countAttachedSessions() > 0.
  let hasSeenAttachedTui = false;
  const stopRepulse = startInFlightRepulse({
    intervalMs: REPULSE_MS,
    isInFlight: makeInFlightProbe({
      inFlight,
      serverUrl,
      httpCall,
      maxWindowMs: REPULSE_WINDOW_MS,
      // The gateway turn-END observer + the authoritative clear. clearTurn POSTs
      // /turn-end (turn_busy=0) — only ever CLEARS, keyed on the gateway's process
      // truth, never the aify server's derived status (anti-feedback-loop safe).
      readGatewayStatus: readManagedSessionStatus,
      clearTurnImpl: () => clearTurn(httpCall, id).catch(() => {}),
    }),
    // Thread the in-flight runId so the server heartbeat handler keeps
    // turn_run_id pointing at the open run (it OVERWRITES turn_run_id from the
    // body on every busy beat). Omitting it cleared the run linkage on the
    // first re-pulse, dropping the dashboard's "working on <run>" association
    // mid-turn. See makeInFlightPulse.
    pulse: makeInFlightPulse({ httpCall, agentId: id, inFlight }),
  });

  // Continuous, bidirectional gateway turn-state detector
  // (fix/hermes-working-debounce). The in-flight re-pulse above is the DISPATCH
  // instant path (only armed inside a dispatch's submit window). This detector is
  // the CONTINUOUS backstop in BOTH directions, running for the whole loop
  // lifetime independent of any dispatch — so an AUTONOMOUS / direct-typed-in-the
  // -TUI turn (which never stamps inFlight.submittedAt) is still reflected as
  // `working` (#172), AND the debounced idle→end transition prevents the mid-turn
  // -idle flap. ANTI-FEEDBACK-LOOP: it keys ONLY on the gateway's session
  // ["running"] truth via readManagedSessionStatus, never the aify server's
  // derived status; /turn-start is edge-triggered (set once per turn, no per-tick
  // spam). The 120s server window remains the long backstop for a dropped tick.
  const stopGatewayTurnDetector = startHermesGatewayTurnDetector({
    intervalMs: GATEWAY_TURN_POLL_MS,
    idleDebounce: GATEWAY_TURN_IDLE_DEBOUNCE,
    readGatewayStatus: readManagedSessionStatus,
    // SET working on a gateway-running turn (edge-triggered). busy:true POSTs the
    // turn-start heartbeat; no runId because an autonomous turn has no aify run.
    postTurnStart: () => reportTurnBusy(httpCall, id, { busy: true }).catch(() => {}),
    // CLEAR on sustained idle — authoritative /turn-end, only ever clears.
    postTurnEnd: () => clearTurn(httpCall, id).catch(() => {}),
  });

  let totalProcessed = 0;
  // Consecutive-404 self-heal counter (Task 1.3) — persists across poll cycles
  // so a 404 that survives the grace window terminates the loop.
  const claimErrorCounter = { count: 0 };
  // BOUNDED NO-ATTACH FAIL counter (Task 2.3) — persists across poll cycles so a
  // run that the visible TUI never attaches for is FAILED after the bounded budget
  // instead of requeued forever. Owned here (one map per loop lifetime), threaded
  // through runPollCycle → deliverRun. Cleared per-run on a successful attach.
  const emptyAttachCounter = new Map();
  try {
    for (let iter = 0; maxIterations === undefined || iter < maxIterations; iter++) {
      try {
        if (!serverUrl) {
          await sleepImpl(POLL_MS);
          continue;
        }
        let connectErr = null;
        const ws = await ensureWs().catch((err) => {
          connectErr = err;
          console.error("[hermes-managed-host] WS connect failed:", err?.message || String(err));
          return null;
        });
        if (!ws) {
          // Dead ephemeral gateway port (the resident-run liveness gap): the
          // agent shows `available` but the gateway host is gone. Self-correct
          // ONCE so the dispatcher stops accepting runs against the dead port,
          // rather than waiting out the ~150s heartbeat lease.
          if (isGatewayConnectRefused(connectErr)) {
            await reportGatewayDeadOnce(gatewayUnreachableMessage(host.wsUrl));
          }
          await sleepImpl(POLL_MS);
          continue;
        }
        const result = await runPollCycle({
          agentId: id,
          httpCall,
          wsClient: ws,
          inFlight,
          gatewayUrl: host.wsUrl,
          claimErrorCounter,
          tempDir: markerDir,
          emptyAttachCounter,
        });
        totalProcessed += result.processed || 0;
        // NO-TUI TEARDOWN BACKSTOP (FIX SET A2). After a successful poll cycle the
        // gateway WS is live, so a TRUSTWORTHY active_list read is available. Count
        // attached sessions: zero means no visible TUI (the SIGKILL'd-terminal orphan
        // the A1 trap can't reach — the gateway host survived but nothing is viewing
        // it). Accumulate consecutive empties; a single empty (or any attached
        // session) resets the counter so a brief relaunch detach never trips it. On a
        // read error (-1) leave the counter unchanged (treat a flaky read as neither
        // empty nor attached). After NO_TUI_TEARDOWN_CYCLES sustained empties, promote
        // to resident-lost: reportGatewayDeadOnce flips the agent off `available` and
        // teardown reaps the gateway host this loop owns, then self-exit so the
        // orphaned-but-reachable gateway can no longer keep the agent looking online.
        const attachedCount = await countAttachedSessions();
        // COLD-START GRACE (FIX, 2026-06-03): an empty active_list counts toward the
        // no-TUI teardown ONLY once the TUI has attached at least once (latch) OR the
        // cold-start grace window has elapsed. Before then it's the expected pre-attach
        // window (the loop is spawned before the visible `hermes --tui` attaches), so a
        // slow first-launch TUI build on a loaded host is never torn down prematurely.
        const coldStartGraceElapsed = now() - loopStartedAt > noTuiGraceMs;
        if (attachedCount === 0 && (hasSeenAttachedTui || coldStartGraceElapsed)) {
          noTuiCycles += 1;
          if (noTuiCycles >= noTuiTeardownCycles) {
            await reportGatewayDeadOnce(
              `Hermes gateway at ${host.wsUrl} has had NO attached session (no visible TUI / ` +
                `non-loop WS client) across ${noTuiCycles} consecutive poll cycles; the operator's ` +
                `terminal was likely closed/killed. Self-correcting off 'available' (resident-lost) ` +
                `and reaping the orphaned gateway host.`,
            );
            stopLiveness();
            stopRepulse();
            stopGatewayTurnDetector();
            stopGatewayProbe();
            await teardown();
            return { released: false, processed: totalProcessed, residentLost: true };
          }
        } else if (attachedCount > 0) {
          // The TUI is attached: latch it (so future empties count immediately) and
          // reset the empty streak (a brief relaunch detach never trips teardown).
          hasSeenAttachedTui = true;
          noTuiCycles = 0;
        }
        // attachedCount === -1 (read error) and the in-grace empty case both leave
        // noTuiCycles unchanged — a flaky read or a cold-start pre-attach empty is
        // neither a confirmed attach nor a teardown-worthy empty.
        // Task 1.4: the loop is now a LIVE CLAIMER — gateway ok + heartbeat
        // started (above) + a successful /dispatch/claim round-trip (even with 0
        // runs). Write/refresh the ready marker the wrapper health-gates on so a
        // visible TUI is only exec'd once work can actually be delivered. Refresh
        // on EVERY successful claim so the marker's mtime stays fresh.
        if (result.claimOk) {
          writeReady(id, markerDir);
          // WS5 Task 5.1: the same readiness gate also acquires/refreshes the
          // explicit claimer lease — the server's positive "live claimer now"
          // signal. Refresh on EVERY successful claim so the lease's freshness
          // tracks the loop's liveness (the release-on-teardown is what makes a
          // dead loop deaf immediately; this refresh backstops a missed release).
          await postClaimerLease("acquire");
        }
        // Task 1.3: a TERMINAL claim signal (410 agent-removed / graced 404) is
        // the lifecycle owner's exit cue. Tear down the gateway host (kills by
        // child or by PORT — Task 1.2), clear markers, and self-exit(0) so a
        // tombstoned agent's loop CANNOT keep polling forever (defect #3).
        if (result.terminal) {
          stopLiveness();
          stopRepulse();
          stopGatewayTurnDetector();
          stopGatewayProbe();
          await teardown();
          // Marker hygiene (fix/hermes-leak P4): the default teardown
          // intentionally PRESERVES the gateway port/key markers so kill-prior
          // can reap the shared gateway on a relaunch. But agent-removed (410)
          // is TERMINAL — the agent is tombstoned and will NOT relaunch — so the
          // port/key markers are now dead weight that the env-bridge boot sweep
          // would keep re-finding. Clear them ONLY for agent-removed (a transient
          // `released`/dead-gateway teardown still keeps them for relaunch).
          if (result.terminal === "agent-removed") {
            try { clearGatewayMarkers(id, markerDir); } catch { /* best effort */ }
            // FIX 3 (2026-06-03): the agent is TERMINALLY removed (tombstoned, no
            // relaunch), so its persistent agent→real-session binding is now dead
            // weight — clear it too. This is the ONLY teardown path that drops the
            // session marker; a released/relaunch teardown deliberately keeps it so
            // the next launch resumes the SAME transcript.
            try { clearSessionMarker(id, markerDir); } catch { /* best effort */ }
          }
          console.error(
            `[hermes-managed-host] agent '${id}' ${result.terminal}; gateway torn down, exiting.`,
          );
          procExit(0);
          return { released: false, processed: totalProcessed, terminal: result.terminal };
        }
        if (result.released) {
          await teardown();
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
    stopRepulse();
    stopGatewayTurnDetector();
    stopGatewayProbe();
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
  // DEAD PRE-SEED (2026-06-03 cleanup): the synthetic `aify-<id>` SessionDB row is
  // no longer resumed — the native-session-id model resumes the agent's REAL
  // session id (the marker), so ensureStableSession only littered
  // `hermes sessions list` with an orphan `aify-<id>` row on EVERY launch. It is
  // now OFF by default; the `ensureSession === true` seam is retained as an
  // explicit opt-in (and so the existing `ensureSession:false` test stays green).
  if (deps.ensureSession === true) {
    ensureStableSession({ agentId: id, spawnSync: deps.spawnSyncImpl });
  }
  const spawn = spawnImpl || (await import("node:child_process")).spawn;
  const host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl });
  // Persist the gateway URL in an AGENT-KEYED marker so the in-session MCP
  // bridge (server.js) can auto-register the gateway even though its env only
  // ever has the unresolved `${AIFY_HERMES_GATEWAY_URL}` placeholder — the
  // gateway host can't inject its own URL into the MCP child's env at spawn
  // time. Without this, every agent depended on either a cwd-keyed marker
  // (collides for same-folder agents) or the agent hand-rolling registration.
  writeGatewayUrlMarker(id, host.wsUrl, {
    gatewayTokenEnv: process.env.AIFY_HERMES_GATEWAY_TOKEN_ENV || "",
    tempDir: TMP_DIR,
  });
  // The gateway host must OUTLIVE this short-lived CLI process (the delivery
  // loop + the visible TUI attach to it). It was spawned detached+unref'd.
  // NOTE: the legacy `resumeKey` (synthetic `aify-<id>` name) is no longer
  // emitted — the native-session-id model resumes the agent's REAL session id, so
  // the wrapper no longer consumes a synthetic resume key (install.sh guards on
  // its presence and falls back when absent). Dropped to stop advertising a dead
  // field (2026-06-03 cleanup).
  const payload = {
    port: host.port,
    token: host.token,
    wsUrl: host.wsUrl,
  };
  out(JSON.stringify(payload) + "\n");
  err(`[hermes-managed-host] gateway host ready for '${id}' on port ${host.port}\n`);
  return payload;
}

// ---------------------------------------------------------------------------
// `resolve-session` CLI mode — LAUNCH-SIDE session convergence (FIX C, 2026-06-03).
// ---------------------------------------------------------------------------
//
// The three-id desync bug: at launch the wrapper resumed `--resume <marker>` but
// the agent-keyed marker could be DAYS stale, so the visible TUI viewed a dead/old
// session while the agent's real work landed in a gateway-host session the TUI
// never viewed. The delivery loop already self-heals the marker from the active_list
// fallback — but the VISIBLE TUI is exec'd ONCE at launch and never re-resolved, so
// it stays pointed at the stale id.
//
// This verb makes LAUNCH agree with the loop by querying the gateway's GROUND TRUTH
// (`session.active_list`) and resolving the freshest live session, so the visible
// TUI resumes the SAME session the loop will target. Resolution:
//   (a) the marker id, IF it is still a live row in active_list (continuous
//       transcript — prefer the bound session when it's actually attached);
//   (b) else the gateway's MOST-RECENT live session (the freshest real id);
//   (c) else "" (no live session yet — first launch / cold gateway → the wrapper
//       starts a FRESH session and the bridge captures its real id on register).
// On (a)/(b) it PERSISTS the resolved id to the marker (best-effort) and seeds the
// per-agent active-session file so the in-session bridge reads a real handle even
// before the TUI writes its own. Prints the resolved id (or empty) as ONE line.
//
// ASYMMETRY(hermes): a hermes TUI can still CREATE or SWITCH sessions AFTER launch
// (e.g. the operator starts a new chat). That post-launch switch is tracked by the
// TUI's own writeActiveSessionFile() into HERMES_TUI_ACTIVE_SESSION_FILE (the plugin
// pins that env at the stable per-agent path and prevents its deletion), and the
// delivery loop re-resolves the freshest live session on EVERY delivery — so the
// loop converges on the new session even though the launch-time resume id is now
// historical. Launch resolves the BEST id known at launch; runtime convergence is
// owned by the loop + the TUI's active-file writes, not by re-exec'ing the TUI.
export async function runResolveSessionCli(agentId, deps = {}) {
  const {
    out = (s) => process.stdout.write(s),
    err = (s) => process.stderr.write(s),
    // Injectable seams for tests.
    openClient,
    readMarker = readSessionIdMarker,
    writeMarker = writeSessionIdMarker,
    writeActiveSessionFile = defaultWriteActiveSessionFile,
    tempDir = TMP_DIR,
    activeSessionFile = String(process.env.AIFY_HERMES_ACTIVE_SESSION_FILE || "").trim(),
    // EXPLICIT-RESUME mode (BUG 2, 2026-06-03): when the operator passes
    // `hermes-aify --resume <id>`, <id> is AUTHORITATIVE. We SKIP the gateway
    // active_list query entirely and just SEED the per-agent active-session file
    // + overwrite the session marker with <id>, so the in-session bridge's
    // discoverSessionId reads <id> (primary: active-file) and the stale marker
    // can never override it. This guarantees the registered handle == the visible
    // TUI's resumed session, instead of falling through to a stale marker.
    explicitId = "",
  } = deps;
  const id = String(agentId || "").trim();
  if (!id) throw new Error("resolve-session requires an agentId");

  // EXPLICIT-RESUME short-circuit: an operator-supplied id wins unconditionally.
  // Seed the active-session file + marker and print it; no gateway round-trip.
  const explicit = String(explicitId || "").trim();
  if (explicit) {
    try { writeMarker(id, explicit, { tempDir }); } catch { /* best-effort */ }
    if (activeSessionFile) {
      try { writeActiveSessionFile(activeSessionFile, explicit); } catch { /* best-effort */ }
    }
    err(`[hermes-managed-host] resolve-session: agent '${id}' → ${explicit} (explicit-resume; seeded marker + active file).\n`);
    out(explicit + "\n");
    return { agentId: id, resolved: explicit, source: "explicit-resume" };
  }

  // Read the gateway URL the wrapper already resolved + exported (ensure-host ran
  // first). Without a gateway we cannot query ground truth → emit empty (the
  // wrapper then resumes the bare marker / starts fresh, same as before).
  const wsUrl = String(
    deps.gatewayUrl || process.env.AIFY_HERMES_GATEWAY_URL || process.env.HERMES_TUI_GATEWAY_URL || "",
  ).trim();
  const marker = (() => {
    try {
      return String(readMarker(id, { tempDir }) || "").trim();
    } catch {
      return "";
    }
  })();

  if (!wsUrl) {
    // No gateway to consult — fall back to the marker as-is (best we know).
    out((marker || "") + "\n");
    return { agentId: id, resolved: marker || "", source: marker ? "marker(no-gateway)" : "none" };
  }

  let client = null;
  let resolved = "";
  let source = "none";
  try {
    client = openClient
      ? await openClient(wsUrl)
      : await openGatewayWsClient(wsUrl);
    let rid = 1;
    const listResp = await client.request(
      buildSessionActiveListFrame({ id: rid++, currentSessionId: "" }),
    );
    // (a) prefer the marker id when it is a LIVE row (continuous transcript).
    if (marker && pickSessionById(listResp, marker)) {
      resolved = marker;
      source = "marker(live)";
    } else {
      // (b) most-recent live session — the gateway's freshest real id.
      const recent = pickMostRecentSession(listResp);
      if (recent) {
        resolved = recent;
        source = "active_list(most-recent)";
      }
    }
  } catch (e) {
    err(`[hermes-managed-host] resolve-session: active_list query failed (${e?.message || e}); falling back to marker.\n`);
    resolved = marker || "";
    source = marker ? "marker(query-failed)" : "none";
  } finally {
    try { client?.close?.(); } catch { /* ignore */ }
  }

  if (resolved) {
    // Converge launch == loop == marker == active-session file. Best-effort.
    if (resolved !== marker) {
      try { writeMarker(id, resolved, { tempDir }); } catch { /* best-effort */ }
    }
    if (activeSessionFile) {
      try { writeActiveSessionFile(activeSessionFile, resolved); } catch { /* best-effort */ }
    }
    err(`[hermes-managed-host] resolve-session: agent '${id}' → ${resolved} (${source}).\n`);
  } else {
    err(`[hermes-managed-host] resolve-session: agent '${id}' has no live gateway session yet (will start fresh).\n`);
  }
  out((resolved || "") + "\n");
  return { agentId: id, resolved: resolved || "", source };
}

// ---------------------------------------------------------------------------
// argv dispatch.
// ---------------------------------------------------------------------------

// Dispatch on argv. Modes:
//   ensure-host <agentId> → runEnsureHostCli (prints JSON line, exits 0)
//   resolve-session <id>  → runResolveSessionCli (prints the freshest live id)
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
  if (mode === "resolve-session") {
    const agentId = String(argv[1] || "").trim() || readBoundAgentId();
    // Optional `--explicit <id>` (BUG 2): an operator `--resume <id>` is
    // AUTHORITATIVE — seed the active-file + marker with <id>, skip the gateway
    // query. Parsed from argv so the wrapper's explicit-resume branch can call
    // `resolve-session <agentId> --explicit <id>`.
    let explicitId = "";
    for (let i = 2; i < argv.length; i++) {
      const a = String(argv[i] || "");
      if (a === "--explicit") {
        explicitId = String(argv[i + 1] || "").trim();
        i++;
      } else if (a.startsWith("--explicit=")) {
        explicitId = a.slice("--explicit=".length).trim();
      }
    }
    await runResolveSessionCli(agentId, { ...deps, explicitId: deps.explicitId ?? explicitId });
    return { mode: "resolve-session", agentId };
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
