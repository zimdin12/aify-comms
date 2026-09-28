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

/** The host wall clock in integer microseconds. `clockMs` is injectable so a test can fix it. */
export function hostNowUs(clockMs = () => performance.timeOrigin + performance.now()) {
  return Math.round(clockMs() * 1000);
}

/** `{ firedAtUs, machineId }` for a turn event reported now, from this host. */
export function turnEventStamp({ firedAtUs = hostNowUs(), machineId = THIS_HOST } = {}) {
  return { firedAtUs, machineId };
}
