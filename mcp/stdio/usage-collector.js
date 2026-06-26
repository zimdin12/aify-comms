// Usage / quota collector for the aify-comms env-bridge.
//
// Reads per-POOL subscription quota (% used) from the two sources on this host and
// normalizes them to a common shape the service caches and the dashboards render:
//   - anthropic-claude-max   -> GET api.anthropic.com/api/oauth/usage (OAuth token)
//   - openai-chatgpt-codex   -> latest codex rollout's `rate_limits` snapshot
// codex AND hermes share the openai-chatgpt-codex pool (hermes points at the same
// chatgpt.com/backend-api/codex backend). See
// docs/superpowers/specs/2026-06-26-usage-quota-stats-design.md.
//
// All functions are pure given injected fetch/fs (defaults wire the real ones), and
// NEVER throw into the bridge loop: on any error/missing creds they return
// `{ source_id, unknown: true }` so a pool shows "unknown", never a fake 0%.

// ── pure helpers ─────────────────────────────────────────────────────────────

// Percent of quota REMAINING from percent USED (provider reports "used").
export function pctLeft(usedPct) {
  const u = Number(usedPct);
  if (!Number.isFinite(u)) return null;
  return Math.max(0, Math.min(100, 100 - u));
}

const SEVERITY_RANK = { normal: 0, warning: 1, critical: 2 };

// Severity for a used-% against thresholds, escalated by the provider's own severity
// (so a provider "warning" is never downgraded). Default warn>=90, critical>=98.
export function severityFor(usedPct, providerSeverity, warn = 90, critical = 98) {
  let s = "normal";
  const u = Number(usedPct);
  if (Number.isFinite(u)) {
    if (u >= critical) s = "critical";
    else if (u >= warn) s = "warning";
  }
  const p = providerSeverity && SEVERITY_RANK[providerSeverity] !== undefined ? providerSeverity : "normal";
  return SEVERITY_RANK[p] > SEVERITY_RANK[s] ? p : s;
}

// Common normalized pool shape consumed by the service cache + dashboards.
export function normalizeUsage({
  sourceId, fiveHourUsed, weeklyUsed, fiveHourResetsAt, weeklyResetsAt,
  providerSeverity, planType, warn, critical,
} = {}) {
  const win = (used, resets) => ({
    used_pct: Number.isFinite(Number(used)) ? Number(used) : null,
    left_pct: pctLeft(used),
    resets_at: resets || null,
  });
  return {
    source_id: sourceId,
    five_hour: win(fiveHourUsed, fiveHourResetsAt),
    weekly: win(weeklyUsed, weeklyResetsAt),
    severity: severityFor(weeklyUsed, providerSeverity, warn, critical),
    plan_type: planType || null,
  };
}
