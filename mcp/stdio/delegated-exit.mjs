// Whether a delegated terminal actually ENDED, or we merely stopped being able to see it.
//
// THE ORPHAN FACTORY, traced on the operator's host 2026-08-28 from the terminal's own event log:
//
//     18:24:52  the process starts under aify-env as p1, pid 155844
//     18:32:44  terminal_output ... and the row is marked `stopped`
//     18:32:45  terminal_consistency_repaired
//     18:34:25  reconciled_managed_orphan_worker
//
// The operator had killed aify-env at 18:32. Every delegated output stream ended at once, and the
// bridge reported each terminal as exited. aify-env's own shutdown deliberately leaves children
// running when it cannot confirm their stops -- it keeps the owned record so the next instance can
// reap them -- so the processes survived. aify-env came back, correctly re-owned pid 155844, and the
// control plane has said `stopped` about a live, owned process ever since.
//
// THE DISTINCTION WAS ALREADY CARRIED AND THEN THROWN AWAY. `env-client.mjs` says it out loud where
// the stream ends: "The stream closed with no exit frame. aify-env sends one and then ends, so
// reaching here means the environment went away rather than the process finishing." It then calls the
// same `finish(null)` a real exit uses, and `_handleExit` finalises either way.
//
// WHICH WAY TO BE WRONG, because one of the two is recoverable and the other is not:
//
//   say STOPPED about a live process  -> an orphan. Nothing collects it. The agent reads `available`
//                                        because the orphan heartbeats on its own behalf, work routes
//                                        to a session that cannot run, and only a human notices.
//   say ATTACHED about a dead process -> a stale row. `terminal_consistency.py`, `terminal_runs.py`
//                                        and `managed_workers.py` are all built to heal exactly that.
//
// So an unobserved exit is never asserted. The environment is ASKED first, and silence holds the row
// rather than closing it.

import { envListing } from "./env-listing.mjs";

/**
 * @typedef {"exited"|"alive"|"unknown"} DelegatedExitKind
 *   exited  -- aify-env said so, or said the process is gone. Finalise.
 *   alive   -- the process is still listed. The stream broke, not the process. Do NOT finalise.
 *   unknown -- nobody could say. Do NOT finalise: see the asymmetry above.
 */

/**
 * Did this terminal end?
 *
 * PURE. The listing is the caller's job, so every combination is drivable without an environment.
 *
 * @param {object} input
 * @param {boolean} input.observedExitFrame  aify-env sent `event: exit` for this process
 * @param {boolean|null} input.stillListed   is the process still in aify-env's listing; null = could not ask
 * @returns {{kind: DelegatedExitKind, finalise: boolean, reason: string}}
 */
export function delegatedExitVerdict({ observedExitFrame = false, stillListed = null } = {}) {
  if (observedExitFrame) {
    // THE ONLY POSITIVE EVIDENCE OF AN EXIT there is. aify-env watched the process end and said so.
    return { kind: "exited", finalise: true, reason: "aify-env reported the process exited" };
  }
  if (stillListed === true) {
    return {
      kind: "alive",
      finalise: false,
      reason: "the output stream ended but aify-env still owns this process, so the stream broke "
        + "rather than the process ending",
    };
  }
  if (stillListed === false) {
    // No frame AND not listed. The process is gone; we simply did not watch it go, which is worth
    // saying because a null exit code is not the same fact as an observed 0.
    return {
      kind: "exited",
      finalise: true,
      reason: "the output stream ended and aify-env no longer lists the process",
    };
  }
  return {
    kind: "unknown",
    finalise: false,
    reason: "the output stream ended and aify-env could not be asked whether the process survived",
  };
}

/**
 * Is this process still in aify-env's listing? `null` when the question could not be put.
 *
 * THE THREE ANSWERS STAY THREE. "not listed" and "could not ask" lead to opposite decisions above,
 * and collapsing them is how the original defect worked: an absence of signal read as a positive
 * fact.
 *
 * @param {{list: () => Promise<any>}|null} client
 * @param {string} processId
 * @returns {Promise<boolean|null>}
 */
export async function processStillListed(client, processId) {
  const id = String(processId ?? "").trim();
  if (!client || typeof client.list !== "function" || !id) return null;
  let listing;
  try {
    listing = await client.list();
  } catch {
    return null;
  }
  // THROUGH THE SHARED READER. This unwrap was correct here and wrong in `label-reconciler.mjs`,
  // which is what a hand-written envelope read costs: it agrees until one of the three copies is
  // written by somebody who has not read the other two. A refusal is not an empty listing.
  const { processes, refused } = envListing(listing);
  if (refused || !processes) return null;
  return processes.some((process_) => String(process_?.id ?? "").trim() === id);
}
