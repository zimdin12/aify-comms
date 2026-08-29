// Is the fleet listing readable without a key, and does it hand out live credentials when it is?
//
// MEASURED ON THE OPERATOR'S HOST, 2026-08-29. `GET /api/v1/agents` answered 200 with no key and 200
// with a deliberately wrong one, and 16 of 47 agent rows carried a `runtimeConfig.gatewayUrl`
// containing a live `token=`. The container publishes 8800 on 0.0.0.0.
//
// NEITHER HALF IS A DEFECT ON ITS OWN, and that is the whole design of this check.
//
//   * No API key is a CONFIGURATION. `main.py` installs `APIKeyMiddleware` only when `config.api_key`
//     is set, and it is empty here. A loopback-only deployment can reasonably run open, and a check
//     that fired on every such deployment is one an operator learns to skim -- this repo's own rule.
//   * The token is in the listing because a FEATURE NEEDS IT. `hermesGatewayUrlToHttp` pulls `token`
//     out of that URL to build the clickable link that opens an agent's hermes TUI. Redacting it
//     would remove the exposure and the feature together, so this reports rather than redacts.
//
// It is the COMBINATION that is worth a line: an unauthenticated endpoint that returns working
// credentials. That is not a thing anybody chose; it is two reasonable choices meeting.
//
// AND IT IS A DECISION, NOT A REPAIR. Requiring a key, binding to loopback, or moving the token to a
// per-agent read are all changes with consequences for bridges, for remote environments and for the
// console link. None of them is a tool's to make, which is why this says what is true and stops.
//
// EACH ONE NAMES ITS COST, and the first did not until 2026-08-29. `API_KEY` reads like the cheap
// option -- the bridges are handed it at install -- and the DASHBOARD is not: it sends
// `X-Aify-Operator-Key` and never `X-API-Key`, the dashboard app has no proxy route to attach one
// server-side, and `APIKeyMiddleware` exempts `/ws` but nothing under `/api/v1`. So the page keeps a
// live socket and loses every poll, which is the most confusing of the three failures on offer. A
// remedy whose cost is unstated is the one an operator picks first.
//
// A CORRECTION I MADE TO MYSELF WHILE WRITING THIS, because it nearly became the finding. My first
// probe reported `API_KEY` as SET in the container -- from a shell test whose quoting made it check a
// literal string rather than the variable. Read properly it is empty. "The operator configured a key
// and the service ignores it" would have been a serious and false claim, and the instrument that
// produced it looked exactly like one that worked.

/**
 * @typedef {object} ApiExposureInput
 * @property {boolean|null} unauthenticatedRead  did a keyless request succeed; null = could not ask
 * @property {number|null} credentialRows        agent rows carrying a live credential; null = unknown
 * @property {number|null} totalRows             agent rows seen; null = unknown
 */

/**
 * @param {ApiExposureInput} input
 * @returns {{ok: boolean, code: string, detail: string, fix: string}}
 */
export function apiExposureVerdict({
  unauthenticatedRead = null, credentialRows = null, totalRows = null,
} = {}) {
  if (unauthenticatedRead === null || credentialRows === null) {
    // NO EVIDENCE IS NOT A PASS. This repo's `env-bridge` and `bridge-current` both shipped
    // green-by-default and both were wrong the same way.
    return {
      ok: false, code: "unknown",
      detail: "Could not establish whether the fleet listing is readable without a key. Nothing was "
        + "verified, so this is not a clean result.",
      fix: "Check the `service` row above; this check needs the service to answer.",
    };
  }
  if (!unauthenticatedRead) {
    return {
      ok: true, code: "authenticated",
      detail: "The fleet listing requires an API key.",
      fix: "",
    };
  }
  if (credentialRows <= 0) {
    // Open, but handing out nothing. An operator who chose this gets one calm sentence, not a warning.
    return {
      ok: true, code: "open-no-credentials",
      detail: `The fleet listing is readable without an API key, and no agent row carries a live `
        + `credential (${totalRows ?? 0} row(s) checked). That is a configuration, not a leak.`,
      fix: "",
    };
  }
  return {
    ok: false, code: "open-with-credentials",
    detail: `The fleet listing is readable without an API key AND ${credentialRows} of `
      + `${totalRows ?? "?"} agent row(s) carry a live gateway token. Anything that can reach this `
      + "port can read working credentials for those agents.",
    fix: "Three ways out, all of them decisions rather than repairs: set API_KEY so the service "
      + "requires one -- every bridge is given it at install, but the DASHBOARD is not, so every one "
      + "of its /api/v1 polls would answer 401 while /ws stays exempt, leaving a page that reports a "
      + "live connection over no data until it is given a key too; publish the port on 127.0.0.1 "
      + "instead of 0.0.0.0, which costs remote environments; or move the gateway token off the fleet "
      + "listing, which costs the dashboard's one-click hermes console link.",
  };
}

//: What a live credential looks like in a value the listing returns. DERIVED SHAPES, not field names:
//: the exposure found was in `runtimeConfig.gatewayUrl`, and naming that field would have missed the
//: next one. A caller walks the whole row.
const CREDENTIAL_SHAPES = [
  /[?&]token=[^&\s"']+/i,
  /[?&]key=[^&\s"']+/i,
  /\bBearer\s+\S+/i,
];

/**
 * Does this string carry something usable as a credential?
 *
 * Deliberately narrow. "secret" appearing in prose is not a credential, and a check that flagged it
 * would produce a count nobody trusts -- and a count nobody trusts is one nobody reads.
 *
 * @param {unknown} value
 * @returns {boolean}
 */
export function looksLikeCredential(value) {
  if (typeof value !== "string" || !value) return false;
  return CREDENTIAL_SHAPES.some((shape) => shape.test(value));
}

/**
 * How many rows in an agent listing carry one, and how many rows there were.
 *
 * @param {object|null} agents  the `agents` map from GET /agents
 * @returns {{credentialRows: number|null, totalRows: number|null}}
 */
export function credentialBearingRows(agents) {
  if (!agents || typeof agents !== "object") return { credentialRows: null, totalRows: null };
  const rows = Array.isArray(agents) ? agents : Object.values(agents);
  let credentialRows = 0;
  for (const row of rows) {
    if (walk(row)) credentialRows += 1;
  }
  return { credentialRows, totalRows: rows.length };
}

/** Depth-first over a row's values. The field that leaked was two levels down. */
function walk(value, depth = 0) {
  if (depth > 6) return false;
  if (looksLikeCredential(value)) return true;
  if (Array.isArray(value)) return value.some((item) => walk(item, depth + 1));
  if (value && typeof value === "object") return Object.values(value).some((item) => walk(item, depth + 1));
  return false;
}
