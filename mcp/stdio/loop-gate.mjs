// Whether a bridge loop may tick — the one question all fourteen loop gates in server.js were asking, and
// the one term every single one of them was missing.
//
// THE DEFECT THIS EXISTS FOR. `shutdownWithStatus()` is async. It sets `shutdownStarted = true`, then
// awaits a great deal of work before the process actually goes: a 1500ms race on `reportResidentLost`,
// `reportEnvironmentOffline()`, `TERMINAL_MANAGER.stopAll()`, `runManagedTeardownForBridge()` (which
// spawnSync-kills detached processes), and four session-shutdown passes. The loop timers keep firing
// through all of it — the terminal-control loop every 800ms, dispatch and spawn every 3s — and not one of
// their gates consulted `shutdownStarted`. So a bridge would report itself OFFLINE and then go on CLAIMING
// work for the next several seconds: spawn requests, dispatch runs, terminal controls, environment
// controls. Each claim is taken by a process that is about to exit and will never execute it, leaving the
// run claimed-but-orphaned until the service's aging backstop requeues it minutes later — the observable
// symptom being a restart that produces no worker.
//
// It could not be a one-line fix per gate and still be a fix: server.js is imported by no test, so a term
// added there is a term nothing checks. The decision moves here, where it can be called.
//
// NEGATIVE NAMING IS DELIBERATE and so is the shape of the test beside it. Every call site reads
// `if (shouldSkipLoop(...)) return;`, matching the `if (...) return;` the gates already used, so the
// rewrite does not invert a condition while relocating it. An inverted gate here would silently disable
// every loop in the bridge, so the tests assert BOTH directions of every term rather than only the skip.

/** The set of terms a caller must state. Kept as data so the guard below cannot drift from the signature. */
const TERMS = ["eligible", "alreadyActive", "shuttingDown"];

/**
 * Should this loop tick be skipped?
 *
 * @param {object} gate
 * @param {boolean} gate.eligible      the loop's OWN preconditions, already conjoined by the caller —
 *                                     `IS_REMOTE`, `IS_ENVIRONMENT_BRIDGE`, `bridgeTerminalSupported()`.
 *                                     Passed as one boolean so each loop keeps its own eligibility rule
 *                                     verbatim instead of this module growing a copy of all of them.
 * @param {boolean} gate.alreadyActive the loop's timer or busy flag — it is already armed or mid-tick.
 * @param {boolean} gate.shuttingDown  `shutdownStarted`. The term that was missing everywhere.
 * @returns {boolean} true when the caller must return without doing work.
 */
export function shouldSkipLoop(gate) {
  // EVERY TERM IS REQUIRED, and omitting one throws rather than defaulting. A default would have to be
  // `false` to be unobtrusive, which for `shuttingDown` means "not shutting down" — silently restoring the
  // exact bug this module was written to remove, at whichever call site forgot to pass it.
  if (!gate || typeof gate !== "object") {
    throw new TypeError("shouldSkipLoop requires a gate object stating every term");
  }
  const missing = TERMS.filter((t) => typeof gate[t] !== "boolean");
  if (missing.length) {
    throw new TypeError(
      `shouldSkipLoop requires a boolean for every term; missing or non-boolean: ${missing.join(", ")}`,
    );
  }
  return !gate.eligible || gate.alreadyActive || gate.shuttingDown;
}

/** The terms, exported so the test can prove the guard covers exactly the documented signature. */
export const LOOP_GATE_TERMS = Object.freeze([...TERMS]);
