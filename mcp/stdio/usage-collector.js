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
import { openAiTokenExpiry, openAiUsageVerdict } from "./doctor-predicates.js";

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
  // An ABSENT window must stay unknown — never 0. `Number(null)` is 0, so the old check
  // ("is Number(used) finite?") turned a MISSING window into "0% used / 100% left": a
  // confident, fabricated all-clear for a limit we know nothing about. Real case: a plan that
  // publishes only a weekly window was shown as having a fully-free 5-hour window.
  const win = (used, resets) => {
    const known = used !== null && used !== undefined && used !== "" && Number.isFinite(Number(used));
    return {
      used_pct: known ? Number(used) : null,
      left_pct: known ? pctLeft(used) : null,
      resets_at: resets || null,
    };
  };
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

// WHERE A TOOL KEEPS ITS FILES — searched, never guessed by platform (2026-07-14).
//
// Every previous bug here was a WRONG GUESS about a path: the Windows path used unconditionally
// on Linux; then the right OS but the wrong tool (hermes' auth.json is a POINTER —
// {"active_provider":"openai-codex"} — because it delegates to the codex CLI's store). Guessing
// by `process.platform` is the anti-pattern: it fails silently and looks healthy. So don't guess.
// Enumerate every location the tool is known to use on ANY platform and take the first that
// actually exists / carries what we need. A missing path costs one failed read.
//
// Honours each tool's OWN override env var first, so a non-default install just works.
export function toolHomeCandidates(tool) {
  const home = homeDir();
  const out = [];
  const push = (p) => { if (p && !out.includes(p)) out.push(p); };
  const override = process.env[`${tool.toUpperCase()}_HOME`];
  if (override) push(override);
  push(join(home, `.${tool}`));                                            // POSIX default (and codex on Windows)
  const localAppData = process.env.LOCALAPPDATA || join(home, "AppData", "Local");
  push(join(localAppData, tool));                                          // Windows
  push(join(process.env.XDG_CONFIG_HOME || join(home, ".config"), tool));  // XDG
  push(join(home, "Library", "Application Support", tool));                // macOS
  return out;
}

