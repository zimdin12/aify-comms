import {
  AIFY_API_KEY,
  AIFY_SERVER_URL,
  makeAifyHttpCall,
} from "./aify-http.mjs";
import {
  activeListRowsLocal,
  sessionKeyFor,
  startResumeMarkerSync,
} from "./hermes-active-session.mjs";
import { defaultKillByPort } from "./hermes-daemon.js";
import {
  noAttachedSessionTeardownMessage,
  runPollCycle,
} from "./hermes-delivery-run.mjs";
import {
  clearGatewayMarkers as defaultClearGatewayMarkers,
  clearSessionMarker as defaultClearSessionMarker,
  readSessionIdMarker,
  resolveGatewayPort,
} from "./hermes-endpoint.js";
import { TMP_DIR } from "./hermes-env.mjs";
import { startGatewayLivenessProbe } from "./hermes-gateway-liveness.js";
import {
  buildSessionActiveListFrame,
  pickMostRecentSessionRow,
  pickSessionStatusById,
  pickSessionStatusForKey,
} from "./hermes-gateway-protocol.js";
import {
  DEFAULT_IDLE_DEBOUNCE_TICKS,
  startHermesGatewayTurnDetector,
} from "./hermes-gateway-turn-detector.js";
import { buildGatewayTurnCallbacks } from "./hermes-turn-detector-callbacks.mjs";
import {
  MAX_REENSURE_WITHOUT_RECOVERY,
  ensureGatewayHost,
  gatewayIndexUrlFromWs,
  gatewayUnreachableAfterProbesMessage,
  gatewayUnreachableMessage,
  installShutdownTeardown,
  isGatewayConnectRefused,
  makeGatewayReachabilityProbe,
  makeTeardown,
  maybeReEnsureGatewayHost,
  nextReEnsureBudget,
  openGatewayWsClient,
  reportGatewayDead,
  sleep,
} from "./hermes-gateway.mjs";
import {
  REPULSE_MS,
  REPULSE_WINDOW_MS,
  makeInFlightProbe,
  makeInFlightPulse,
} from "./hermes-inflight.mjs";
import {
  clearLoopReady,
  writeLoopReady,
} from "./hermes-loop-ready.js";
import {
  channelBridgeId,
  clearTurn,
  markRunFailed,
} from "./hermes-run-reporting.mjs";
import { startInFlightRepulse } from "./hermes-turn-repulse.js";
import { startLivenessHeartbeat } from "./liveness-heartbeat.js";

// The per-agent delivery loop: one long-lived async function that owns a gateway child, a websocket,
// a liveness heartbeat and a turn detector for the lifetime of one managed hermes agent.
//
// Split out of `hermes-managed-host.js` in v0.5.4 with `hermes-delivery-run.mjs`. It is 619 lines and
// stays ONE function deliberately: its 32 depth-1 declarations are closure state that the seven inner
// arrow functions capture, so carving them out is a redesign — an object owning that state — not a
// relocation. This slice moves it byte-for-byte and leaves that question open.

