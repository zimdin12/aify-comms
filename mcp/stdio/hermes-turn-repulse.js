// In-flight turn_busy re-pulse for the HERMES bridge paths (#172).
//
// PROBLEM (operator-observed 2026-06-01): a MANAGED hermes agent (e.g.
// sc-coder) shows status `online` while it is actually WORKING. The server
// only reports `working` when agent_turn_state.turn_busy is FRESH (within
// TURN_BUSY_STALE_SECONDS=120) or an active run is claimed/running. Neither
// hermes delivery path keeps turn_busy fresh DURING a long in-flight turn:
//   - hermes-channel.js runs chatStream to completion (could be > 120s) but
//     only pulses turn_busy ONCE at the start, then clears at the end.
//   - hermes-managed-host.js does a FIRE-AND-FORGET prompt.submit and leaves
//     the run `delivered`, relying entirely on the 120s stale window.
// So any hermes turn longer than 120s flips to `online` mid-turn.
//
// FIX: while the bridge KNOWS a turn is in flight, re-pulse turn_busy=true on
// an interval (mirror turn-busy-heartbeat.js / liveness-heartbeat.js). The
// in-flight signal is OWNED BY THE BRIDGE — the pending chatStream promise
// (channel) or the bounded post-submit window (managed-host) — NEVER the
// server's DERIVED status. Re-pulsing off derived status is the 2026-05-23
// feedback-loop trap (a stuck-working-forever bug); this module deliberately
// keys only on real bridge-owned in-flight state, exactly like claude's
// decideRepulse keys on activeRun.status === claimed|running.
//
// File budget per 500-line rule: <= 200 lines.

// Start an in-flight re-pulse beat. While `isInFlight()` returns truthy, call
// `pulse()` every `intervalMs`. Returns a `stop()` that halts the beat. Mirrors
// startTurnBusyHeartbeat but is intentionally a distinct, self-documenting unit
// for the hermes paths (its `isInFlight` is anchored on bridge-owned turn state,
// not on a transcript-mtime probe).
//
// SAFETY: this NEVER beats on its own forever — the caller's `isInFlight`
// (a pending promise, or a bounded deadline) gates every tick. When the turn
// settles, the caller flips `isInFlight` false and/or calls stop(), so the
// server's 120s window closes naturally on the last pulse.
export function startInFlightRepulse({ intervalMs = 45000, isInFlight, pulse } = {}) {
  const noop = () => {};
  if (
    typeof isInFlight !== "function" ||
    typeof pulse !== "function" ||
    !Number.isFinite(intervalMs) ||
    intervalMs <= 0
  ) {
    return noop;
  }
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    let active = false;
    try {
      active = !!(await isInFlight());
    } catch {
      return; // probe failed → treat as not-in-flight this tick
    }
    if (!active) return;
    try {
      await pulse();
    } catch {
      /* best-effort: never let a failed pulse kill the timer */
    }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return function stop() {
    stopped = true;
    clearInterval(timer);
  };
}

// Pure decision for the MANAGED-HOST (fire-and-forget prompt.submit) path:
// given the bridge's own post-submit state, should we still be re-pulsing
// turn_busy right now? The managed-host WS client cannot reliably observe the
// gateway turn-complete event (events route to the TUI transport as owner), so
// the in-flight signal is a BOUNDED window opened at submit time:
//   - submittedAt: epoch ms when prompt.submit was accepted (0 = never).
//   - now: current epoch ms.
//   - maxWindowMs: hard cap so a missed completion CANNOT stick `working`
//     forever — past this the agent falls back to the 120s server window and
//     then to `online`. This is the anti-feedback-loop guard for a path that
//     has no completion event.
//   - completed: set true the moment ANY completion signal is observed (e.g. a
//     best-effort gateway `final` event, or the agent's own reply closing the
//     run); short-circuits the window immediately.
// Returns true only inside an OPEN, uncompleted, un-expired window.
export function shouldManagedHostRepulse({
  submittedAt = 0,
  now = Date.now(),
  maxWindowMs = 15 * 60 * 1000,
  completed = false,
} = {}) {
  const start = Number(submittedAt) || 0;
  if (start <= 0) return false; // no turn submitted → nothing in flight
  if (completed) return false; // observed completion → stop immediately
  const elapsed = Number(now) - start;
  if (!(elapsed >= 0)) return false; // clock skew guard
  return elapsed < (Number(maxWindowMs) || 0);
}