// Read the tail of the newest codex rollout file (searched across every codex home).
function defaultReadLatestRollout() {
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
  for (const codexHome of toolHomeCandidates("codex")) walk(join(codexHome, "sessions"));
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

// WHERE THE OPENAI TOKEN ACTUALLY LIVES — searched across BOTH tools, on ANY OS (2026-07-14).
//
// Two separate bugs were caused by "knowing" this path:
//   * the Windows path used unconditionally, so a Linux/WSL host read a file that didn't exist;
//   * then the right OS but the wrong TOOL — hermes' auth.json is a POINTER on a default install
//     (`{"version":1,"active_provider":"openai-codex"}`, no tokens at all) because it delegates
//     its OpenAI auth to the CODEX CLI's store, which is where the JWT really is.
// Both failed SILENTLY: no token -> fall back to the stale rollout -> the quota simply never
// updates and nothing looks broken. So stop guessing: enumerate every auth.json either tool is
// known to use on any platform, and take the first that actually carries an OpenAI token.
export function openAiAuthCandidates() {
  const out = [];
  // Codex first: hermes DELEGATES to it, so on a default pair of installs that is the live store.
  for (const tool of ["codex", "hermes"]) {
    for (const dir of toolHomeCandidates(tool)) out.push(join(dir, "auth.json"));
  }
  return out;
}

function defaultReadHermesAuth() {
  let firstReadable = "";
  for (const path of openAiAuthCandidates()) {
    let text;
    try { text = readFileSync(path, "utf8"); } catch { continue; }
    if (!firstReadable) firstReadable = text;
    try { if (extractOpenAiToken(text)) return text; } catch { /* try the next store */ }
  }
  return firstReadable; // nothing carried a token — caller gets null, exactly as before
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

// Does the auth store hold a refresh token, i.e. can this login renew itself without the
// operator? Deliberately scoped to the SAME subtree that carried the OpenAI access token, so a
// nous/anthropic refresh_token elsewhere in a shared store cannot make a dead OpenAI login look
// recoverable — that would recreate the false-green class doctor exists to prevent, inverted.
export function hasOpenAiRefreshToken(authJsonText) {
  let j;
  try { j = JSON.parse(authJsonText || "{}"); } catch { return false; }
  let found = false;
  const isOpenAi = (t) => {
    try {
      const payload = JSON.parse(Buffer.from(String(t).split(".")[1], "base64url").toString());
      return String(payload.iss || "").includes("openai.com");
    } catch { return false; }
  };
  const walk = (o) => {
    if (!o || typeof o !== "object" || found) return;
    const entries = Object.entries(o);
    const access = entries.find(([k, v]) => k === "access_token" && typeof v === "string" && isOpenAi(v));
    if (access) {
      const refresh = entries.find(([k, v]) => k === "refresh_token" && typeof v === "string" && v.trim());
      if (refresh) { found = true; return; }
    }
    for (const [, v] of entries) {
      if (v && typeof v === "object") walk(v);
      if (found) return;
    }
  };
  walk(j);
  return found;
}

// When did codex last RUN its refresh path? It stamps `last_refresh` on renewal. A stamp LATER
// than the access token's own expiry is the only file-visible proof that refreshing was attempted
// and still left an expired token — i.e. the refresh token is dead. Without it, doctor cannot tell
// "refresh is broken" from "codex simply has not run", and guessing from age produced a false RED
// for low-usage operators (rejected in review before tagging). Returns epoch SECONDS, or NaN.
export function openAiLastRefreshEpoch(authJsonText) {
  let j;
  try { j = JSON.parse(authJsonText || "{}"); } catch { return NaN; }
  let found = NaN;
  const walk = (o) => {
    if (!o || typeof o !== "object" || Number.isFinite(found)) return;
    for (const [k, v] of Object.entries(o)) {
      if (k === "last_refresh" && typeof v === "string" && v.trim()) {
        const ms = Date.parse(v.trim());
        if (Number.isFinite(ms)) { found = ms / 1000; return; }
      }
      if (v && typeof v === "object") walk(v);
      if (Number.isFinite(found)) return;
    }
  };
  walk(j);
  return found;
}

// Which window is the 5-hour one and which is the weekly one? Read `limit_window_seconds` —
// do NOT assume primary=5h / secondary=weekly (2026-07-14).
//
// That positional assumption was wrong on a real account. The operator's `prolite` plan returns
// ONE window, and it is the WEEKLY one:
//     primary_window:   { used_percent: 29, limit_window_seconds: 604800 }   // 7 days
//     secondary_window: null
// So the weekly figure was published as "5h" (a "5-hour" window whose reset was six days out)
// and `weekly` came out null — which is why the dashboard's OpenAI card showed "—" for its
// headline number and an empty bar. Classify by DURATION, and keep the old positional mapping
// only as a fallback for a response that carries no duration at all.
export function classifyUsageWindows(rateLimit) {
  const rl = rateLimit || {};
  const list = [rl.primary_window, rl.secondary_window].filter((w) => w && typeof w === "object");
  let five = null;
  let week = null;
  for (const w of list) {
    const secs = Number(w.limit_window_seconds || w.window_seconds || 0);
    if (!(secs > 0)) continue;
    // A day is a clean divide: 5h = 18000s, weekly = 604800s. Anything short is the burst
    // window, anything long is the rolling one — no exact-value matching, so a plan with a
    // 3h or 30-day window still lands sensibly.
    if (secs <= 86400) five = five || w;
    else week = week || w;
  }
  if (!five && !week && list.length) {
    five = list[0] || null;
    week = list[1] || null;
  }
  return { five, week };
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
    const { five, week } = classifyUsageWindows(rl);
    if (five?.used_percent == null && week?.used_percent == null) return null;
    return normalizeUsage({
      sourceId: SOURCE_CODEX,
      fiveHourUsed: five ? five.used_percent : null,
      weeklyUsed: week ? week.used_percent : null,
      fiveHourResetsAt: five ? epochToIso(five.reset_at) : null,
      weeklyResetsAt: week ? epochToIso(week.reset_at) : null,
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

// ── OpenAI usage PREFLIGHT (2026-07-14) ──────────────────────────────────────────────
//
// The OpenAI quota pool fails SILENTLY by design: no token -> fall back to a rollout -> show a
// stale number. Nothing errors, so an operator (or an installing agent) has no way to know the
// dashboard's ChatGPT figure is dead. This turns that silence into a verdict, and it PROVES the
// connection rather than merely finding a file — a present-but-expired token is exactly the case
// a file check would call healthy.
//
// Returns { ok, code, message, detail }. Codes:
//   ok                 - token found AND the usage API accepted it
//   no-token           - no OpenAI token in any codex/hermes store (codex not installed / not logged in)
//   rejected           - a token was found but the API refused it (expired -> `codex login` again)
//   unreachable        - could not reach the API (offline / blocked); says nothing about the token
export async function checkOpenAiUsageAccess({ readHermesAuth = defaultReadHermesAuth, fetchImpl = globalThis.fetch } = {}) {
  let authText = "";
  let token = null;
  try {
    authText = readHermesAuth() || "";
    token = extractOpenAiToken(authText);
  } catch { token = null; }
  if (!token) {
    return {
      ok: false,
      code: "no-token",
      message: "OpenAI/ChatGPT usage will NOT appear in the dashboard: no OpenAI token found.",
      detail:
        "Install the codex CLI and sign in (`codex login`). Hermes delegates its OpenAI auth to the " +
        "codex store, so codex is what actually holds the token — a hermes-only install has none. " +
        "Searched: " + openAiAuthCandidates().join(", "),
    };
  }
  let res;
  try {
    res = await fetchImpl(CHATGPT_USAGE_URL, {
      headers: { authorization: `Bearer ${token}`, accept: "application/json", "user-agent": "codex-cli" },
    });
  } catch (err) {
    return {
      ok: false,
      code: "unreachable",
      message: "Found an OpenAI token, but could not reach the ChatGPT usage API.",
      detail: `Network/offline? ${String(err && err.message ? err.message : err)}`,
    };
  }
  if (!res || !res.ok) {
    // A REJECTED token is not automatically a broken login. codex keeps a refresh_token and
    // renews the ~10-day access token LAZILY, on its next use, so doctor can read a stale copy
    // out of auth.json while the login is perfectly healthy. Observed live 2026-08-09: HTTP 401
    // here, then GREEN three minutes later with no operator action — and the old wording had
    // already told the operator to run `codex login`, which was wrong. Let the predicate decide
    // which of those two situations this is; see doctor-predicates.js.
    const verdict = openAiUsageVerdict({
      hasToken: true,
      tokenExp: openAiTokenExpiry(token),
      hasRefreshToken: hasOpenAiRefreshToken(authText),
      lastRefresh: openAiLastRefreshEpoch(authText),
      httpStatus: res ? res.status : 0,
      apiOk: false,
    });
    return {
      ok: verdict.ok,
      code: verdict.code,
      message: verdict.detail,
      detail: verdict.fix,
    };
  }
  return { ok: true, code: "ok", message: "OpenAI/ChatGPT usage is connected.", detail: "" };
}
