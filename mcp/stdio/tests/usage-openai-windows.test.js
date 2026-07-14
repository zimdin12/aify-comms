// OpenAI quota: the token store, and which window is which.
//
// Operator: "in analytics page I still do not see OpenAI usage stuff." Two bugs, both proven
// against the LIVE API (HTTP 200, real account) rather than inferred:
//
// 1. The token was not where we looked. Hermes' auth.json can be a POINTER —
//    {"active_provider": "openai-codex"} with no tokens at all — because it delegates to the
//    codex CLI's store. We only read ~/.hermes/auth.json, found nothing, and silently fell back
//    to the stale codex rollout, so the quota never refreshed.
//
// 2. We mapped the windows BY POSITION (primary=5h, secondary=weekly). The real response on a
//    `prolite` plan carries ONE window and it is the WEEKLY one:
//        primary_window:   { used_percent: 29, limit_window_seconds: 604800 }  // 7 days
//        secondary_window: null
//    So the weekly number was published as "5h" (its reset six days out gave it away) and
//    `weekly` came out null — which is exactly why the dashboard card showed "—" and an empty
//    bar: its headline IS the weekly figure.

import assert from "node:assert/strict";
import test from "node:test";

import { classifyUsageWindows, extractOpenAiToken, fetchChatGptUsageLive } from "../usage-collector.js";

// Captured verbatim from chatgpt.com/backend-api/wham/usage (HTTP 200).
const REAL_PROLITE = {
  plan_type: "prolite",
  rate_limit: {
    allowed: true,
    limit_reached: false,
    primary_window: {
      used_percent: 29,
      limit_window_seconds: 604800,
      reset_after_seconds: 511030,
      reset_at: 1784557646,
    },
    secondary_window: null,
  },
};

const FIVE_AND_WEEK = {
  plan_type: "pro",
  rate_limit: {
    primary_window: { used_percent: 4, limit_window_seconds: 18000, reset_at: 1784557646 },
    secondary_window: { used_percent: 61, limit_window_seconds: 604800, reset_at: 1784999999 },
  },
};

test("a 7-day window is WEEKLY even when it arrives as primary_window", () => {
  const { five, week } = classifyUsageWindows(REAL_PROLITE.rate_limit);
  assert.equal(week?.used_percent, 29, "the 604800s window is the weekly one");
  assert.equal(five, null, "this plan has no 5-hour window — do not invent one");
});

test("a plan with both windows still maps correctly", () => {
  const { five, week } = classifyUsageWindows(FIVE_AND_WEEK.rate_limit);
  assert.equal(five?.used_percent, 4);
  assert.equal(week?.used_percent, 61);
});

test("falls back to positional order when the API sends no durations", () => {
  const { five, week } = classifyUsageWindows({
    primary_window: { used_percent: 10 },
    secondary_window: { used_percent: 20 },
  });
  assert.equal(five?.used_percent, 10);
  assert.equal(week?.used_percent, 20);
});

test("REGRESSION: the real response yields a WEEKLY number, not a mislabelled 5h one", async () => {
  const jwt = ["x", Buffer.from(JSON.stringify({ iss: "https://auth.openai.com" })).toString("base64url"), "y"];
  const auth = JSON.stringify({ tokens: { access_token: "ey" + jwt.join(".") } });
  const usage = await fetchChatGptUsageLive({
    readHermesAuth: () => auth,
    fetchImpl: async () => ({ ok: true, json: async () => REAL_PROLITE }),
  });
  assert.ok(usage, "live fetch must produce a pool");
  assert.equal(usage.weekly.used_pct, 29);
  assert.equal(usage.weekly.left_pct, 71, "this is the number the dashboard headline shows");
  // Before the fix this said five_hour=29% with a reset six days away.
  assert.equal(usage.five_hour?.used_pct ?? null, null);
});

test("the token is found in the CODEX store when hermes' auth.json is only a pointer", () => {
  const hermesPointer = JSON.stringify({ version: 1, active_provider: "openai-codex" });
  assert.equal(extractOpenAiToken(hermesPointer), null, "hermes' file genuinely has no token");

  const jwt = ["x", Buffer.from(JSON.stringify({ iss: "https://auth.openai.com" })).toString("base64url"), "y"];
  const codexStore = JSON.stringify({ auth_mode: "chatgpt", tokens: { access_token: "ey" + jwt.join(".") } });
  assert.ok(extractOpenAiToken(codexStore), "the codex store is where the token actually is");
});
