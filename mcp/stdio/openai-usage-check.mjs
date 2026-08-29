// The `usage-openai` check: is the ChatGPT quota token actually usable, and when it is not,
// is that a login that needs a human or one that heals itself on codex's next call?
//
// SPLIT OUT OF `doctor-predicates.js` on 2026-08-29, at 998 lines against a 1000-line gate.
// CLAUDE.md had predicted the shape of that failure in writing -- the next doctor check goes red
// on arrival, for a reason unrelated to itself -- and named this as the fix: one module per
// check, the way `service-check.mjs` and `api-exposure-check.mjs` already are.
//
// Nothing re-exports these from the old home. A stale import fails loudly rather than resolving,
// which is the rule the control-plane split set for the same reason.
//
// `tests/doctor-sources.mjs` walks the doctor's imports transitively, so this module is part of
// "the doctor" with no edit anywhere: doctor.js reaches usage-collector.js, which reaches here.

// ── `usage-openai` staleness vs. genuine rejection ───────────────────────────────────
//
// FALSE RED, observed live 2026-08-09. doctor reported "the ChatGPT usage API rejected it
// (HTTP 401) — the token is likely expired, re-authenticate with `codex login`". Three minutes
// later the same check was GREEN with no operator action at all, because codex had refreshed
// the token on its next use (`auth.json.last_refresh` 11:45:19Z, new access_token valid ten
// more days). The advice was wrong: nothing needed re-authenticating.
//
// The mechanism: codex stores a long-lived `refresh_token` alongside a ~10-day `access_token`
// and refreshes LAZILY, on use. So between an access token's expiry and codex's next call
// there is a window where the on-disk token is stale but the login is perfectly healthy.
// doctor reads that file and calls the API itself, so it lands in that window and cries wolf.
//
// This is the third time this repo has shipped a doctor verdict that was wrong in a
// self-healing situation (see ENV_FUTURE_SKEW_MS for the clock-skew false RED, and
// bridgeInstallVerdict for the alarm-fatigue argument). A check that goes red on a condition
// that fixes itself trains the operator to skim past it — and then the one time it means
// something it reads the same as the twenty times it did not.
//
// So: an EXPIRED on-disk token with a refresh token available is NOT a failure. Only report a
// failure when the login genuinely cannot self-heal — an unexpired token the API still
// rejects (revoked/invalid), or an expired one with no refresh token to recover from.
// `exp` is read, not verified: we are asking "is this copy stale", not "is this signature
// good" — the API is the authority on validity and we already called it.
export function openAiTokenExpiry(token) {
  const raw = String(token || "");
  const parts = raw.split(".");
  if (parts.length < 2) return NaN;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString());
    const exp = Number(payload && payload.exp);
    return Number.isFinite(exp) ? exp : NaN;
  } catch {
    return NaN;
  }
}

// Small cushion so a token expiring *during* the round-trip is treated as stale rather than
// revoked. Sized like ENV_FUTURE_SKEW_MS: comfortably past ordinary latency, negligible against
// the 10-day lifetime.
export const OPENAI_TOKEN_EXPIRY_SKEW_SECONDS = 60;

// The "self-heals" green needs a floor, or it hides a permanently-dead login: if the REFRESH
// token is itself revoked, codex's renewal fails, the access token stays expired forever, and an
// unconditional `expired + refresh token -> ok` reports that as fine in perpetuity. That is the
// false GREEN `756f3a5` shipped, and it is worse than the false RED being fixed here, because a
// red gets investigated and a green does not.
//
// The floor must be EVIDENCE, not age. My first attempt used age — "expired more than 24h, so
// the renewal is not happening" — and the reviewer rejected it before tagging, correctly:
// `expiredFor > 24h` proves only that CODEX HAS NOT RUN, not that refreshing fails. An operator
// who does not touch codex over a weekend would have been told to re-authenticate a perfectly
// healthy login — a brand-new false RED, of exactly the kind this whole change exists to remove.
// doctor does not perform the refresh itself, so it cannot infer failure from silence.
//
// What IS evidence: codex stamps `last_refresh` when it renews. A `last_refresh` LATER than the
// access token's own expiry means codex did run the refresh path and the store still holds an
// expired token — the renewal produced nothing usable. That is a broken login, provable from the
// file, with no timing guess. Absent that, the honest answer stays "not renewed yet".
//
// KNOWN LIMIT, stated rather than papered over: on this host `last_refresh` (11:45:08Z) sits
// beside the token it minted (`iat` 11:45:19Z), which suggests codex stamps on a SUCCESSFUL
// renewal. If it never stamps on failure, this predicate rarely fires and a revoked refresh token
// keeps reading `stale-token`. That is accepted deliberately: doctor does not run the refresh, so
// it CANNOT prove the path is broken, and the reviewer's rule applies — do not fail `--strict` on
// something you cannot prove. The operator is not left blind either, because codex surfaces a
// revoked login directly the moment they use it. So this is a best-effort extra that catches the
// one provable case, NOT a guarantee that a dead refresh token will be reported.
export function openAiRefreshLooksBroken({ tokenExp = NaN, lastRefresh = NaN } = {}) {
  if (!Number.isFinite(tokenExp) || !Number.isFinite(lastRefresh)) return false;
  return lastRefresh > tokenExp;
}

