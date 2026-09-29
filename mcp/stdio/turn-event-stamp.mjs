// When, and on which host, a bridge observed the turn state it is about to report.
//
// The service applies turn events in the order they were OBSERVED, not the order they arrive
// (service/api_core/hook_event_order.py). The hooks have always sent `firedAtUs` and `machineId`; the
// bridge-side detectors, hermes' `clearTurn` and the heartbeat that starts a turn sent neither, so a
// detector's end observed before the next turn started could land after it and clear it (0.7.6 review,
// O2). Every one of them now takes its stamp here, at the moment it reports, before any await: the
// read it reports on has just returned, and what the stamp must beat is the transit delay after it.
//
// Microseconds on the host's wall clock, the same clock the hooks read through `$EPOCHREALTIME`, so a
// hook's time and a detector's are comparable. Times are never compared across hosts; `machineId` is
// what lets the service tell.

import { defaultMachineId } from "./machine-id.mjs";

const THIS_HOST = defaultMachineId();

/**
 * The host wall clock in integer microseconds. `clockMs` is injectable so a test can fix it.
 *
 * Date.now(), NOT performance.timeOrigin + performance.now(). That sum is a monotonic clock anchored
 * once at process start, and it drifts from the wall clock the hooks read: measured on a WSL host at
 * 1.7 s every few seconds, ~200 s after an hour. The service orders both in one sequence, so a
 * long-lived bridge's events read as the future and a relaunched one's turn-ends were refused
 * (external review, 2026-09-29, HIGH). Milliseconds are coarser than the hooks' microseconds; an
 * equal time still lets a turn-end through (hook_event_order.py).
 */
export function hostNowUs(clockMs = () => Date.now()) {
  return Math.round(clockMs() * 1000);
}

/** `{ firedAtUs, machineId }` for a turn event reported now, from this host. */
export function turnEventStamp({ firedAtUs = hostNowUs(), machineId = THIS_HOST } = {}) {
  return { firedAtUs, machineId };
}
