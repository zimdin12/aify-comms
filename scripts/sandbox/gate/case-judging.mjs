// How the gate turns one scenario's evidence into a case verdict: the case's fixture checks first, then its judge.
// A fixture check refuses evidence whose run did not construct what the case needs (review section 6), by name, so a
// fixture that failed to separate two phases can never read as the product passing or failing the case.

import { FIRST_PASS_MS } from "./judges.mjs";

/** env-resumed's phase discriminator (review section 6.1): after the resume, comms must have accepted a fresh BUSY
 *  record, the agent must have stayed busy for a whole claim pass, and only then may idle have been requested and
 *  accepted. A run whose fresh and idle acceptances coincide cannot tell "waited for fresh" from "waited for idle". */
function resumeSeparatesFreshBusyFromIdle(m) {
  const fresh = m?.freshBusyAccepted, idle = m?.idleAccepted;
  if (!fresh) return "fixture: no fresh busy record was accepted after the resume";
  if (!idle) return "fixture: no idle record was accepted after the fresh busy one";
  if (!(idle.at > fresh.at && idle.seenAt > fresh.seenAt)) {
    return "fixture: the resume did not separate fresh-busy from idle";
  }
  if (!(m.idleRequestedAt - fresh.seenAt >= FIRST_PASS_MS)) {
    return "fixture: the fresh-busy phase was shorter than one claim pass";
  }
  return null;
}

/** Each case's fixture checks, by case id. A check returns the reason it refuses, or null. */
export const FIXTURE_CHECKS = { "env-resumed": [resumeSeparatesFreshBusyFromIdle] };

/** One case's verdict: a scenario error or missing evidence fails it, a refused fixture fails it by name, and only
 *  evidence every fixture check admits reaches the case's judge. */
export function judgeCase({ id, error, evidence, judges }) {
  if (error || !evidence) return { pass: false, failed: [`scenario error: ${error ?? "no evidence"}`], unknown: [] };
  const refused = (FIXTURE_CHECKS[id] ?? []).map((check) => check(evidence.marks)).filter(Boolean);
  if (refused.length) return { pass: false, verdict: "FAIL", failed: refused, unknown: [] };
  return judges[id](evidence);
}
