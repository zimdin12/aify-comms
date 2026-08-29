// The fleet listing is readable without a key, and it returns live gateway tokens. Both, or neither.
//
// MEASURED ON THE OPERATOR'S HOST, 2026-08-29. `GET /api/v1/agents` answered 200 with no key and 200
// with a deliberately wrong one; 16 of 47 agent rows carried a `runtimeConfig.gatewayUrl` containing a
// live `token=`; the container publishes 8800 on 0.0.0.0.
//
// NEITHER HALF IS A DEFECT ALONE, and that is the whole design.
//
//   * No API key is a CONFIGURATION. `main.py` installs `APIKeyMiddleware` only when `config.api_key`
//     is set, and it is empty here. A loopback-only deployment can reasonably run open, and a check
//     firing on every such deployment is one an operator learns to skim.
//   * The token is in the listing because a FEATURE NEEDS IT: `hermesGatewayUrlToHttp` pulls `token`
//     out of that URL to build the link that opens an agent's hermes TUI. Redacting it would remove
//     the exposure and the feature together.
//
// It is the COMBINATION that earns a line: an unauthenticated endpoint returning working credentials.
// Nobody chose that; it is two reasonable choices meeting.
//
// A CORRECTION I MADE TO MYSELF, because it nearly became the finding. My first probe reported
// `API_KEY` as SET in the container -- from a shell test whose quoting made it check a literal string
// rather than the variable. Read properly it is empty. "The operator configured a key and the service
// ignores it" would have been a serious and false claim, from an instrument that looked like it worked.
import assert from "node:assert/strict";
import { test } from "node:test";

import { apiExposureVerdict, credentialBearingRows, looksLikeCredential } from "../api-exposure.mjs";
import { checkApiExposure } from "../api-exposure-check.mjs";

const TOKENED = "ws://127.0.0.1:9341/api/ws?token=abc123";

// ---- the verdict ---------------------------------------------------------------------------------

test("THE COMBINATION IS THE FINDING", () => {
  const verdict = apiExposureVerdict({ unauthenticatedRead: true, credentialRows: 16, totalRows: 47 });
  assert.equal(verdict.ok, false);
  assert.equal(verdict.code, "open-with-credentials");
  assert.match(verdict.detail, /16 of 47/);
});

test("an open listing with nothing to leak is a configuration, not an alarm", () => {
  // A check that fired on every keyless deployment is one nobody reads, which is this repo's own rule
  // about alarms. Said calmly and passed.
  const verdict = apiExposureVerdict({ unauthenticatedRead: true, credentialRows: 0, totalRows: 12 });
  assert.equal(verdict.ok, true);
  assert.equal(verdict.code, "open-no-credentials");
});

test("a listing that wants a key passes whatever it carries", () => {
  assert.equal(apiExposureVerdict({
    unauthenticatedRead: false, credentialRows: 99, totalRows: 99,
  }).ok, true);
});

test("NO EVIDENCE IS NOT A PASS", () => {
  // `env-bridge` and `bridge-current` both shipped green-by-default in this repo and both were wrong
  // the same way.
  for (const input of [{}, { unauthenticatedRead: true }, { credentialRows: 3 }]) {
    const verdict = apiExposureVerdict(input);
    assert.equal(verdict.ok, false, JSON.stringify(input));
    assert.equal(verdict.code, "unknown");
  }
});

test("the remedy names three DECISIONS and does not pretend to be a repair", () => {
  const { fix } = apiExposureVerdict({ unauthenticatedRead: true, credentialRows: 1, totalRows: 1 });
  assert.match(fix, /API_KEY/);
  assert.match(fix, /127\.0\.0\.1/);
  assert.match(fix, /console link|hermes/i, "the cost of the third option must be stated, or it reads "
    + "as free");
  // THE SAME RULE FOR THE FIRST, which had no cost until 2026-08-29 and is the expensive one. The
  // dashboard sends `X-Aify-Operator-Key` and never `X-API-Key`, and its own app has no proxy route
  // to attach one, so setting API_KEY 401s every poll while `/ws` stays exempt -- a page reporting a
  // live connection over no data. An operator reading three options picks the one with no stated
  // cost.
  assert.match(fix, /dashboard/i, "the cost of the FIRST option must be stated too");
  assert.match(fix, /401/, "say what the dashboard actually does, not that it is 'affected'");
});

