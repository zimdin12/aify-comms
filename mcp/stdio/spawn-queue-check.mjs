// Spawn requests that were CLAIMED and never started.
//
// WHY THIS EXISTS, and it is the review's own argument. External review, Round 8 M9: the doctor reads
// NO spawn-request state at all -- zero hits across `doctor.js`, every `*-check.mjs` and every
// predicate module. So a request that a host claimed and then failed to act on is invisible to every
// instrument, and the operator sees a spawn that was accepted and never appears.
//
// AND IT IS WHY H2 WAS INVISIBLE. That defect stopped a host's claim loop dead on one failed report,
// while the SEPARATE heartbeat loop kept `bridgeLastSeen` fresh -- so `env-bridge` passed,
// `spawn-delegation` passed, `bridge-current` passed, and the queue simply never drained. Every row
// in the doctor was green because every row was asking a different question. This is the question
// nobody was asking: is there work that was taken and not done.
//
// IT REPORTS, IT NEVER ACTS. A claimed request is somebody's in-flight work, and this project's
// standing rule is that refusing is recoverable and killing is not -- so this names rows and their
// ages and leaves them alone. `SPAWN_ORPHAN_GRACE_SECONDS` (180) is the service's own window for the
// same question, read rather than re-invented: a second number here would drift from the one that
// actually requeues.
//
// UNKNOWN IS NOT OK. A listing that could not be fetched proves nothing, and this repo has fixed
// green-by-default in `env-bridge` and `bridge-current` already. No evidence gets its own code.

/** The service's own grace window before a claimed-but-unstarted request is considered orphaned. */
export const CLAIMED_GRACE_SECONDS = 180;

/** Statuses that mean the request has been taken by a host but is not yet a running process. */
export const TAKEN_NOT_RUNNING = Object.freeze(["claimed", "starting"]);

/**
 * @param {object} deps
 * @param {() => Promise<{ok: boolean, spawnRequests?: any[]}>} deps.list  GET /spawn-requests
 * @param {() => number} [deps.now]
 */
export async function spawnQueueVerdict({ list, now = Date.now } = {}) {
  let answer;
  try {
    answer = await list();
  } catch (error) {
    return {
      ok: false,
      code: "unknown-all",
      detail: "the spawn-request queue could not be read, so nothing here was verified: "
        + String(error?.message || error),
      fix: "Fix the named condition and re-run. A check that gathered no evidence is not a pass.",
    };
  }

  const rows = Array.isArray(answer?.spawnRequests) ? answer.spawnRequests : [];
  const stuck = [];
  for (const row of rows) {
    const status = String(row?.status || "").trim().toLowerCase();
    if (!TAKEN_NOT_RUNNING.includes(status)) continue;
    // THE CLAIM TIME, not the creation time: a request can sit `queued` for as long as the operator
    // likes without anything being wrong. What this measures is the gap between a host TAKING the
    // work and the work starting.
    const claimedAt = Date.parse(String(row?.claimedAt || row?.claimed_at || "").trim());
    if (Number.isNaN(claimedAt)) continue;
    const ageSeconds = Math.round((now() - claimedAt) / 1000);
    if (ageSeconds >= CLAIMED_GRACE_SECONDS) {
      stuck.push({ id: String(row?.id || ""), status, ageSeconds, environmentId: String(row?.environmentId || "") });
    }
  }

  if (!stuck.length) {
    return {
      ok: true,
      code: rows.length ? "ok" : "empty",
      detail: rows.length
        ? `${rows.length} spawn request(s); none claimed and unstarted past ${CLAIMED_GRACE_SECONDS}s.`
        : "no spawn requests on this service.",
      fix: "",
    };
  }

  const named = stuck
    .sort((a, b) => b.ageSeconds - a.ageSeconds)
    .map((r) => `${r.id || "(no id)"} ${r.status} for ${r.ageSeconds}s on ${r.environmentId || "(no env)"}`);
  return {
    ok: false,
    code: "stuck-claims",
    detail: `${stuck.length} spawn request(s) were CLAIMED by a host and never started: ${named.join(", ")}. `
      + "The host took the work and did not do it, which every other row in this report reads as "
      + "healthy -- the heartbeat is a separate loop from the claim loop.",
    fix: "Check that host's aify-env log for a claim loop that stopped. Restarting aify-env there "
      + "releases the claims; the service requeues them after its own grace window.",
  };
}
