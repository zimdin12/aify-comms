// Proactive periodic gateway-liveness probe for hermes bridges.
//
// THE GAP (status-liveness, 2026-06-02): a hermes agent gets `resident-run`
// (→ status `available`/`online`) whenever a gatewayUrl is PRESENT — runtimes.js
// `gatewayOk = !!gatewayUrl` and the server's `_has_hermes_gateway_url` are both
// PRESENCE checks, not LIVENESS checks. So a hermes whose api_server/gateway
// (`hermes.exe`) DIED, but whose bridge is still heartbeating (the A3 liveness
// beat proves the BRIDGE is alive, not the gateway), keeps showing `available`
// for the whole heartbeat lease — operator saw `available` + a red dot on a
// resident with a dead gateway.
//
// The server runs IN A CONTAINER and cannot reach a remote agent's
// 127.0.0.1:<port> gateway, so liveness can ONLY be determined on the agent's
// own host. This is the BRIDGE-side complement to the REACTIVE
// reportGatewayDead (hermes-managed-host.js, fired on a run's connect-refusal):
// it catches a dead gateway EVEN WITH NO PENDING RUN.
//
// DEBOUNCE: require N CONSECUTIVE failed probes (default 3) before declaring the
// gateway dead — a single slow/transient probe must NOT flip a healthy agent to
// stale. The counter resets on any successful probe. On reaching the threshold
// we call reportDead ONCE (latched) via the existing resident-lost path, then stop. WHAT THE AGENT
// RESTS AT IS THE SERVER'S CALL, from session_mode: a resident stops, a MANAGED worker rests
// cold-startable and goes on reading `available` so the next message starts a fresh session.
// (`stale` left the status vocabulary on 2026-06-18 and is not a destination.)
//
// Mirrors the liveness-heartbeat.js / hermes-turn-repulse.js pattern: a PURE
// decision helper (unit-testable with injected results, no sockets) + a timer
// driver (injected probe + reportDead + interval). The timer is unref'd; probe
// errors are swallowed (a throw counts as a failure, never crashes the timer).
//
// File budget per 500-line rule: <= 150 lines.

export const DEFAULT_GATEWAY_PROBE_THRESHOLD = 3;
export const DEFAULT_GATEWAY_PROBE_INTERVAL_MS = 30_000;

// TUI-deps / npm-build boot-failure signature (task #237 item e). When the hidden
// `hermes dashboard` gateway host boots it may run an "Installing TUI dependencies"
// npm step that is NOT covered by `--skip-build`; on hermes upstream drift that step
// fails with `npm error Missing script: "build"` (the 2026-07-03 incident), so the
// dashboard never binds and the launch otherwise surfaces only as an OPAQUE ~60s
// readiness timeout ("did not become ready within 60000ms"). Detect the signature in
// the gateway child's stderr so a broken boot is INSTANTLY triageable and can fail
// FAST instead of polling the full timeout. Pure + exported for unit tests.
export function isTuiDepsBuildFailure(text) {
  const s = String(text || "");
  if (!s) return false;
  // The canonical npm failure (with or without the `npm error`/`npm ERR!` prefix).
  if (/npm (?:error|ERR!)\s+Missing script:\s*["']build["']/i.test(s)) return true;
  if (/Missing script:\s*["']build["']/i.test(s)) return true;
  // hermes' own wrapper message around the same failure.
  if (/TUI build failed/i.test(s)) return true;
  // The TUI-deps install banner paired with any npm error line.
  if (/Installing TUI dependencies/i.test(s) && /npm (?:error|ERR!)/i.test(s)) return true;
  return false;
}

// Build the CLEAR, distinct error message for the TUI-deps/npm-build boot failure so
// triage is instant (vs the opaque readiness timeout). `detail` is an optional stderr
// tail included for context.
export function tuiDepsBuildFailureMessage(port, detail) {
  const tail = String(detail || "").trim().slice(-400);
  return (
    `hermes gateway host on port ${port} FAILED its boot-time "Installing TUI dependencies" ` +
    `npm step (\`npm error Missing script: "build"\`). This step runs OUTSIDE \`--skip-build\` and ` +
    `breaks on hermes upstream drift (the 2026-07-03 incident); the dashboard never bound. ` +
    `TRIAGE: repair/pin the hermes TUI build (see the aify-comms-debug skill's 10-second triage), ` +
    `then relaunch hermes-aify.` +
    (tail ? ` [stderr tail] ${tail}` : "")
  );
}

// Pure decision: given the running count of CONSECUTIVE failed probes and a
// threshold, should we declare the gateway dead? True only at/above the
// threshold. A non-positive/NaN threshold disables the decision (never dead).
export function gatewayProbeShouldDeclareDead(
  consecutiveFailures,
  threshold = DEFAULT_GATEWAY_PROBE_THRESHOLD,
) {
  const t = Number(threshold);
  if (!Number.isFinite(t) || t <= 0) return false;
  const n = Number(consecutiveFailures) || 0;
  return n >= t;
}

// Pure counter step: increment the consecutive-failure count on a failed probe,
// reset to 0 on a successful probe. `ok` is the probe's alive/healthy result.
export function nextConsecutiveFailures(consecutiveFailures, ok) {
  if (ok) return 0;
  return (Number(consecutiveFailures) || 0) + 1;
}

// Start the proactive gateway-liveness probe. Every `intervalMs`, call
// `probe()` (must resolve `{ alive: boolean }` or throw — a throw counts as a
// failed probe). Track CONSECUTIVE failures; reset on any success. When the
// failure count reaches `threshold`, call `reportDead({ consecutiveFailures })`
// EXACTLY ONCE (latched), then stop probing for this probe's lifetime — a single
// resident-lost transition is enough; the server owns the state, and we must
// not spam it. Returns a `stop()` that halts the timer.
//
// Everything is injected (probe, reportDead, timing) so the driver is fully
// unit-testable with no real sockets and no real processes.
export function startGatewayLivenessProbe({
  intervalMs = DEFAULT_GATEWAY_PROBE_INTERVAL_MS,
  threshold = DEFAULT_GATEWAY_PROBE_THRESHOLD,
  probe,
  reportDead,
  log = (msg) => console.error(msg),
} = {}) {
  const noop = () => {};
  if (
    typeof probe !== "function" ||
    typeof reportDead !== "function" ||
    !Number.isFinite(intervalMs) ||
    intervalMs <= 0
  ) {
    return noop;
  }

  let stopped = false;
  let latched = false; // reportDead has fired → do not fire again
  let consecutiveFailures = 0;
  let running = false; // re-entrancy guard (a slow probe must not overlap)

  const tick = async () => {
    if (stopped || latched || running) return;
    running = true;
    let alive = false;
    try {
      const result = await probe();
      alive = !!(result && result.alive);
    } catch {
      alive = false; // a throwing probe counts as a failure
    } finally {
      running = false;
    }
    if (stopped || latched) return;

    consecutiveFailures = nextConsecutiveFailures(consecutiveFailures, alive);

    if (gatewayProbeShouldDeclareDead(consecutiveFailures, threshold)) {
      latched = true; // latch BEFORE the async report so no tick double-fires
      try {
        log(
          `[hermes-gateway-liveness] gateway unreachable for ${consecutiveFailures} ` +
            `consecutive probe(s) (>= ${threshold}); reporting gateway dead.`,
        );
      } catch {
        /* logging must never break the latch */
      }
      try {
        await reportDead({ consecutiveFailures });
      } catch {
        /* best-effort: reportDead must never crash the timer; stay latched */
      }
    }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return function stop() {
    stopped = true;
    clearInterval(timer);
  };
}
