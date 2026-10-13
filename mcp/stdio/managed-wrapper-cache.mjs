// The managed-via-wrapper runtime set, cached. Extracted from server.js in v0.5.4.
//
// The dispatch loop asks this on every claim to know which runtimes to SKIP — those are claimed by a
// wrapper's own child bridge instead. Two properties carry the risk and neither is visible from the call
// site: the 5-second cache is what stops a claim loop hammering /settings, and the catch returns the
// STALE set rather than an empty one, because an empty set means "skip nothing" and would have this
// bridge claim work belonging to a wrapper's child during any blip on /settings.

import { httpCall } from "./aify-service-endpoint.mjs";
import { WRAPPER_SET_UNKNOWN, managedViaWrapperRuntimesFromSettingsResponse } from "./managed-wrapper-settings.js";

// Unified-backing refactor 2026-05-24: read the `managed_via_wrapper` setting
// so the dispatch loop knows which runtimes to skip claiming for (the
// wrapper's child bridge claims those). 5s cache to avoid hammering /settings.
// Unknown until a read succeeds: a cold bridge whose first read fails must not read that as "none".
let _managedViaWrapperCache = { fetchedAt: 0, runtimes: WRAPPER_SET_UNKNOWN };
export async function readManagedViaWrapperRuntimes() {
  if (Date.now() - _managedViaWrapperCache.fetchedAt < 5000) {
    return _managedViaWrapperCache.runtimes;
  }
  try {
    const resp = await httpCall("GET", "/settings");
    const set = managedViaWrapperRuntimesFromSettingsResponse(resp);
    if (set === WRAPPER_SET_UNKNOWN) return _managedViaWrapperCache.runtimes; // malformed: as a failed read
    _managedViaWrapperCache = { fetchedAt: Date.now(), runtimes: set };
    return set;
  } catch (_) {
    return _managedViaWrapperCache.runtimes; // best-effort: return stale cache
  }
}
