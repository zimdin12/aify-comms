// Which runtime controllers are mid-turn right now.
//
// A `Set` of unresolved controller `start()` promises, and the two operations over it. This is PROCESS
// TRUTH rather than derived status: a promise is in the set exactly while the turn it represents is
// running, so nothing has to infer liveness from timestamps or hook firing.
//
// WHAT IT IS FOR, because a bare Set of promises does not say. The turn-busy heartbeat POSTs
// `turn_busy=1` every 30s while any controller is active, which is what keeps a long turn reading as
// `working` instead of flapping to `online` between hook events — the operator-observed failure this was
// added to fix (Plan 4 Task 13, 2026-05-25). Native runtimes only: codex, pi and hermes have controllers;
// claude has none and carries its liveness a different way.
//
// v0.5.4 layer 0 of the server.js decomposition. Three call sites mark a start — a turn handle, the main
// dispatch loop, and local-mode auto-start — and one reads whether anything is active. That last reader
// is why THE SET STAYS PRIVATE: two functions cover every call site, so the collection itself need not
// leave this module. Compare `bridge-agent-state.mjs`, which does export its Maps because thirty readers
// mutate them directly and a wrapper API would have to be thirty functions wide. Exporting mutable state
// is a fallback, not the default.
//
// SELF-CLEANING, and that is the property to preserve. `promise.then(cleanup, cleanup)` removes the entry
// on BOTH settle paths, so a rejected turn cannot leave the heartbeat believing work is still running —
// which would pin an agent at `working` with nothing to clear it. A single-argument `.then(cleanup)` would
// leak on rejection, silently and only for failing turns.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

const ACTIVE_CONTROLLER_PROMISES = new Set();

// Returns the promise it was given, so a caller can mark and forward in one expression.
//
// Named `__markControllerStart` in server.js and kept under that name here: renaming it would touch
// three call sites for no behavioural reason, and this was a structural slice. The leading underscores
// read oddly on an exported function and are worth revisiting deliberately, not as a side effect.
export function __markControllerStart(promise) {
  if (!promise || typeof promise.then !== "function") return promise;
  ACTIVE_CONTROLLER_PROMISES.add(promise);
  const cleanup = () => { ACTIVE_CONTROLLER_PROMISES.delete(promise); };
  promise.then(cleanup, cleanup);
  return promise;
}

// The heartbeat's `isActive`. A predicate rather than the Set, so no caller can add to it without going
// through the bookkeeping above — an entry added without its cleanup would never be removed.
export function anyControllerActive() {
  return ACTIVE_CONTROLLER_PROMISES.size > 0;
}
