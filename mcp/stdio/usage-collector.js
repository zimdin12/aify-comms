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

import { readFileSync, readdirSync, statSync, openSync, fstatSync, readSync, closeSync } from "node:fs";
import { join } from "node:path";

const ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage";
const ANTHROPIC_USAGE_HEADERS = { "anthropic-beta": "oauth-2025-04-20" };
const SOURCE_ANTHROPIC = "anthropic-claude-max";
const SOURCE_CODEX = "openai-chatgpt-codex";

function homeDir() { return process.env.HOME || process.env.USERPROFILE || ""; }

// ── per-agent consumption (attribution) ─────────────────────────────────────

// Sum a claude transcript's per-message token usage into session totals. Each assistant
// message's `usage` is ONE API request's billed tokens, so summing across messages =
// total tokens consumed this session (input + output + cache creation/read).
export function readClaudeConsumption(content) {
  const out = { input_tokens: 0, output_tokens: 0, cache_tokens: 0 };
  for (const line of String(content || "").split("\n")) {
    const t = line.trim();
    if (!t) continue;
    let o;
    try { o = JSON.parse(t); } catch { continue; }
    const u = o && o.message && o.message.usage;
    if (!u) continue;
    out.input_tokens += Number(u.input_tokens || 0);
    out.output_tokens += Number(u.output_tokens || 0);
    out.cache_tokens += Number(u.cache_creation_input_tokens || 0) + Number(u.cache_read_input_tokens || 0);
  }
  return out;
}

// claude transcript path: ~/.claude/projects/<cwd-with-nonalnum-as-dash>/<sessionId>.jsonl
function claudeTranscriptPath(cwd, sessionId) {
  const enc = String(cwd || "").replace(/[^a-zA-Z0-9]/g, "-");
  return join(homeDir(), ".claude", "projects", enc, `${sessionId}.jsonl`);
}

// Best-effort consumption for one agent. Only claude-code is computed in v1 (its
// transcript path is deterministic from cwd+sessionHandle); codex/hermes return null
// until their session->file mapping is added.
export function readAgentConsumption({ runtime, cwd, sessionHandle, readFile = (p) => readFileSync(p, "utf8") }) {
  const rt = String(runtime || "").toLowerCase();
  if ((rt === "claude-code" || rt === "claude") && cwd && sessionHandle) {
    try { return readClaudeConsumption(readFile(claudeTranscriptPath(cwd, sessionHandle))); }
    catch { return null; }
  }
  return null;
}

// ── collector loop ───────────────────────────────────────────────────────────

// Poll every pool and POST each result (including `unknown`, so the UI can show it).
// Each adapter is isolated — one throwing/failing never blocks the others.
export async function collectOnce({ fetchAnthropic = fetchAnthropicUsage, fetchCodex = fetchCodexUsage, post } = {}) {
  const settled = await Promise.allSettled([fetchAnthropic(), fetchCodex()]);
  for (const s of settled) {
    const r = s.status === "fulfilled" ? s.value : null;
    if (r && r.source_id && typeof post === "function") {
      try { await post(r); } catch { /* best-effort, like the rest of the bridge */ }
    }
  }
}

// Enumerate agents, compute each one's consumption (where readable), and POST the rows.
export async function collectConsumptionOnce({ getAgents, post, readConsumption = readAgentConsumption } = {}) {
  let agents;
  try { agents = await getAgents(); } catch { return; }
  const rows = [];
  for (const [id, info] of Object.entries(agents || {})) {
    const c = readConsumption({ runtime: info.runtime, cwd: info.cwd, sessionHandle: info.sessionHandle });
    if (c && (c.input_tokens || c.output_tokens || c.cache_tokens)) {
      rows.push({ agent_id: id, source_id: info.usageSource || "", model: info.model || "", ...c });
    }
  }
  if (rows.length && typeof post === "function") {
    try { await post(rows); } catch { /* best-effort */ }
  }
}

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

// ── adapters ─────────────────────────────────────────────────────────────────

// Default cred reader: the host claude OAuth credentials.
function defaultReadCreds() {
  return readFileSync(join(homeDir(), ".claude", ".credentials.json"), "utf8");
}

