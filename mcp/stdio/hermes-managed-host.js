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
//      `aify-<agentId>` key. Then `prompt.submit {session_id, text}`. Events route
//      to the TUI's transport
//      (owner) so the TUI renders; aify's submit does NOT displace it. A 4009
//      explicit queue waits for turn-end; ordinary busy sends use session.steer.
//
// WAKE-ONLY (symmetric with claude-channel.js): this helper authors NO reply.
// The in-session hermes agent has the aify-comms comms_* tools loaded and
// self-replies via comms_send + inReplyTo, which closes the require_reply run.
// After a successful submit the run is left `delivered`.
//
// The real session id is re-discovered every delivery (the most-recent fallback
// may rebind it), so it is NEVER cached — every delivery re-runs
// `session.active_list`.

import path from "path";
import { fileURLToPath } from "url";
import { loadSettingsEnv } from "./load-env.js";
import { readAgentBindingFile } from "./binding-file.js";
import {  // v0.5.4: neutral owner
  TMP_DIR,
} from "./hermes-env.mjs";
import {  // v0.5.4: moved out; the host is now a CALLER of the session module
  ensureStableSession,
  runResolveSessionCli,
} from "./hermes-active-session.mjs";
import {  // v0.5.4: moved out; this file is now a CALLER of the gateway module
  ensureGatewayHost,
  openGatewayWsClient,
} from "./hermes-gateway.mjs";
import {
  resolveGatewayPort,
  writeGatewayUrlMarker,
} from "./hermes-endpoint.js";
// v0.5.4: the delivery loop and the per-run work moved to ./hermes-delivery-loop.mjs and
// ./hermes-delivery-run.mjs — 998 lines together, which one module could not hold without a fresh
// violation of the 1000-line rule. The CLI entry points below stay here and call in.
import { runDeliveryLoop } from "./hermes-delivery-loop.mjs";

loadSettingsEnv();

const IS_MAIN =
  Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

// Windows + Docker Desktop: force IPv4 loopback (see claude-channel.js).
// coerceLoopbackToIPv4 moved to ./aify-http.mjs in v0.5.4.

// AIFY_SERVER_URL moved to ./aify-http.mjs in v0.5.4.
// AIFY_API_KEY moved to ./aify-http.mjs in v0.5.4.

// MACHINE_ID moved to ./hermes-env.mjs in v0.5.4.
// Per-agent channel-sidecar bridge id (holistic-review F1, 2026-05-31). A
// machine-global `hermes-managed-host-<machine>` id collided across co-located
// managed hermes agents because bridge_instances.id is the PRIMARY KEY — only
// one agent could own the row, starving the others' liveness heartbeats and
// letting two detached delivery loops fight over one row. Scope by agentId.
// CHANNEL_BRIDGE_PREFIX moved to ./hermes-run-reporting.mjs in v0.5.4 with its only reader.
// channelBridgeId moved to ./hermes-run-reporting.mjs in v0.5.4.
// HTTP_TIMEOUT_MS moved to ./aify-http.mjs in v0.5.4.
// READY_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4.
// RPC_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4.
// COLD-START DELIVERY RACE (2026-05-31): on the first dispatch after a cold
// (re)launch, the delivery loop can claim + try to deliver BEFORE the visible
// TUI has finished resuming its real session into the gateway, so
// session.active_list returns no matching session yet. Wait (bounded) for the
// session to attach before submitting; if it never attaches in time, REQUEUE
// the run (leave it claimable) rather than failing it permanently.
// ATTACH_WAIT_MS moved to ./hermes-active-session.mjs in v0.5.4.
// ATTACH_POLL_MS moved to ./hermes-active-session.mjs in v0.5.4.
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
// ATTACH_FRESH_GRACE_FRACTION moved to ./hermes-active-session.mjs in v0.5.4.
// TMP_DIR moved to ./hermes-env.mjs in v0.5.4.
// RUNTIME moved to ./hermes-env.mjs in v0.5.4.
// In-flight re-pulse cadence + bounded window (#172). prompt.submit is
// FIRE-AND-FORGET (returns on accept, not turn completion) and the managed-host
// WS client cannot reliably observe the gateway turn-complete event, so we
// re-pulse turn_busy on an interval for a BOUNDED window after each submit so a
// long managed-hermes turn keeps showing `working` past the server's 120s
// window. The window is hard-capped so a missed completion CANNOT stick
// `working` forever (anti-feedback-loop guard for this completion-event-less
// path). The agent's own reply closing the run is the precise clear; the next
// submit resets the window.
// REPULSE_MS moved to ./hermes-inflight.mjs in v0.5.4.
// TURN_START_TIMEOUT_MS moved to ./hermes-inflight.mjs in v0.5.4.
// Continuous gateway turn-state detector cadence (fix/hermes-working-debounce).
// A faster, dedicated poll of the gateway session["running"] status that drives
// the BIDIRECTIONAL turn-state detector (sets working on a gateway-running turn,
// clears on sustained idle). Faster than REPULSE_MS so turn-state reflects
// promptly; the idle→end debounce (GATEWAY_TURN_IDLE_DEBOUNCE ticks at this
// cadence) is what prevents the mid-turn-idle flap. ~3s × 3 ticks = ~9s of
// sustained idle before a turn-end — well under the 120s server backstop.
// REPULSE_WINDOW_MS moved to ./hermes-inflight.mjs in v0.5.4.
// HERMES_CMD moved to ./hermes-env.mjs in v0.5.4.
// Proactive gateway-liveness probe cadence (status-liveness, 2026-06-02). Every
// interval the delivery loop probes the gateway HOST (dashboard index) for
// reachability; after GATEWAY_PROBE_THRESHOLD consecutive failures it reports
// the gateway dead (resident-lost) so the agent stops showing `available` while
// the gateway host is actually gone — the complement to the reactive
// connect-refused self-correct in deliverRun/runDeliveryLoop.
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
// Per-probe HTTP timeout — short so a slow probe still completes within the
// interval and counts as ONE failure (not a hang). Debounced by the threshold.
// GATEWAY_PROBE_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4.

