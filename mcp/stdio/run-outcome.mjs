// How a managed run's outcome is reported: success bookkeeping and failure bookkeeping are SEPARATE
// branches of the run's own promise.
//
// It was `promise.then(success).catch(failure)`, so a throw anywhere in the SUCCESS bookkeeping --
// after the run had already been PATCHed `completed` -- ran the failure branch, which PATCHed an error
// and appended a `failed` event to a run that succeeded (v0.7 scan B11). The service keeps the terminal
// status, so the harm was a false audit trail: "completed ... failed". claude-channel.js fixed the same
// shape for batches in 2026-07. A throw in either branch is logged, never swallowed.

export function followRunOutcome(promise, { succeed, fail, always, log = console.error }) {
  return promise
    .then(succeed, fail)
    .catch((error) => log("[aify] run bookkeeping failed after the run settled:", error?.message || error))
    .finally(always);
}