// ---- what counts as a credential -----------------------------------------------------------------

test("a token in a query string is a credential; prose is not", () => {
  assert.equal(looksLikeCredential(TOKENED), true);
  assert.equal(looksLikeCredential("https://host/x?key=deadbeef"), true);
  assert.equal(looksLikeCredential("Bearer abc.def.ghi"), true);
  // NEGATIVE CONTROL. A pattern that matched the word "secret" in a description would produce a count
  // nobody trusts, and a count nobody trusts is one nobody reads.
  assert.equal(looksLikeCredential("this agent keeps no secret state"), false);
  assert.equal(looksLikeCredential("ws://127.0.0.1:9341/api/ws"), false);
  assert.equal(looksLikeCredential(""), false);
  assert.equal(looksLikeCredential(42), false);
});

test("the walk reaches a token NESTED in the row, which is where the real one was", () => {
  // `runtimeConfig.gatewayUrl` is two levels down. A scan of top-level values would have found none
  // and reported the fleet clean.
  const agents = {
    a: { id: "a", runtimeConfig: { gatewayUrl: TOKENED } },
    b: { id: "b", runtimeConfig: { gatewayUrl: "ws://127.0.0.1:1/api/ws" } },
    c: { id: "c", sessions: [{ links: [{ href: "http://h/?token=zz" }] }] },
  };
  assert.deepEqual(credentialBearingRows(agents), { credentialRows: 2, totalRows: 3 });
});

test("an unreadable listing yields nulls, not zeroes", () => {
  // Zero means "checked and found none". Null means "did not check", and they must not become the
  // same answer on the way to the verdict.
  assert.deepEqual(credentialBearingRows(null), { credentialRows: null, totalRows: null });
  assert.deepEqual(credentialBearingRows("nope"), { credentialRows: null, totalRows: null });
});

// ---- the check, executed -------------------------------------------------------------------------

function recorder() {
  const rows = [];
  return { rows, add: (...args) => rows.push(args) };
}

test("THE CHECK RUNS, rather than being asserted about", () => {
  // `service-check.mjs` exists because a proven predicate sat behind an early return nothing
  // consulted. This drives the call.
  const { rows, add } = recorder();
  return checkApiExposure({
    baseUrl: "http://127.0.0.2:1",
    fetchJson: async () => ({ agents: { a: { runtimeConfig: { gatewayUrl: TOKENED } } } }),
    add,
  }).then(() => {
    assert.equal(rows.length, 1);
    const [id, ok, code] = rows[0];
    assert.equal(id, "api-exposure");
    assert.equal(ok, false);
    assert.equal(code, "open-with-credentials");
  });
});

test("a 401 body is read as authenticated, not as an unreadable listing", async () => {
  // The happy answer arrives as an ERROR body, so a check that only looked for `agents` would report
  // `unknown` for a service that is correctly refusing it.
  const { rows, add } = recorder();
  await checkApiExposure({
    baseUrl: "http://127.0.0.2:1",
    fetchJson: async () => ({ error: "Invalid or missing API key." }),
    add,
  });
  assert.equal(rows[0][2], "authenticated");
});

test("a service that does not answer is unknown, not clean", async () => {
  const { rows, add } = recorder();
  await checkApiExposure({ baseUrl: "http://127.0.0.2:1", fetchJson: async () => null, add });
  assert.equal(rows[0][1], false);
  assert.equal(rows[0][2], "unknown");
});

test("it asks WITHOUT the service's own key", async () => {
  // The one thing this check cannot get wrong and still mean anything: asking with a key can only
  // ever answer "yes, with a key". It takes `fetchJson`, which carries none, rather than doctor's
  // `get`, which carries the configured one.
  let asked = "";
  const { add } = recorder();
  await checkApiExposure({
    baseUrl: "http://127.0.0.2:1",
    fetchJson: async (url) => { asked = url; return null; },
    add,
  });
  assert.equal(asked, "http://127.0.0.2:1/api/v1/agents");
});