// sleep moved to ./hermes-gateway.mjs in v0.5.4.

// Flatten the many session.active_list envelope shapes into a row array. Mirrors
// hermes-gateway-protocol.js's internal normalizer (kept local so the freshness
// guard can read a chosen row's timestamp without an extra protocol export).
// activeListRowsLocal moved to ./hermes-active-session.mjs in v0.5.4.

// Freshness epoch (ms) of a session.active_list row — the SAME key precedence
// pickMostRecentSession orders by (last_active → started_at → created_at). 0 when
// no parseable timestamp is present (a row with no stamp can never clear a floor
// that is > 0, so it's treated as not-fresh — the safe default for the guard).
// rowFreshnessStamp moved to ./hermes-active-session.mjs in v0.5.4.

// The real id off an active_list row (`id` / `session_id` / `sessionId`).
// rowRealIdLocal moved to ./hermes-active-session.mjs in v0.5.4.

// Seed the per-agent active-session file (FIX C) in the SAME shape hermes' TUI
// writes (useSessionLifecycle.ts writeActiveSessionFile → {"session_id": "..."}),
// and that hermes' Python wrapper reads (_read_tui_active_session_file → .session_id).
// Byte-compatible so the in-session bridge reads a real handle at launch instead of
// the stale launch-time id. Best-effort; the caller swallows throws.
// defaultWriteActiveSessionFile moved to ./hermes-active-session.mjs in v0.5.4.

// Freshness stamp of the row whose real id is `recentId` within an active_list
// response (so the fallback can compare the CHOSEN most-recent session against
// the freshness floor). 0 when the row can't be found / has no stamp.
// stampForSessionId moved to ./hermes-active-session.mjs in v0.5.4.

// The STABLE resume key the visible TUI attaches under. Its runtime id is
// ephemeral; we match on this stable key in session.active_list. This MUST be
// byte-identical to what the install.sh wrapper passes as HERMES_TUI_RESUME, so
// it reuses the SAME sanitization scheme (pinnedSessionId from
// hermes-session-id.js, which the wrapper mirrors via `tr -c 'a-zA-Z0-9_-'`).
// sessionKeyFor moved to ./hermes-active-session.mjs in v0.5.4.

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
// makeAifyHttpCall moved to ./aify-http.mjs in v0.5.4.

