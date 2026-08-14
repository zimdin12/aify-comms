// Remembering how a control loop's claim is FAILING, so the log says it once and says it usefully.
//
// Extracted from server.js in v0.5.4. The stateful counterpart to `claim-failure-policy.js`, which is 25
// lines of pure decision-making with no state and no logging -- so this does NOT join it. The policy
// decides whether to warn; this remembers what has happened per loop and does the warning.
//
// KEYED BY LABEL, and that is the point. Two loops report through here ("environment controls" and
// "terminal controls"). A single shared counter would let one loop's outage inflate the other's count and
// warn about a loop that is perfectly healthy -- and a recovery on either would clear both.

import { SERVER_URL, activeServerUrl } from "./aify-service-endpoint.mjs";
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