export function openAiUsageVerdict({
  hasToken = false,
  tokenExp = NaN,
  hasRefreshToken = false,
  lastRefresh = NaN,
  httpStatus = 0,
  apiOk = false,
  now = 0,
} = {}) {
  // UNREACHABLE FROM THE ONLY PRODUCTION CALLER, and deliberately kept. usage-collector.js calls the
  // API itself and returns `ok` directly when the response is good, so it reaches this function ONLY
  // after a failed call and passes a literal `apiOk: false`. The branch is exercised by
  // doctor-openai-token-staleness.test.js and states the contract: real API evidence outranks
  // anything read out of auth.json.
  //
  // Do not 'wire it up' by making the caller compute apiOk and fall through to here — that moves the
  // success decision into a function whose whole remaining job is classifying FAILURES, and the
  // 2026-08-09 incident recorded below is about getting that classification right.
  if (apiOk) return { ok: true, code: "ok", detail: "OpenAI/ChatGPT quota is connected", fix: "" };
  if (!hasToken) {
    return {
      ok: false,
      code: "no-token",
      detail: "No OpenAI token found — ChatGPT quota will not appear in the dashboard.",
      fix: "Install the codex CLI and sign in (`codex login`). Hermes delegates its OpenAI auth to "
        + "the codex store, so codex is what actually holds the token.",
    };
  }
  const nowSeconds = Number(now) > 0 ? Number(now) : Math.floor(Date.now() / 1000);
  const expired = Number.isFinite(tokenExp) && tokenExp <= nowSeconds + OPENAI_TOKEN_EXPIRY_SKEW_SECONDS;
  if (expired && hasRefreshToken) {
    // PROVABLE failure: codex ran its refresh path after this token expired and the store still
    // holds an expired token, so the renewal produced nothing usable. See openAiRefreshLooksBroken
    // for why this is keyed on evidence rather than on how long the token has been expired.
    if (openAiRefreshLooksBroken({ tokenExp, lastRefresh })) {
      return {
        ok: false,
        code: "refresh-failing",
        detail: "codex refreshed its OpenAI auth AFTER this access token expired and the stored "
          + "token is still expired — the refresh token itself has most likely expired or been "
          + "revoked, so this login cannot recover on its own.",
        fix: "Re-authenticate with `codex login`.",
      };
    }
    // Self-healing: codex renews on its next use. Reporting this as a failure is what produced
    // wrong operator advice on 2026-08-09.
    return {
      ok: true,
      code: "stale-token",
      detail: "The cached OpenAI access token has expired, but codex holds a refresh token and "
        + "renews it on next use — no action needed. Quota may read stale until then.",
      fix: "Nothing, unless you need fresh quota right now — then run codex once to trigger the "
        + "renewal. If it is still rejected after that, re-authenticate with `codex login`.",
    };
  }
  if (expired) {
    return {
      ok: false,
      code: "expired-no-refresh",
      detail: "The OpenAI access token has expired and there is no refresh token to renew it.",
      fix: "Re-authenticate with `codex login`.",
    };
  }
  const status = Number(httpStatus) || 0;
  if (status === 401 || status === 403) {
    return {
      ok: false,
      code: "rejected",
      detail: `The OpenAI token is unexpired but the ChatGPT usage API rejected it (HTTP ${status}) — `
        + "it has most likely been revoked.",
      fix: "Re-authenticate with `codex login`.",
    };
  }
  return {
    ok: false,
    code: "rejected",
    detail: `The ChatGPT usage API did not accept the request (HTTP ${status || "?"}).`,
    fix: "Retry; if it persists, re-authenticate with `codex login`.",
  };
}