// ---------------------------------------------------------------------------
// 1. GATEWAY HOST — hidden `hermes dashboard --tui` child + token scrape.
// ---------------------------------------------------------------------------

// Fetch the dashboard index and scrape __HERMES_SESSION_TOKEN__. Returns the
// token string or throws. fetchImpl is injectable (tests pass a fake).
// scrapeToken moved to ./hermes-gateway.mjs in v0.5.4.

// Poll the dashboard index until it responds (and carries a token), or the
// deadline elapses. Returns the token.
// waitForIndexToken moved to ./hermes-gateway.mjs in v0.5.4.

// Spawn (idempotently) the hidden gateway host and return its coordinates.
//   { port, token, wsUrl, child }
// - `hermes dashboard --port <port> --host 127.0.0.1 --no-open --skip-build`
// - detached:true, windowsHide:true (CRITICAL — no popup OS window).
// - When probeFirst is set we probe the index first; if a host is already
//   serving (token scrape succeeds) we DON'T spawn (idempotent re-attach).
// ensureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.

// ---------------------------------------------------------------------------
// Blast-radius isolation (task #237 item a): PROACTIVE gateway re-ensure.
// ---------------------------------------------------------------------------
//
// Upstream `hermes update` and `hermes dashboard --stop` SIGTERM EVERY
// `hermes dashboard`/`serve` process, so an operator running `hermes update` kills
// ALL aify managed gateway hosts at once. Previously they only recovered on the next
// env-bridge BOOT (a full bridge restart). This helper lets the normal periodic
// delivery/heartbeat cycle DETECT a dead gateway host and RE-ENSURE (respawn) it
// idempotently, so a single agent's host self-heals without waiting for a bridge
// restart.
//
// SAFETY:
//   - NEVER double-spawns: it re-ensures ONLY when `isAlive()` reports the gateway
//     dead (dashboard index unreachable). A gateway that is bound but merely
//     ws-broken stays `alive` here and is left to the reactive resident-lost path —
//     so this never fights that behavior. On top of that, `ensureHost` is expected to
//     be `ensureGatewayHost` (probeFirst), which itself reuses any live host.
//   - NEVER fights a deliberate stop: `isStopping()` short-circuits before any probe
//     or spawn, so a teardown-in-progress is never re-spawned.
//
// Pure/injected (isAlive, ensureHost, isStopping) so it is fully unit-testable with
// no real sockets or processes.
// CRASH-LOOP GUARD (2026-07-11, from reading Traycer's host-health-monitor:
// MAX_AUTO_RESPAWNS_WITHOUT_RECOVERY = 3, counter reset by a successful probe).
// maybeReEnsureGatewayHost re-ensures a dead gateway host on EVERY delivery poll; for
// a gateway that BINDS-THEN-DIES (TUI-build failure `Missing script: "build"`, an
// operator `hermes update`/`--stop`, a hermes/GLM account with no API balance), that
// respawns a fresh dashboard process every POLL_MS with no ceiling — each a hermes.exe
// the reapers must then clean (the proliferation / headless-orphan class). This budget
// caps consecutive no-recovery respawns; a live ws connect (real recovery) resets it.
// MAX_REENSURE_WITHOUT_RECOVERY moved to ./hermes-gateway.mjs in v0.5.4.

// Pure budget arbiter so the guard is unit-testable without sockets/processes. Returns
// the next budget: a live ws RESETS to max; a re-ensure DECREMENTS (floored at 0); a
// steady tick is unchanged. Callers gate the respawn on `budget > 0`.
// nextReEnsureBudget moved to ./hermes-gateway.mjs in v0.5.4.

// maybeReEnsureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.

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
// resolveHermesPython moved to ./hermes-active-session.mjs in v0.5.4.

// Create-or-ignore the stable `aify-<agentId>` session row via the hermes
// SessionDB. Idempotent (INSERT OR IGNORE) + best-effort (never throws). Returns
// true when the row is known to exist afterward, false on any failure.
// `spawnSync` is injectable for tests.
// ensureStableSession moved to ./hermes-active-session.mjs in v0.5.4.

// ---------------------------------------------------------------------------
// WS client — a thin JSON-RPC request/response wrapper over `ws`.
// ---------------------------------------------------------------------------

