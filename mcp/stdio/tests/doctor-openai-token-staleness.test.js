#!/usr/bin/env node
// An EXPIRED cached OpenAI token is not a broken login (false RED, observed live 2026-08-09).
//
// doctor reported "the ChatGPT usage API rejected it (HTTP 401) — the token is likely expired,
// re-authenticate with `codex login`", and three minutes later the same check was GREEN with no
// operator action: codex had renewed the token on its next use (`auth.json.last_refresh`
// 11:45:19Z, new access_token good for ten more days). The advice was wrong, and
// `aify-doctor --strict` exits 1, so this would also fail any script or CI using it — on a
// condition that fixes itself.
//
// Third time this repo has shipped a doctor verdict that was wrong in a self-healing situation
// (ENV_FUTURE_SKEW_MS = clock-skew false RED; bridgeInstallVerdict = alarm fatigue). The line
// this pins: a login that CAN renew itself is not a failure; one that cannot, is.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  OPENAI_TOKEN_EXPIRY_SKEW_SECONDS,
  openAiTokenExpiry,
  openAiUsageVerdict,
} from "../doctor-predicates.js";
import { hasOpenAiRefreshToken } from "../usage-collector.js";

const NOW = 1_786_000_000;

function jwt(payload) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
  return `${b64({ alg: "none" })}.${b64(payload)}.sig`;
}

// ── expiry parsing ───────────────────────────────────────────────────────────────────
test("reads the exp claim without verifying the signature", () => {
  assert.equal(openAiTokenExpiry(jwt({ iss: "https://auth.openai.com", exp: NOW + 600 })), NOW + 600);
});

test("a malformed or absent token yields NaN, never a number", () => {
  for (const bad of ["", null, undefined, "not-a-jwt", "a.b", "a.!!!.c", jwt({ iss: "x" })]) {
    assert.ok(Number.isNaN(openAiTokenExpiry(bad)), `expected NaN for ${JSON.stringify(bad)}`);
  }
});

// ── the false RED this exists to prevent ─────────────────────────────────────────────
test("expired token WITH a refresh token is OK — codex renews it on next use", () => {
  const v = openAiUsageVerdict({
    hasToken: true, tokenExp: NOW - 3600, hasRefreshToken: true, httpStatus: 401, now: NOW,
  });
  assert.equal(v.ok, true, "a self-healing condition must not fail --strict");
  assert.equal(v.code, "stale-token");
  assert.match(v.detail, /no action needed/i);
  assert.equal(v.fix, "", "must NOT tell the operator to re-authenticate — that advice was wrong");
  assert.doesNotMatch(v.detail, /codex login/);
});

test("a token expiring mid-round-trip counts as stale, not revoked", () => {
  const v = openAiUsageVerdict({
    hasToken: true,
    tokenExp: NOW + Math.floor(OPENAI_TOKEN_EXPIRY_SKEW_SECONDS / 2),
    hasRefreshToken: true,
    httpStatus: 401,
    now: NOW,
  });
  assert.equal(v.code, "stale-token");
});

// ── the genuine failures must still fail ─────────────────────────────────────────────
test("UNEXPIRED token rejected 401 is a real failure — revoked", () => {
  const v = openAiUsageVerdict({
    hasToken: true, tokenExp: NOW + 86_400, hasRefreshToken: true, httpStatus: 401, now: NOW,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "rejected");
  assert.match(v.detail, /revoked/);
  assert.match(v.fix, /codex login/);
});

test("403 on an unexpired token is also a real failure", () => {
  const v = openAiUsageVerdict({
    hasToken: true, tokenExp: NOW + 86_400, hasRefreshToken: true, httpStatus: 403, now: NOW,
  });
  assert.equal(v.ok, false);
  assert.match(v.fix, /codex login/);
});

test("expired with NO refresh token cannot self-heal — real failure", () => {
  const v = openAiUsageVerdict({
    hasToken: true, tokenExp: NOW - 10, hasRefreshToken: false, httpStatus: 401, now: NOW,
  });
  assert.equal(v.ok, false);
  assert.equal(v.code, "expired-no-refresh");
  assert.match(v.fix, /codex login/);
});

test("no token at all still points at codex login", () => {
  const v = openAiUsageVerdict({ hasToken: false, now: NOW });
  assert.equal(v.ok, false);
  assert.equal(v.code, "no-token");
  assert.match(v.fix, /codex login/);
});

test("a healthy API response is OK regardless of the cached expiry", () => {
  const v = openAiUsageVerdict({ hasToken: true, tokenExp: NOW - 999, apiOk: true, now: NOW });
  assert.equal(v.ok, true);
  assert.equal(v.code, "ok");
});

test("an unexpired token rejected with a non-auth status is not blamed on the login", () => {
  const v = openAiUsageVerdict({
    hasToken: true, tokenExp: NOW + 86_400, hasRefreshToken: true, httpStatus: 500, now: NOW,
  });
  assert.equal(v.ok, false);
  assert.match(v.detail, /HTTP 500/);
  assert.match(v.fix, /Retry/);
});

// ── refresh-token detection must stay scoped to the OpenAI subtree ───────────────────
test("finds a refresh token that sits beside the OpenAI access token", () => {
  const store = JSON.stringify({
    tokens: { access_token: jwt({ iss: "https://auth.openai.com", exp: NOW }), refresh_token: "rt-1" },
  });
  assert.equal(hasOpenAiRefreshToken(store), true);
});

test("does NOT count another provider's refresh token as OpenAI's", () => {
  // Otherwise a dead OpenAI login looks recoverable because anthropic has a refresh token —
  // the false-green class doctor exists to prevent, inverted.
  const store = JSON.stringify({
    anthropic: { access_token: jwt({ iss: "https://anthropic.com", exp: NOW }), refresh_token: "rt-other" },
    openai: { access_token: jwt({ iss: "https://auth.openai.com", exp: NOW }) },
  });
  assert.equal(hasOpenAiRefreshToken(store), false);
});

test("malformed stores are false, never a throw", () => {
  for (const bad of ["", "{", null, undefined, "[]", "{}"]) {
    assert.equal(hasOpenAiRefreshToken(bad), false);
  }
});

console.log("doctor-openai-token-staleness.test.js: all assertions passed");