// Anthropic Claude Max: GET oauth/usage with the stored bearer token.
// Returns normalized usage, or { source_id, unknown:true } on any failure.
export async function fetchAnthropicUsage({ readCreds = defaultReadCreds, fetchImpl = globalThis.fetch } = {}) {
  const unknown = { source_id: SOURCE_ANTHROPIC, unknown: true };
  try {
    const creds = JSON.parse(readCreds() || "{}");
    const tok = creds && creds.claudeAiOauth && creds.claudeAiOauth.accessToken;
    if (!tok) return unknown;
    const res = await fetchImpl(ANTHROPIC_USAGE_URL, {
      headers: { authorization: `Bearer ${tok}`, ...ANTHROPIC_USAGE_HEADERS },
    });
    if (!res || !res.ok) return unknown;
    const j = await res.json();
    const fiveHour = j.five_hour || {};
    const weekly = j.seven_day || {};
    const activeWeekly = Array.isArray(j.limits)
      ? j.limits.find((l) => l && l.group === "weekly" && l.is_active) || j.limits.find((l) => l && l.group === "weekly")
      : null;
    return normalizeUsage({
      sourceId: SOURCE_ANTHROPIC,
      fiveHourUsed: fiveHour.utilization,
      weeklyUsed: weekly.utilization,
      fiveHourResetsAt: fiveHour.resets_at,
      weeklyResetsAt: weekly.resets_at,
      providerSeverity: activeWeekly && activeWeekly.severity,
      planType: creds.claudeAiOauth.subscriptionType || creds.claudeAiOauth.rateLimitTier,
    });
  } catch {
    return unknown;
  }
}

// Read the tail of the newest codex rollout file (defaults).
function defaultReadLatestRollout() {
  const dir = join(homeDir(), ".codex", "sessions");
  let newest = null;
  let newestMtime = -1;
  const walk = (d) => {
    let entries;
    try { entries = readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const p = join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile() && e.name.startsWith("rollout-") && e.name.endsWith(".jsonl")) {
        try { const m = statSync(p).mtimeMs; if (m > newestMtime) { newestMtime = m; newest = p; } } catch { /* skip */ }
      }
    }
  };
  walk(dir);
  if (!newest) return "";
  const fd = openSync(newest, "r");
  try {
    const size = fstatSync(fd).size;
    const start = Math.max(0, size - 262144);
    const buf = Buffer.alloc(size - start);
    const n = readSync(fd, buf, 0, size - start, start);
    return buf.subarray(0, n).toString("utf8");
  } finally { closeSync(fd); }
}

// Recursively find the first `rate_limits` object (with primary/secondary) in a parsed line.
function findRateLimits(obj) {
  if (!obj || typeof obj !== "object") return null;
  if (obj.rate_limits && typeof obj.rate_limits === "object") return obj.rate_limits;
  for (const v of Object.values(obj)) {
    if (v && typeof v === "object") { const r = findRateLimits(v); if (r) return r; }
  }
  return null;
}

function epochToIso(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  try { return new Date(n * 1000).toISOString(); } catch { return null; }
}

// OpenAI ChatGPT-Codex (shared by codex + hermes): the latest rollout snapshots
// `rate_limits` on every response. primary=5h, secondary=weekly.
export async function fetchCodexUsage({ readLatestRollout = defaultReadLatestRollout } = {}) {
  const unknown = { source_id: SOURCE_CODEX, unknown: true };
  try {
    const content = readLatestRollout() || "";
    const lines = content.split("\n").filter((l) => l.trim());
    let rl = null;
    for (let i = lines.length - 1; i >= 0 && !rl; i--) {
      if (!lines[i].includes("rate_limits")) continue;
      try { rl = findRateLimits(JSON.parse(lines[i])); } catch { /* partial/ junk line */ }
    }
    if (!rl) return unknown;
    const primary = rl.primary || {};
    const secondary = rl.secondary || {};
    return normalizeUsage({
      sourceId: SOURCE_CODEX,
      fiveHourUsed: primary.used_percent,
      weeklyUsed: secondary.used_percent,
      fiveHourResetsAt: epochToIso(primary.resets_at),
      weeklyResetsAt: epochToIso(secondary.resets_at),
      planType: rl.plan_type,
    });
  } catch {
    return unknown;
  }
}