// Open a WS client to the gateway and return { request(frame), close() }.
// `WebSocketImpl` is injectable for tests; production uses the bundled `ws`.
// openGatewayWsClient moved to ./hermes-gateway.mjs in v0.5.4.

// ---------------------------------------------------------------------------
// aify dispatch reporting helpers (mirror hermes-channel.js).
// ---------------------------------------------------------------------------

// reportTurnBusy moved to ./hermes-run-reporting.mjs in v0.5.4.

// clearTurn moved to ./hermes-run-reporting.mjs in v0.5.4.

// Read the dispatch run's current status + require_reply flag (the
// host-observable turn-end signals). Best-effort: any error → status "" (treated
// as not-yet-terminal, requireReply false). The run's terminal status
// (`completed` when the agent self-replies, or failed/cancelled/stopped) is one
// real "this turn finished" signal; a `delivered` run with require_reply=0 is
// the OTHER (a delivery-only nudge owes no turn) — see shouldLatchComplete.
// GET /dispatch/runs/{id} already exposes `requireReply` via
// _serialize_dispatch_run_row (no server change needed).
// fetchRunStatus moved to ./hermes-inflight.mjs in v0.5.4.

// markRunDelivered moved to ./hermes-run-reporting.mjs in v0.5.4.

// markRunFailed moved to ./hermes-run-reporting.mjs in v0.5.4.

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
// isGatewayConnectRefused moved to ./hermes-gateway.mjs in v0.5.4.

// Build the actionable run-failure message for a dead gateway port.
// gatewayUnreachableMessage moved to ./hermes-gateway.mjs in v0.5.4.

// Build the actionable run-failure message for the BOUNDED no-attach case (Task
// 2.3): the gateway host is REACHABLE (we polled session.active_list) but NO
// visible hermes TUI ever attached to it — active_list stayed empty across the
// bounded requeue budget. This is distinct from gatewayUnreachableMessage (a dead
// PORT): here the host is alive but no TUI is a WS client of it (it spun up its own
// tui_gateway, or no `hermes --tui` is running), so injected prompts have nowhere
// to render. The fix is to relaunch hermes-aify so the visible TUI attaches to THIS
// gateway via HERMES_TUI_GATEWAY_URL. `attempts` is the consecutive empty-poll count.

// Self-correct off `available` via the EXISTING resident-lost signal (the same
// host→server path server.js uses when a resident Codex app-server is
// unreachable). The server transitions the agent resident→managed (or stopped),
// which immediately drops `resident-run`/`available` instead of waiting out the
// ~150s heartbeat lease. NOTE: deliberately NO bridgeId — the managed-host's
// channel-sidecar bridge id is not the resident MCP bridge that owns
// runtime_state.bridgeInstanceId, so sending it would hit the server's
// bridge_not_current guard and be ignored. Best-effort: never throws.
// reportGatewayDead moved to ./hermes-gateway.mjs in v0.5.4.

// Derive the gateway HOST index URL (`http://127.0.0.1:<port>/`) from the
// gateway WS URL (`ws://127.0.0.1:<port>/api/ws?token=...`). The index is the
// cheapest reachability signal for the hidden `hermes dashboard --tui` host —
// the same surface ensureGatewayHost's idempotent probe uses. Returns "" when
// the URL can't be parsed (probe then treats the gateway as unreachable).
// gatewayIndexUrlFromWs moved to ./hermes-gateway.mjs in v0.5.4.

// Build a single-shot gateway reachability probe for the proactive liveness
// checker. Resolves `{ alive: boolean }`; a non-OK response or a thrown
// fetch/timeout counts as not-alive (a failure). `fetchImpl` + `timeoutMs` are
// injectable so the driver is unit-testable with no real sockets.
// makeGatewayReachabilityProbe moved to ./hermes-gateway.mjs in v0.5.4.

// COLD-START requeue: the visible TUI has not (yet) attached its real session
// to the gateway, so this is a TRANSIENT not-yet-ready condition, NOT a
// permanent failure. Put the run back to `queued` (claimable) so the very next
// poll delivers once the TUI finishes resuming. Never markRunFailed for this.
// markRunRequeued moved to ./hermes-run-reporting.mjs in v0.5.4.

