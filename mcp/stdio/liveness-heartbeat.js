// Unconditional liveness beat. Distinct from turn-busy-heartbeat.js (which is
// gated on isActive): this fires for as long as the process lives so the
// service can treat bridge last_seen as a true "alive now" signal. See
// docs/superpowers/plans/2026-06-01-status-liveness-worker-hygiene.md.
//
// File budget per 500-line rule: <=100 lines.

export function startLivenessHeartbeat({ intervalMs = 30000, beat } = {}) {
  if (typeof beat !== "function") throw new Error("startLivenessHeartbeat: beat required");
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    try { await beat(); } catch { /* never let a failed beat kill the timer */ }
  };

  // beat once immediately so a freshly-started process is live without waiting
  // a full interval
  void tick();

  // Honor the provided intervalMs directly; guard only against non-positive/NaN
  // (production callers pass 30000; tests may use small values for speed).
  const ms = Number.isFinite(intervalMs) && intervalMs > 0 ? intervalMs : 30000;
  const timer = setInterval(tick, ms);
  if (typeof timer.unref === "function") timer.unref();

  return function stop() {
    stopped = true;
    clearInterval(timer);
  };
}
