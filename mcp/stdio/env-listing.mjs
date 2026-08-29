// Reading a process listing out of what `EnvClient` actually returns.
//
// THE DEFECT THIS EXISTS FOR, executed 2026-08-29 before it was written. `EnvClient.#request` answers
// `{ ok: true, handle: <body> }` or `{ ok: false, error }`. Three readers unwrapped that by hand and
// two of them got it wrong:
//
//     processStillListed   `listing?.handle ?? listing`               correct
//     probeEnvTerminal     `result.handle`, after checking ok         correct
//     reconcileLabels      `listing?.processes`                       ALWAYS undefined
//
// So `reconcileLabels` -- the thing that keeps aify-env's AGENT column right when a label drifts, the
// column the operator asked for by name -- read `processes` off the envelope, got undefined, fell
// through to `[]`, and pushed nothing. Ever. Measured with the real shape: `{pushed: 0, failed: 0}`
// against a listing that plainly contained a process needing a label, while the pure `labelsToPush`
// returned the correct push for the same data. A green helper and a call site wired to nothing, which
// is a shape this repo has shipped before and now has a rule about.
//
// It went unnoticed because the SPAWN path sets the label directly, so the common case worked and only
// drift repair was dead.
//
// AND A REFUSAL IS NOT AN EMPTY LISTING. `{ok: false}` and a listing with no processes produced the
// identical `{pushed: 0, failed: 0}` -- an environment that never answered, reported as an environment
// with nothing to do. `processStillListed` already keeps those apart and calls collapsing them "an
// absence of signal read as a positive fact". One reader, so all three agree by construction rather
// than by three people remembering.

/**
 * @typedef {{processes: object[]|null, refused: boolean}} EnvListing
 *   `processes` is null when the listing could not be READ -- refused, or a body in a shape this
 *   cannot parse. An empty array means aify-env answered and owns nothing, which is a different fact
 *   and leads to different behaviour in every consumer.
 */

/**
 * The processes in an `EnvClient` response, or null if there was no readable listing.
 *
 * Accepts the envelope, a bare body, or a bare array, because `/health` and `/processes` both carry a
 * `processes` key and a caller should not have to know which one it asked.
 *
 * @param {{ok?: boolean, handle?: any}|any} result
 * @returns {EnvListing}
 */
export function envListing(result) {
  if (result?.ok === false) return { processes: null, refused: true };
  const body = result?.handle ?? result;
  if (Array.isArray(body)) return { processes: body, refused: false };
  const processes = body?.processes;
  if (Array.isArray(processes)) return { processes, refused: false };
  return { processes: null, refused: false };
}
