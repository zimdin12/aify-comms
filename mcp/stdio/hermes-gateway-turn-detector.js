// Managed-hermes hook-independent turn-STATE detector (2026-06-02
// fix/hermes-working-debounce). The hermes mirror of the CLAUDE bidirectional
// detector pair (turn-end-detector.js + claude-turn-end-detector.js).
//
// WHY (the flap fix): the managed-hermes re-pulse probe (makeInFlightProbe in
// hermes-managed-host.js) ended a turn on the FIRST gateway-idle read after
// observing working. But the hermes gateway session["running"] flag flips False
// MID-TURN (between tool calls, during model/generation gaps), surfacing
// status "idle" for a tick or two. A single-idle latch therefore false-cleared
// turn_busy mid-turn → the agent flapped working↔online, the next re-pulse
// flipping it back. The fix DEBOUNCES the idle→end transition: require N
// CONSECUTIVE idle observations before latching turn-end; any "working" read
// resets the streak. A momentary mid-turn idle can never end the turn.
//
// WHY (continuous + bidirectional — the #172 fix): the in-flight probe only ran
// during a DISPATCH's bounded window (shouldManagedHostRepulse keyed on
// inFlight.submittedAt). An AUTONOMOUS / direct-typed-in-the-TUI turn never
// stamps submittedAt, so it was never tracked → the agent showed online/available
// while actually working. This detector is a CONTINUOUS per-agent loop (run in
// runDeliveryLoop, not only during a dispatch) that reads the gateway session
// status every tick and:
//   gateway RUNNING ("working")           → POST /turn-start (SET working), once
//   gateway IDLE sustained (>= debounce)  → POST /turn-end   (CLEAR), once
//   unknown / gateway-error / starting / waiting → no change
// so it covers typed, channel-woken, dispatch-driven, AND autonomous turns.
//
// ANTI-FEEDBACK-LOOP INVARIANT (mirrors decideRepulse): the input is ALWAYS the
// gateway's OWN session["running"] truth (session.active_list → `status`), NEVER
// the aify server's DERIVED status. /turn-start is EDGE-TRIGGERED (fired once on
// the working transition, never per tick) so it cannot spam or self-reinforce.
//
// SUBMIT-RACE GUARD: an "idle" observed BEFORE the first "working" never ends a
// turn — the state machine only counts idle ticks once it has entered in-flight,
// so a momentary post-submit idle (before the worker thread flips running=True)
// cannot end the turn early (#172 under-show-working guard).
//
// FALSE-CLEAR / FALSE-SET SAFETY: an unknown/empty/error status is a transient
// no-op — it neither fires a directive nor resets the idle streak, so a single
// unreadable tick between idle ticks does not stall the eventual turn-end and a
// single unreadable tick mid-working does not spuriously end the turn.

import { isGatewaySessionIdle, isGatewaySessionWorking } from "./hermes-gateway-protocol.js";

// Default consecutive-idle ticks required before latching turn-end. Chosen with
// the loop's default 3s cadence (~9s sustained idle) so it comfortably outlasts
// the momentary mid-turn running=False gaps that caused the flap, while keeping
// turn-end latency well under the old 120s server backstop. Tunable via the loop.
export const DEFAULT_IDLE_DEBOUNCE_TICKS = 3;

// Pure, debounced, bidirectional state machine. Feed one gateway session status
// string per tick via observe(); it returns an edge-triggered directive:
//   "start" — transitioned into in-flight (gateway "working")
//   "end"   — sustained idle (>= idleDebounce) after having been in-flight
//   null    — steady state, transitional, or unknown (no action this tick)
export function makeGatewayTurnDetector({ idleDebounce = DEFAULT_IDLE_DEBOUNCE_TICKS } = {}) {
  const threshold = Math.max(1, Number(idleDebounce) || DEFAULT_IDLE_DEBOUNCE_TICKS);
  // inFlight: have we observed "working" and not yet latched the turn-end?
  let inFlight = false;
  // idleStreak: consecutive idle observations WHILE in-flight.
  let idleStreak = 0;

  return {
    observe(status) {
      const working = isGatewaySessionWorking(status);
      const idle = isGatewaySessionIdle(status);

      if (working) {
        idleStreak = 0; // any working read resets the idle streak (no flap).
        if (!inFlight) {
          inFlight = true;
          return "start"; // edge: into in-flight. Fire /turn-start ONCE.
        }
        return null; // already in-flight → steady state, no re-fire.
      }

      if (idle) {
        // Submit-race guard: idle before ever observing working is not a turn-end.
        if (!inFlight) return null;
        idleStreak += 1;
        if (idleStreak >= threshold) {
          // Debounce satisfied: a real, sustained turn-end. Latch ONCE.
          inFlight = false;
          idleStreak = 0;
          return "end";
        }
        return null; // idle below threshold → keep waiting (no premature end).
      }

      // Unknown / "starting" / "waiting" / "" — transitional. A no-op that does
      // NOT change in-flight and does NOT reset the idle streak (a single
      // unreadable tick mustn't stall the eventual end nor false-end a turn).
      return null;
    },
  };
}

// Periodic loop wrapper (mirrors startClaudeTurnEndDetector). Every intervalMs it
// reads the gateway session status and acts on the detector's edge-triggered
// directive. Returns a stop() function. Best-effort throughout: a read error or a
// POST error never kills the timer (the next tick / the long server backstop
// self-heals). Timer is unref'd so it never holds the process open.
export function startHermesGatewayTurnDetector({
  intervalMs,
  idleDebounce = DEFAULT_IDLE_DEBOUNCE_TICKS,
  readGatewayStatus,
  postTurnStart,
  postTurnEnd,
}) {
  const noop = () => {};
  if (
    typeof readGatewayStatus !== "function" ||
    typeof postTurnEnd !== "function" ||
    !Number.isFinite(intervalMs) ||
    intervalMs <= 0
  ) {
    return noop;
  }
  let stopped = false;
  const detector = makeGatewayTurnDetector({ idleDebounce });

  const tick = async () => {
    if (stopped) return;
    let status;
    try {
      status = String((await readGatewayStatus()) || "");
    } catch {
      // Gateway hiccup → unknown status → detector treats it as a transient
      // no-op (never a false turn-end). Skip this tick entirely.
      return;
    }
    let directive = null;
    try {
      directive = detector.observe(status);
    } catch {
      return;
    }
    if (!directive || stopped) return;
    if (directive === "start") {
      if (typeof postTurnStart === "function") {
        try {
          await postTurnStart();
        } catch {
          /* best-effort; the next working tick is steady-state (no retry needed),
             and the dispatch delivery pulse remains the instant set path */
        }
      }
      return;
    }
    // directive === "end"
    try {
      await postTurnEnd();
    } catch {
      /* best-effort; the long server backstop still self-heals */
    }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
