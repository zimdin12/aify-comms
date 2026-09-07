// Can this host actually authenticate against the service it is pointed at?
//
// THIS CHECK ASKED THE WRONG QUESTION UNTIL 2026-09-08, and the review that found out said it best:
// "Key presence alone never proves authentication, and MCP configs do not prove standalone-process
// credentials." The first version parsed `~/.claude.json` and `~/.hermes/config.yaml` looking for a
// key NAME. Two things were wrong with that, and the second is the fatal one.
//
// IT WAS FULL OF FALSE GREENS (R6). The detector returned true for `# AIFY_API_KEY: old-key`, for
// `AIFY_API_KEY: "" # no key`, for `NOT_AIFY_API_KEY`, for the token appearing in a description
// outside `env`, and for an `aify-comms` block under the wrong root -- while a correctly quoted entry
// returned null and was then discarded. The gatherer was no better (R5): HTTP 500 and 404 both read
// as "no key required", malformed JSON and EACCES both read as "no clients installed", `HERMES_HOME`
// was ignored so only the legacy path was searched, and a client configured for a DIFFERENT endpoint
// was judged against this service and blamed as keyless.
//
// AND EVEN CORRECT, IT WOULD HAVE MISSED THE DEFECT IT WAS BUILT DURING. The outage was a STANDALONE
// process -- `hermes-managed-host.js run <agent>`, spawned by the launcher -- whose environment
// carried no key at all. No MCP config describes that process. The check would have read both configs,
// found their keys, and reported green while five agents sat unable to claim anything.
//
// SO IT ASKS THE REAL QUESTION NOW, and it has no parser to be wrong. It resolves the key exactly as
// the runtime does -- environment first, then the endpoint-bound credential store, through the same
// module every bridge component uses -- and then TRIES it against the service. That is the same move
// `api-exposure` makes and for the same reason: a question about credentials is answered by making a
// request, not by reading a file and hoping the two agree.
//
// UNAUTHENTICATED FIRST, ALWAYS. A probe carrying a key can only ever answer "yes, with a key", which
// is not the question. Only a successful unauthenticated response establishes that this endpoint
// accepts requests without credentials.
//
// EVERY FAILURE IS TYPED. A transport error, a 500 and a 404 are not evidence that no key is needed;
// they are evidence of nothing, and this row says `unknown` rather than green. That distinction is
// one this repo has already paid for twice, in `env-bridge` and `bridge-current`.

/**
 * What an unauthenticated request tells us about this endpoint.
 *
 * @param {{status: number}|null} response  null when the request could not be made at all
 * @returns {"required"|"open"|"unknown"}
 */
export function credentialPolicyFrom(response) {
  if (!response || typeof response.status !== "number") return "unknown";
  const { status } = response;
  if (status === 401 || status === 403) return "required";
  if (status >= 200 && status < 300) return "open";
  // A 500 says the service is broken and a 404 says we asked the wrong thing. Neither says anything
  // about credentials, and reading either as "open" is how this check reported green on a service
  // that was refusing every call.
  return "unknown";
}

/**
 * @param {object} deps
 * @param {"required"|"open"|"unknown"} deps.policy   what the unauthenticated probe established
 * @param {string} deps.endpoint                      the service this host is pointed at
 * @param {boolean} deps.hasKey                       whether this host resolved a key at all
 * @param {string} deps.keySource                     where that key came from, for the report
 * @param {{status: number}|null} deps.authed         the same request carrying the resolved key
 */
export function clientApiKeyVerdict({
  policy = "unknown", endpoint = "", hasKey = false, keySource = "", authed = null,
} = {}) {
  if (policy === "unknown") {
    return {
      ok: false,
      code: "unknown-all",
      detail: `could not establish whether ${endpoint || "the service"} requires an API key -- an `
        + "unauthenticated probe answered with neither a refusal nor a success, so nothing was "
        + "verified.",
      fix: "Check the `service` row above; an unreachable or erroring service reports here too.",
    };
  }
  if (policy === "open") {
    return {
      ok: true,
      code: "no-key-required",
      detail: `${endpoint} accepts unauthenticated calls, so a host holding no key is correct. `
        + "`api-exposure` is the row that reports what running open costs.",
      fix: "",
    };
  }
  // policy === "required" from here.
  if (!hasKey) {
    return {
      ok: false,
      code: "no-key-resolved",
      detail: `${endpoint} refuses unauthenticated calls and this host resolves NO key -- every call `
        + "it makes comes back 401. A standalone worker fails this way silently: it keeps "
        + "registering, so its `lastSeen` refreshes and its status stays green while it claims "
        + "nothing.",
      fix: "Check that aify-env holds a credential for this endpoint (`aify-env credential`), or "
        + "export AIFY_API_KEY for this process. Re-run install.sh if the registry entry is missing.",
    };
  }
  if (!authed || typeof authed.status !== "number") {
    return {
      ok: false,
      code: "partial",
      detail: `${endpoint} requires a key and this host resolved one from ${keySource || "an unnamed "
        + "source"}, but the authenticated probe could not be completed, so it was never proven to work.`,
      fix: "Re-run when the service is reachable.",
    };
  }
  if (authed.status === 401 || authed.status === 403) {
    return {
      ok: false,
      code: "key-refused",
      detail: `${endpoint} REFUSED the key this host resolves (from ${keySource || "an unnamed source"}). `
        + "Clients hold one key and the service runs on another, which reads as a total outage while "
        + "both halves look correctly configured.",
      fix: "Re-run install.sh so the client and the service agree, or check API_KEY in the service's "
        + ".env against aify-env's credential for this endpoint.",
    };
  }
  if (authed.status >= 200 && authed.status < 300) {
    return {
      ok: true,
      code: "authenticated",
      detail: `${endpoint} requires a key and the one this host resolves (from ${keySource || "an "
        + "unnamed source"}) is accepted.`,
      fix: "",
    };
  }
  return {
    ok: false,
    code: "partial",
    detail: `${endpoint} requires a key and the authenticated probe answered ${authed.status}, which `
      + "neither accepts nor refuses the credential.",
    fix: "Check the `service` row above.",
  };
}
