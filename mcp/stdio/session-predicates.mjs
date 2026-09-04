// Predicates over managed session state.
//
// Extracted from server.js in v0.5.4, following the `doctor-predicates.js` pattern: logic that lives in
// the bridge is only reachable through a running bridge, so it can only fail in production.
//
// `isActiveManagedSessionStatus` decides whether a session still counts as ALIVE. Everything downstream
// of it — reaping, adoption, whether a send cold-starts a worker or waits — keys on that answer, so both
// directions are load-bearing: a status wrongly called active leaves work queued behind a dead worker,
// and one wrongly called inactive gets a live worker reaped out from under its run.

export function isActiveManagedSessionStatus(status) {
  return ["starting", "running", "recovering", "restarting"].includes(String(status || "").toLowerCase());
}

/**
 * Whether THIS process must host its own dispatch loop.
 *
 * MOVED HERE in v0.6.2 from `managed-teardown-ownership.js`, which held it beside the environment
 * bridge's ownership and bootstrap machinery. That machinery went with the bridge and this was the
 * only survivor -- a three-line predicate in a file named for teardown ownership is a file nobody
 * would look in, so it sits with the other session predicate instead.
 *
 * A resident with an agent id AND channels enabled is woken by its channel; anything else has to
 * poll for its own work.
 */
export function localAgentNeedsDispatchHosting({ agentId = "", channelsEnabled = false } = {}) {
  return !(String(agentId || "").trim() && channelsEnabled === true);
}