// ---------------------------------------------------------------------------
// 3. DELIVERY — claim → active_list → prompt.submit (requeue on busy) → delivered.
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
// waitForActiveSession moved to ./hermes-active-session.mjs in v0.5.4.

// Drive ONE claimed run end-to-end (WAKE-ONLY). NEVER throws.
//   reportTurnBusy(true) → WAIT for active session (cold-start race) →
//   session.steer for an ordinary busy send, otherwise prompt.submit →
//   markRunDelivered → clearTurn. If the visible TUI never attaches within the
//   bounded window, REQUEUE the run (claimable) — NOT markRunFailed — so the
//   next poll delivers once the TUI resumes. On real failure: markRunFailed.
// The runtime sid is re-discovered here every call — never cached.

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
// makeInFlightProbe moved to ./hermes-inflight.mjs in v0.5.4.

// shouldApplyGatewayTurnEnd moved to ./hermes-gateway.mjs in v0.5.4.

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
// makeInFlightPulse moved to ./hermes-inflight.mjs in v0.5.4.

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

// ---------------------------------------------------------------------------
// Teardown — kill the gateway-host child on shutdown / release.
// ---------------------------------------------------------------------------

// _teardownState moved to ./hermes-gateway.mjs in v0.5.4.

// Kill the gateway-host child. Best-effort + idempotent (a shared `state` flag
// guards double teardown). NEVER throws.
// teardownGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.

// Build a bound teardown callback that kills the gateway host the loop owns —
// by its OWNED CHILD HANDLE when one exists, else BY PORT (a reused host the
// loop attached to via the idempotent probe has child===null but still must be
// killed when this loop is its lifecycle owner). Best-effort + idempotent via a
// shared `state` flag. NEVER throws. `killByPort` defaults to the real
// port→PID→kill from hermes-daemon.js; `clearMarkers` (optional) runs after the
// kill so a teardown also drops the agent's port/key markers (Task 4.1 wiring).
// makeTeardown moved to ./hermes-gateway.mjs in v0.5.4 — teardown is that module's subject.

// Wire SIGTERM/SIGINT → teardown → exit. When the caller supplies a bound
// `teardown` (the port-aware makeTeardown from runDeliveryLoop, Task 1.2) it is
// used so a SIGTERM kills a reused (child===null) host BY PORT too; otherwise it
// falls back to the legacy child-only teardownGatewayHost. `getChild` returns
// the current gateway-host child (it's spawned after handler install). `proc` is
// injectable for tests.
// installShutdownTeardown moved to ./hermes-gateway.mjs in v0.5.4.

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


// Classify a /dispatch/claim error against a small mutable counter object
// `{ count }` (the consecutive-404 self-heal counter). Returns one of:
//   { terminal:true, reason:"agent-removed" }   — 410, or 404 past the grace
//   { terminal:false }                            — transient (retry)
// `grace` is injectable for tests.

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
// RESUME-POINTER SYNC (2026-06-05): keep the agent's durable resume marker + aify session_handle
// in lock-step with whatever session the TUI is LIVE on — independent of aify-comms delivery. The
// TUI mints/switches sessions on its own (operator typing in the visible TUI), and the only prior
// marker-update path (waitForActiveSession) runs ONLY on a delivery — so a directly-used agent kept
// resuming a stale (often GC'd) key and started a FRESH "(untitled)" session on every restart,
// losing the operator's thread (the next-tech-lead bug, with 26 resumable sessions stranded). This
// periodic best-effort beat reads the gateway's most-recent live session, takes its DURABLE key
// (rowResumeKey — session.list/active_list rows expose it), and writes it to the marker + reports it
// to aify, so the next launch resolves the live session. Pure READ of gateway truth + marker write;
// never throws into delivery. An empty active_list (gateway idle/restarting) leaves the marker
// UNCHANGED — clearing is resolve's job, not this beat's.
// startResumeMarkerSync moved to ./hermes-active-session.mjs in v0.5.4.


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
    openWsImpl = openGatewayWsClient,
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
  const host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl, openWsImpl });
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
// runResolveSessionCli moved to ./hermes-active-session.mjs in v0.5.4.

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
