#!/usr/bin/env node
// Tests for the usage collector (per-pool subscription quota + consumption).
// Pure helpers + adapters with injected fetch/fs so they run with no network/creds.
import assert from "node:assert/strict";
import { pctLeft, severityFor, normalizeUsage } from "../usage-collector.js";

// ── helpers ──────────────────────────────────────────────────────────────────
assert.equal(pctLeft(81), 19, "pctLeft(81)=19");
assert.equal(pctLeft(0), 100, "pctLeft(0)=100");
assert.equal(pctLeft(100), 0, "pctLeft(100)=0");
assert.equal(pctLeft("nope"), null, "pctLeft(non-number)=null");

assert.equal(severityFor(10), "normal", "10% used -> normal");
assert.equal(severityFor(92), "warning", "92% -> warning (>=90)");
assert.equal(severityFor(99), "critical", "99% -> critical (>=98)");
assert.equal(severityFor(50, "warning"), "warning", "provider severity escalates a normal pct");
assert.equal(severityFor(99, "warning"), "critical", "threshold beats a lower provider severity");

const n = normalizeUsage({
  sourceId: "anthropic-claude-max",
  fiveHourUsed: 10, weeklyUsed: 81,
  fiveHourResetsAt: "2026-06-26T16:00:00Z", weeklyResetsAt: "2026-06-26T17:00:00Z",
  providerSeverity: "warning", planType: "max",
});
assert.equal(n.source_id, "anthropic-claude-max");
assert.equal(n.weekly.left_pct, 19, "weekly left = 100-81");
assert.equal(n.weekly.used_pct, 81);
assert.equal(n.five_hour.left_pct, 90);
assert.equal(n.severity, "warning");
assert.equal(n.plan_type, "max");

console.log("usage-collector.test.js: helpers ok");

// ── anthropic adapter: oauth/usage shape -> normalized ───────────────────────
import { fetchAnthropicUsage, fetchCodexUsage } from "../usage-collector.js";
{
  const fakeFetch = async () => ({
    ok: true, status: 200,
    json: async () => ({
      five_hour: { utilization: 10.0, resets_at: "2026-06-26T16:00:00Z" },
      seven_day: { utilization: 81.0, resets_at: "2026-06-26T17:00:00Z" },
      limits: [{ kind: "weekly_all", group: "weekly", percent: 81, severity: "warning", is_active: true }],
    }),
  });
  const fakeCreds = JSON.stringify({ claudeAiOauth: { accessToken: "x", expiresAt: Date.now() + 1e6 } });
  const r = await fetchAnthropicUsage({ readCreds: () => fakeCreds, fetchImpl: fakeFetch });
  assert.equal(r.source_id, "anthropic-claude-max");
  assert.equal(r.weekly.used_pct, 81);
  assert.equal(r.weekly.left_pct, 19);
  assert.equal(r.five_hour.used_pct, 10);
  assert.equal(r.severity, "warning", "active weekly limit severity carried through");
}
// anthropic: HTTP error / missing creds -> unknown, never throws
{
  const r = await fetchAnthropicUsage({ readCreds: () => { throw new Error("no creds"); } });
  assert.equal(r.unknown, true);
  assert.equal(r.source_id, "anthropic-claude-max");
  const r2 = await fetchAnthropicUsage({ readCreds: () => JSON.stringify({ claudeAiOauth: { accessToken: "x" } }), fetchImpl: async () => ({ ok: false, status: 401, json: async () => ({}) }) });
  assert.equal(r2.unknown, true, "401 -> unknown");
}
// ── codex adapter: rollout rate_limits -> normalized ─────────────────────────
{
  const rollout = JSON.stringify({ rate_limits: { primary: { used_percent: 1, window_minutes: 300, resets_at: 1778617622 }, secondary: { used_percent: 0, window_minutes: 10080, resets_at: 1779146585 }, plan_type: "prolite" } });
  const r = await fetchCodexUsage({ readLatestRollout: () => rollout });
  assert.equal(r.source_id, "openai-chatgpt-codex");
  assert.equal(r.weekly.used_pct, 0, "secondary=weekly");
  assert.equal(r.five_hour.used_pct, 1, "primary=5h");
  assert.equal(r.plan_type, "prolite");
  // epoch-seconds resets_at converted to ISO
  assert.equal(typeof r.weekly.resets_at, "string");
  assert.ok(r.weekly.resets_at.includes("T"), "resets_at is ISO");
}
// codex: no rollout / no rate_limits -> unknown
{
  const r = await fetchCodexUsage({ readLatestRollout: () => "" });
  assert.equal(r.unknown, true);
  assert.equal(r.source_id, "openai-chatgpt-codex");
}
console.log("usage-collector.test.js: adapters ok");