const POLL_MS = Math.max(
  500,
  Number(process.env.AIFY_COMMS_CHANNEL_POLL_MS || process.env.AIFY_HERMES_CHANNEL_POLL_MS || 3000),
);
const GATEWAY_TURN_POLL_MS = Math.max(
  1000,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_POLL_MS || 3000),
);
const GATEWAY_TURN_IDLE_DEBOUNCE = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE || DEFAULT_IDLE_DEBOUNCE_TICKS),
);
const GATEWAY_PROBE_MS = Math.max(
  5000,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_MS || 30000),
);
const GATEWAY_PROBE_THRESHOLD = Math.max(
  1,
  Number(process.env.AIFY_HERMES_GATEWAY_PROBE_THRESHOLD || 3),
);
const NO_TUI_TEARDOWN_CYCLES = Math.max(
  1,
  Number(process.env.AIFY_HERMES_NO_TUI_TEARDOWN_CYCLES || 10),
);
const NO_TUI_GRACE_MS = Math.max(
  0,
  Number(process.env.AIFY_HERMES_NO_TUI_GRACE_MS || 90000),
);

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

  // RESUME-POINTER SYNC: keep the durable resume marker + aify handle tracking the TUI's live
  // session so a restart resumes it instead of minting a fresh "(untitled)" one. Best-effort,
  // unref()'d — it dies with this `run` process on teardown/procExit (no explicit stop needed).
  startResumeMarkerSync({ agentId: id, tempDir: markerDir });

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
      // verifyWs:false here — the delivery loop opens `/api/ws` itself right below
      // (`openWs(host.wsUrl)`) with its own connect-refused self-correct, so a
      // readiness probe in ensureGatewayHost would double-open the socket and
      // pre-empt that path. The primary `/api/ws` readiness guard lives in the
      // CLI `ensure-host` path (runEnsureHostCli), which is what declares the
      // gateway ready for the wrapper before the TUI attaches.
      host = await ensureGatewayHost({ agentId: id, port, spawn, fetchImpl, verifyWs: false });
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
  const wsIsOpen = (client) => {
    const rs = client?._socket?.readyState;
    return rs === undefined || rs === 1; /* OPEN (or a fake test client w/o a socket) */
  };
  const ensureWs = async () => {
    // Liveness-check the cached client (review must-fix, 2026-06-10): a dropped socket
    // (gateway/TUI restart, idle close) previously stayed cached FOREVER — every request
    // rejected with "WS not open" (which isGatewayConnectRefused deliberately excludes), so
    // the loop became a zombie: it kept claiming runs it could never deliver, failed each
    // after the 25s attach window with a misleading "No visible hermes TUI" error, and the
    // turn detector + no-TUI teardown went blind. Re-open on a dead socket (mirrors the
    // resident reader's wsOpen check in server.js).
    if (wsClient && wsIsOpen(wsClient)) return wsClient;
    try { wsClient?.close?.(); } catch { /* ignore */ }
    wsClient = await openWs(host.wsUrl);
    return wsClient;
  };
  // Latch so the gateway-dead self-correct fires AT MOST once per loop lifetime —
  // a refused connect repeats every poll until the agent is torn down, and we
  // must not spam resident-lost (which the server treats as a state transition).
  // SHARED between the REACTIVE path (a run's connect-refusal, below) and the
  // PROACTIVE periodic probe (started further down) so the two never both fire.
  let gatewayDeadReported = false;
  // Consecutive gateway re-ensures with no ws recovery (crash-loop guard). Reset to
  // MAX on a successful ws connect below; exhausting it stops the respawn and falls to
  // the resident-lost path instead of leaking another hermes.exe every poll.
  let reEnsureBudget = MAX_REENSURE_WITHOUT_RECOVERY;
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
  // gateway dead ONCE (resident-lost) — which for a MANAGED agent rests it
  // cold-startable rather than taking it off `available`, the server's call —
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
        gatewayUnreachableAfterProbesMessage(host.wsUrl, consecutiveFailures),
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
  // dispatchTurnOpen (2026-07-10 flap fix): TRUE from a successful delivery until
  // the gateway turn detector observes this turn END. UNLIKE submittedAt/completed
  // (maintained by the re-pulse probe, which STOPS at REPULSE_WINDOW_MS ~15min, so
  // a >15min turn leaves them frozen), this credit is revoked by the CONTINUOUS
  // detector's own turn-END, so it is accurate for turns of any length. The detector
  // gates its /turn-start on it: hermes POST-TURN background model work (which also
  // sets gateway session["running"]=True, but has no fresh delivery) then cannot
  // re-fire `working` on an idle-to-the-user agent. See the detector's shouldFireTurnStart.
  const inFlight = { submittedAt: 0, completed: false, runId: "", observedWorking: false, dispatchTurnOpen: false };
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
    const byKey = pickSessionStatusForKey(listResp, managedSessionKey);
    if (byKey) return byKey;
    // FINAL FALLBACK (2026-06-06): the hermes gateway keys active_list rows by the
    // EPHEMERAL runtime id + a human task TITLE, never the durable session_key — so
    // a resumed/captured session misses BOTH lookups above and returns "" (read as
    // not-idle → the gateway's idle/ready turn-end is never observed → in_turn latches
    // `working` until the 30-min backstop: the cms/next-* stuck-`working` incident).
    // The gateway is PER-AGENT (its own port), so active_list holds ONLY this agent's
    // session(s) — the most-recent row IS this agent's live session. Read its status
    // so turn-end fires. SAFE: the detector's observed-working guard + multi-tick idle
    // debounce still prevent ending a turn early on a transient idle blip.
    const recentRow = pickMostRecentSessionRow(listResp);
    return String(recentRow?.status || "").trim();
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
      failRunImpl: (runId, error) => markRunFailed(httpCall, { id: runId }, error),
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
    // postTurnStart / postTurnEnd / shouldFireTurnStart moved to
    // ./hermes-turn-detector-callbacks.mjs in v0.6 Phase 1. Each is the fix for a named 2026-07-10
    // incident — the turn_run_id race that deadlocked reply reminders, and the post-turn status
    // flap — and none of them had a test, because firing one needed a live gateway.
    ...buildGatewayTurnCallbacks({ inFlight, httpCall, id }),
    // Re-stamp turn-busy while the gateway stays WORKING so a turn longer than the
    // dispatch re-pulse window (~15min) never expires turn_busy → `online` while working.
    // Mirrors the re-pulse cadence (45s), comfortably under the 120s server stale window.
    workingRefreshMs: REPULSE_MS,
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
            // Blast-radius isolation (task #237 item a): before self-correcting off
            // `available` (resident-lost, which only recovers on the next env-bridge
            // BOOT), try to RE-ENSURE the gateway host proactively on this periodic
            // cycle — an operator `hermes update` / `hermes dashboard --stop` SIGTERMs
            // EVERY hermes dashboard process, so our per-agent host can die under a live
            // loop. Idempotent + double-spawn-safe: it respawns ONLY when the dashboard
            // index is truly unreachable (a bound-but-ws-broken gateway stays `alive`
            // here → falls through to the reactive resident-lost path, preserving that
            // behavior), never fights a teardown, and ensureGatewayHost's probeFirst
            // reuses any live host.
            // Crash-loop guard: only respawn while the budget holds. Once exhausted,
            // skip the re-ensure so a binds-then-dies gateway falls to resident-lost
            // instead of respawning forever (a fresh ws connect resets the budget below).
            const re = reEnsureBudget > 0
              ? await maybeReEnsureGatewayHost({
                  isStopping: () => teardownState.done,
                  isAlive: makeGatewayReachabilityProbe({
                    indexUrl: gatewayIndexUrlFromWs(host.wsUrl),
                    fetchImpl,
                  }),
                  ensureHost: () =>
                    ensureGatewayHost({ agentId: id, port, spawn, fetchImpl, verifyWs: false }),
                  log: (m) => console.error(m),
                }).catch(() => ({ reEnsured: false }))
              : { reEnsured: false, reason: "reensure-budget-exhausted" };
            if (re && re.reEnsured && re.host) {
              reEnsureBudget = nextReEnsureBudget(reEnsureBudget, { reEnsured: true });
              if (reEnsureBudget === 0) {
                console.error(
                  `[hermes-managed-host] '${id}': gateway re-ensured ${MAX_REENSURE_WITHOUT_RECOVERY}x without a ws recovery ` +
                  `(binds-then-dies crash loop?) — this is the last respawn; further deaths fall to resident-lost until it stays up.`,
                );
              }
              host = re.host;
              if (re.host.child) gatewayChild = re.host.child;
              try {
                wsClient?.close?.();
              } catch {
                /* ignore */
              }
              wsClient = null;
              // Reset the dead-latch (#237 low note): the host was re-ensured, so a LATER
              // death must be free to report again. Without this, once reportGatewayDeadOnce
              // had latched, a second gateway death after a recovery would never be reported.
              gatewayDeadReported = false;
              // Recovered — do NOT report resident-lost; retry delivery next tick.
              await sleepImpl(POLL_MS);
              continue;
            }
            await reportGatewayDeadOnce(gatewayUnreachableMessage(host.wsUrl));
          }
          await sleepImpl(POLL_MS);
          continue;
        }
        // RECOVERY: the ws connected → the gateway is reachable. Reset the crash-loop
        // respawn budget so a FUTURE binds-then-dies episode gets a fresh set of tries.
        reEnsureBudget = nextReEnsureBudget(reEnsureBudget, { recovered: true });
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
              noAttachedSessionTeardownMessage(host.wsUrl, noTuiCycles),
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
