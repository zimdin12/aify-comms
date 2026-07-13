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
// Both cwd and sessionId are AGENT-SUPPLIED — scrub every non-[alnum-hyphen] char (claude
// session ids are UUIDs) so neither can path-traverse out of the projects dir.
function claudeTranscriptPath(cwd, sessionId) {
  const enc = String(cwd || "").replace(/[^a-zA-Z0-9]/g, "-");
  const sid = String(sessionId || "").replace(/[^a-zA-Z0-9-]/g, "-");
  return join(homeDir(), ".claude", "projects", enc, `${sid}.jsonl`);
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
// Default OpenAI fetcher: prefer the live (fresh, no-waste) wham/usage source, fall back
// to the codex rollout snapshot.
async function defaultFetchCodex() {
  return (await fetchChatGptUsageLive()) || (await fetchCodexUsage());
}

export async function collectOnce({ fetchAnthropic = fetchAnthropicUsage, fetchCodex = defaultFetchCodex, post } = {}) {
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
  // Severity is the WORST of either window (a fully-consumed 5-hour window rate-limits
  // the agent for hours and is the binding limit on Claude Max — it must not be hidden
  // behind a low weekly %). providerSeverity (pool-wide, e.g. limit_reached) escalates too.
  const sWeekly = severityFor(weeklyUsed, providerSeverity, warn, critical);
  const sFiveHour = severityFor(fiveHourUsed, providerSeverity, warn, critical);
  const severity = SEVERITY_RANK[sFiveHour] > SEVERITY_RANK[sWeekly] ? sFiveHour : sWeekly;
  return {
    source_id: sourceId,
    five_hour: win(fiveHourUsed, fiveHourResetsAt),
    weekly: win(weeklyUsed, weeklyResetsAt),
    severity,
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

// LIVE, NO-WASTE OpenAI quota. hermes keeps a fresh ChatGPT OAuth token in its auth store
// (it auto-refreshes it); `GET /backend-api/wham/usage` with that token returns
// ACCOUNT-LEVEL rate limits — covering hermes AND codex — with NO billed completion.
// This is the authoritative fresh source; the rollout reader is the fallback.
const CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage";

function defaultHermesAuthPath() {
  // Hermes stores its OAuth token store at ~/.hermes/auth.json on Linux/macOS (incl. WSL) and
  // %LOCALAPPDATA%\hermes\auth.json on Windows. The collector runs on the HOST, so pick by
  // platform. BUG (2026-07-13): the old code used the Windows path UNCONDITIONALLY, so on a
  // Linux/WSL host (LOCALAPPDATA empty) it read a non-existent ~/AppData/Local/hermes/auth.json →
  // extractOpenAiToken never saw the real token → the live ChatGPT `wham/usage` fetch always
  // failed → the openai-chatgpt-codex pool fell back to a STALE codex rollout and the quota never
  // refreshed (operator: "ChatGPT/Codex/Hermes usage won't update").
  if (process.platform === "win32") {
    const localAppData = process.env.LOCALAPPDATA || join(homeDir(), "AppData", "Local");
    return join(localAppData, "hermes", "auth.json");
  }
  return join(homeDir(), ".hermes", "auth.json");
}
function defaultReadHermesAuth() {
  return readFileSync(defaultHermesAuthPath(), "utf8");
}

// Pull an OpenAI (auth.openai.com) access_token out of the hermes auth store, ignoring
// tokens for other providers (nous/anthropic). Returns null if none present.
export function extractOpenAiToken(authJsonText) {
  let j;
  try { j = JSON.parse(authJsonText || "{}"); } catch { return null; }
  const isOpenAi = (t) => {
    try {
      const payload = JSON.parse(Buffer.from(String(t).split(".")[1], "base64url").toString());
      return String(payload.iss || "").includes("openai.com");
    } catch { return false; }
  };
  let found = null;
  const walk = (o) => {
    if (!o || typeof o !== "object" || found) return;
    for (const [k, v] of Object.entries(o)) {
      if (found) return;
      if (k === "access_token" && typeof v === "string" && v.startsWith("ey") && isOpenAi(v)) { found = v; return; }
      if (v && typeof v === "object") walk(v);
    }
  };
  walk(j);
  return found;
}

export async function fetchChatGptUsageLive({ readHermesAuth = defaultReadHermesAuth, fetchImpl = globalThis.fetch } = {}) {
  let tok;
  try { tok = extractOpenAiToken(readHermesAuth()); } catch { return null; }
  if (!tok) return null;
  try {
    const res = await fetchImpl(CHATGPT_USAGE_URL, {
      headers: { authorization: `Bearer ${tok}`, accept: "application/json", "user-agent": "codex-cli" },
    });
    if (!res || !res.ok) return null;
    const j = await res.json();
    const rl = j.rate_limit || {};
    const prim = rl.primary_window || {};
    const sec = rl.secondary_window || {};
    if (prim.used_percent == null && sec.used_percent == null) return null;
    return normalizeUsage({
      sourceId: SOURCE_CODEX,
      fiveHourUsed: prim.used_percent,
      weeklyUsed: sec.used_percent,
      fiveHourResetsAt: epochToIso(prim.reset_at),
      weeklyResetsAt: epochToIso(sec.reset_at),
      providerSeverity: rl.limit_reached ? "critical" : undefined,
      planType: j.plan_type,
    });
  } catch { return null; }
}

// OpenAI ChatGPT-Codex (shared by codex + hermes): the latest rollout snapshots
// `rate_limits` on every response. primary=5h, secondary=weekly. Fallback for when the
// live wham/usage source is unavailable.
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
