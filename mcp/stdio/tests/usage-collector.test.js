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
