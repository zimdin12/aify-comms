// The `api-exposure` check: is the fleet listing open, and does it hand out credentials when it is?
//
// EXTRACTED SO IT CAN BE EXECUTED, for the reason `service-check.mjs` and `env-processes-check.mjs`
// both record: `doctor.js` runs its whole sequence at module scope and ends in `process.exit()`, so a
// check written inline there can be asserted ABOUT and never CALLED. That is how the service check's
// early return came to bypass its own verdict with every test green.
//
// Every collaborator is a parameter with no default, so a caller cannot accidentally reach the
// operator's own service.

import { apiExposureVerdict, credentialBearingRows } from "./api-exposure.mjs";

/**
 * @param deps.fetchJson  fetch a JSON URL outright, returning null when it does not answer. This
 *                        check needs a request the service's OWN key is NOT attached to -- `get`
 *                        carries it, and asking with a key can only ever answer "yes, with a key".
 * @param deps.baseUrl    the service base, e.g. http://127.0.0.1:8800
 * @param deps.add        record a check result
 */
export async function checkApiExposure({ fetchJson, baseUrl, add }) {
  // THE ID IS WRITTEN AT EACH CALL SITE, not hidden in a helper that spreads positional arguments.
  // `test_the_doctor_table_lists_the_real_checks.py` derives which ids the doctor EMITS by reading
  // these call sites, so a check whose id only exists inside a `toArgs` is one the documentation gate
  // cannot see -- it went red on exactly that, correctly, and a tidier helper is not worth being
  // invisible to the instrument that keeps the docs honest.
  const report = (verdict) => add("api-exposure", verdict.ok, verdict.code, verdict.detail, verdict.fix);

  const body = await fetchJson(`${baseUrl}/api/v1/agents`);
  if (!body) {
    // Two different situations and only one is good news: the service is down, or it REFUSED the
    // keyless request. They are told apart by the shape of what came back, and when they cannot be,
    // the verdict is `unknown` rather than a guess in either direction.
    return report(apiExposureVerdict({ unauthenticatedRead: null }));
  }
  if (body.error || body.detail) {
    // A 401 body. The listing wanted a key, which is the answer this check hopes for.
    return report(apiExposureVerdict({ unauthenticatedRead: false, credentialRows: 0, totalRows: 0 }));
  }
  const { credentialRows, totalRows } = credentialBearingRows(body.agents);
  return report(apiExposureVerdict({ unauthenticatedRead: true, credentialRows, totalRows }));
}
