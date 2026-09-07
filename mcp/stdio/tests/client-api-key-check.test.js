#!/usr/bin/env node
// Can this host authenticate against the service it is pointed at?
//
// THE CHECK THIS REPLACES ASKED A PROXY QUESTION and was wrong twice over. It parsed
// `~/.claude.json` and `~/.hermes/config.yaml` for a key NAME, and review reproduced the detector
// returning true for `# AIFY_API_KEY: old-key`, for `AIFY_API_KEY: "" # no key`, for
// `NOT_AIFY_API_KEY`, for the token in a description outside `env`, and for an `aify-comms` block
// under the wrong root -- while a correctly quoted entry returned null and was discarded. Its
// gatherer turned HTTP 500, 404, malformed JSON and EACCES all into green, ignored `HERMES_HOME`, and
// judged clients configured for other endpoints against this service.
//
// AND CORRECT, IT STILL WOULD HAVE MISSED THE OUTAGE IT WAS WRITTEN DURING: the process that could
// not authenticate was a STANDALONE worker whose environment no MCP config describes.
//
// So the question is now the direct one -- resolve the key the way the runtime does, and TRY it --
// and these tests are about the only thing that can now go wrong: reading a failure as an answer.

import assert from "node:assert/strict";
import { test } from "node:test";

import { clientApiKeyVerdict, credentialPolicyFrom } from "../client-api-key-check.mjs";

const ENDPOINT = "http://127.0.0.1:8800";

// ── what an unauthenticated probe establishes ───────────────────────────────────────────────────

test("POSITIVE CONTROL: a refusal means a key is required, a success means it is not", () => {
  // Every assertion below is about NOT over-reading a response. A classifier that answered "unknown"
  // to everything would satisfy them all and make the check permanently silent.
  assert.equal(credentialPolicyFrom({ status: 401 }), "required");
  assert.equal(credentialPolicyFrom({ status: 403 }), "required");
  assert.equal(credentialPolicyFrom({ status: 200 }), "open");
});

test("A 500 OR A 404 IS NOT EVIDENCE THAT NO KEY IS NEEDED", () => {
  // R5, reproduced by review: both read as "no key required" and printed green while the service was
  // refusing every call. A broken service and an open one are not the same fact.
  for (const status of [500, 404, 502, 302, 418]) {
    assert.equal(credentialPolicyFrom({ status }), "unknown", `${status} was read as a policy`);
  }
});

test("a request that could not be made at all is unknown, not open", () => {
  for (const nothing of [null, undefined, {}, { status: "200" }]) {
    assert.equal(credentialPolicyFrom(nothing), "unknown");
  }
});

// ── the verdict ─────────────────────────────────────────────────────────────────────────────────

test("THE DEFECT THIS EXISTS FOR: a key is required and this host resolves none", () => {
  // The shape of the real outage: a standalone worker with no key in its environment, beating into
  // 401s while its status stayed green because registration is a separate signal.
  const v = clientApiKeyVerdict({ policy: "required", endpoint: ENDPOINT, hasKey: false });
  assert.equal(v.ok, false);
  assert.equal(v.code, "no-key-resolved");
  assert.match(v.detail, /401/, "the row does not say what the operator will actually see");
  assert.match(v.fix, /credential|AIFY_API_KEY/, "the fix names no way out");
});

test("THE OTHER REAL SHAPE: the service refuses the key this host resolves", () => {
  // Clients holding one key while the service runs on another reads as a total outage with both
  // halves looking correctly configured -- the failure `scripts/api-key.sh` exists for.
  const v = clientApiKeyVerdict({
    policy: "required", endpoint: ENDPOINT, hasKey: true, keySource: ".env", authed: { status: 401 },
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "key-refused");
  assert.match(v.detail, /\.env/, "the row does not say WHICH key was refused");
});

test("a key that works is the pass, and it names its source", () => {
  const v = clientApiKeyVerdict({
    policy: "required", endpoint: ENDPOINT, hasKey: true,
    keySource: "aify-env's credential store", authed: { status: 200 },
  });
  assert.equal(v.ok, true);
  assert.equal(v.code, "authenticated");
  assert.match(v.detail, /credential store/);
});

test("A SERVICE WITH NO KEY IS NOT A DEFECT", () => {
  // Running open is a configuration. Firing here would make this row red on every developer machine
  // that never set API_KEY, which is how a check gets switched off before the day it matters.
  const v = clientApiKeyVerdict({ policy: "open", endpoint: ENDPOINT, hasKey: false });
  assert.equal(v.ok, true);
  assert.equal(v.code, "no-key-required");
});

test("NO EVIDENCE IS NOT A PASS", () => {
  // The rule this repo has already paid for twice (`env-bridge`, `bridge-current`).
  const v = clientApiKeyVerdict({ policy: "unknown", endpoint: ENDPOINT });
  assert.equal(v.ok, false);
  assert.equal(v.code, "unknown-all");
  assert.equal(clientApiKeyVerdict().code, "unknown-all", "called with nothing, it claimed something");
});

test("an authenticated probe that did not complete is PARTIAL, not a pass", () => {
  // Some evidence (the policy) and a gap (whether our key works) is a third state; collapsing it into
  // either neighbour loses the distinction.
  for (const authed of [null, { status: 500 }]) {
    const v = clientApiKeyVerdict({
      policy: "required", endpoint: ENDPOINT, hasKey: true, keySource: ".env", authed,
    });
    assert.equal(v.ok, false, `authed=${JSON.stringify(authed)} reported ok`);
    assert.equal(v.code, "partial");
  }
});

test("every verdict names the endpoint it judged", () => {
  // R5's last item: a client configured for a DIFFERENT endpoint was judged against this service and
  // blamed as keyless. There is one endpoint now -- the one actually probed -- and every row says
  // which, so a reader can never wonder who was being judged.
  const cases = [
    { policy: "open", endpoint: ENDPOINT, hasKey: false },
    { policy: "required", endpoint: ENDPOINT, hasKey: false },
    { policy: "required", endpoint: ENDPOINT, hasKey: true, authed: { status: 401 } },
    { policy: "required", endpoint: ENDPOINT, hasKey: true, authed: { status: 200 } },
  ];
  for (const deps of cases) {
    assert.match(clientApiKeyVerdict(deps).detail, /127\.0\.0\.1:8800/,
      `a verdict did not say which endpoint it judged: ${JSON.stringify(deps)}`);
  }
});
