// Remembering how a control loop's claim is FAILING, so the log says it once and says it usefully.
//
// Extracted from server.js in v0.5.4. The stateful counterpart to `claim-failure-policy.js`, which is 25
// lines of pure decision-making with no state and no logging -- so this does NOT join it. The policy
// decides whether to warn; this remembers what has happened per loop and does the warning.
//
// KEYED BY LABEL, and that is the point. Two loops report through here ("environment controls" and
// "terminal controls"). A single shared counter would let one loop's outage inflate the other's count and
// warn about a loop that is perfectly healthy -- and a recovery on either would clear both.

import { SERVER_URL, SERVER_URLS, activeServerUrl } from "./aify-service-endpoint.mjs";
import { claimFailureDecision, claimRecoveryDecision } from "./claim-failure-policy.js";

// Exported for tests: the tracker's whole state, so a test can prove an entry is CLEARED on recovery
// rather than merely that nothing was logged.
export const CONTROL_CLAIM_FAILURES = new Map();

export function noteControlClaimFailure(label, error) {
  const previous = CONTROL_CLAIM_FAILURES.get(label) || { count: 0, lastLogAt: 0 };
  const state = { count: previous.count + 1, lastLogAt: previous.lastLogAt };
  const decision = claimFailureDecision(state);
  state.lastLogAt = decision.nextLastLogAt;
  CONTROL_CLAIM_FAILURES.set(label, state);
  const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
  const detail = [...new Set([error?.message, error?.cause?.code, error?.cause?.message].filter(Boolean))].join(": ");
  if (decision.debug && String(process.env.AIFY_DEBUG || "").trim() === "1") {
    console.debug(`[aify] ${label} transient failure against ${target}: ${detail}; retrying`);
  }
  if (decision.warn) {
    console.error(
      `[aify] ${label} unavailable (${state.count} consecutive) against ${target}: ${detail}. ` +
      "Retrying quietly; check that the service is running and reachable from this shell.",
    );
  }
}

export function noteControlClaimSuccess(label) {
  const state = CONTROL_CLAIM_FAILURES.get(label);
  if (!state) return;
  if (claimRecoveryDecision(state.count).log) {
    console.error(`[aify] ${label} recovered after ${state.count} failure(s)`);
  }
  CONTROL_CLAIM_FAILURES.delete(label);
}

// --- the SPAWN claim -------------------------------------------------------
//
// Joined this module in v0.5.4. Same subject — remembering how a claim is failing so the log says it once
// and says it usefully — and the two counters' only direct readers are the two functions below, so the
// group owns them.
//
// THE SHAPE DIFFERS ON PURPOSE and the difference is not an inconsistency to tidy away. The control
// tracker is keyed by LABEL because two loops share it; there is exactly one spawn loop, so this is a
// plain counter. This one also lists the configured fallback URLs when there is more than one, because a
// failing spawn claim is the moment an operator needs to know which hosts were even candidates.

export let spawnClaimFailureCount = 0;
export let spawnClaimLastLogAt = 0;

export function noteSpawnClaimFailure(error) {
  spawnClaimFailureCount += 1;
  const now = Date.now();
  const decision = claimFailureDecision({
    count: spawnClaimFailureCount,
    lastLogAt: spawnClaimLastLogAt,
    now,
  });
  spawnClaimLastLogAt = decision.nextLastLogAt;
  const detail = error?.message || String(error || "unknown error");
  const target = error?.serverUrl || activeServerUrl() || SERVER_URL;
  if (decision.debug && String(process.env.AIFY_DEBUG || "").trim() === "1") {
    console.debug(`[aify] spawn claim transient failure against ${target}: ${detail}; retrying`);
  }
  if (decision.warn) {
    const fallbacks = SERVER_URLS.length > 1 ? `; configured URLs: ${SERVER_URLS.join(", ")}` : "";
    console.error(
      `[aify] spawn claim failed (${spawnClaimFailureCount} consecutive) against ${target}: ${detail}${fallbacks}. ` +
      "The bridge will keep retrying; check that the service is running and reachable from this shell.",
    );
  }
}

export function noteSpawnClaimSuccess() {
  if (spawnClaimFailureCount > 0) {
    if (claimRecoveryDecision(spawnClaimFailureCount).log) {
      console.error(`[aify] spawn claim recovered after ${spawnClaimFailureCount} failure(s)`);
    }
    spawnClaimFailureCount = 0;
    spawnClaimLastLogAt = 0;
  }
}
