// The Analytics page and the usage-pool tiles.
//
// One fetch, two renderers, and a cache. `loadAnalytics` is the only reader of `/analytics`, and its
// `force` flag is the whole reason it is worth testing: the page is re-rendered on every poll, so an
// unconditional fetch would put a tenth request on a ~15s cycle for data that changes far more slowly.
// The gate has to hold for the poll and NOT hold when the operator changes the range, or the range
// selector appears to do nothing.
//
// Nothing here is injected. Everything it needs is already an export of a sibling, which is what makes
// this one of the few groups that leaves app.js without a dependency bag at all.

import { agentLeaderboardHtml, busiestChannelsHtml, dispatchOutcomesHtml, failureReasonsHtml, healthGridHtml, opsKpisHtml, rangeDef, rangeSelectorHtml, runStatusMixHtml, statCardsHtml, trafficChartHtml } from './analytics.js';
import { api } from './api-client.mjs';
import { state } from './state.mjs';
import { renderUsageConsumption } from './summary-tiles.mjs';
import { byId, toast } from './ui.js';
import { esc, usageResetLabel } from './util.js';


// Fetch the analytics page's data (analytics + usage pools + consumption) into state,
// then render. Throttled + in-flight-guarded so the WS-driven renderAll loop (which can
// fire many times/sec) collapses to at most one fetch per ~12s — the backend is
// single-worker + lock-sensitive. Pass force=true on page-open / range-change / manual.
export async function loadAnalytics(force = false) {
  if (state.analytics.loading) return;
  if (!force && state.analytics.lastMs && (Date.now() - state.analytics.lastMs) < 12000) {
    renderAnalyticsPage();
    return;
  }
  const range = rangeDef(state.analytics.range).key;
  state.analytics.loading = true;
  try {
    const [data, usage, consumption] = await Promise.all([
      api(`/analytics?range=${encodeURIComponent(range)}`).catch(() => null),
      api('/usage').catch(() => null),
      api('/usage/consumption').catch(() => null),
    ]);
    if (data && typeof data === 'object') state.analytics.data = data;
    else if (!state.analytics.data) state.analytics.data = {};
    // Keep last-good usage on a transient failure (never blank a live quota number);
    // flag it stale so the panel can say so.
    if (usage) { state.analytics.usage = usage; state.analytics.usageStale = false; }
    else if (state.analytics.usage) state.analytics.usageStale = true;
    if (consumption) state.analytics.consumption = consumption;
    state.analytics.lastMs = Date.now();
  } catch (error) {
    if (!state.analytics.data) state.analytics.data = {};
    toast(`Analytics failed: ${error?.message || error}`, 'error');
  } finally {
    state.analytics.loading = false;
    renderAnalyticsPage();
  }
}

// Usage/quota Pools band + Consumption section (2026-06-26). Advisory — read-only.
export function renderUsagePools() {
  const host = byId('usage-pools');
  if (!host) return;
  const pools = (state.analytics.usage && state.analytics.usage.pools) || [];
  if (!pools.length) { host.innerHTML = '<p class="em">Usage collector warming up…</p>'; return; }
  // Stale notice when the last refresh failed (we keep showing last-good rather than blanking).
  const staleNote = state.analytics.usageStale ? '<p class="subtle usage-stale-note">⚠ Last usage refresh failed — showing last known values.</p>' : '';
  const LABELS = {
    'anthropic-claude-max': 'Anthropic · Claude Max',
    'openai-chatgpt-codex': 'OpenAI · ChatGPT (Codex + Hermes)',
    'local-ollama': 'Local · Ollama',
  };
  host.innerHTML = staleNote + pools.map((p) => {
    const w = p.weekly || {}, f = p.five_hour || {};
    const sev = (p.severity && p.severity !== 'normal') ? p.severity : '';
    const left = (p.verified === false || w.left_pct == null) ? '—' : w.left_pct + '%';
    const fleft = (f.left_pct == null) ? '—' : f.left_pct + '%';
    const used = (p.verified === false || w.used_pct == null) ? 0 : Math.max(0, Math.min(100, w.used_pct));
    const fiveHourReset = f.resets_at ? usageResetLabel(f.resets_at) : '';
    const tags = (p.unknown ? '<span class="usage-tag">unknown</span>' : '') + (p.stale ? '<span class="usage-tag">stale</span>' : '');
    const name = LABELS[p.source_id] || p.source_id;
    // Backend blanks the numbers (→ "—") when they can't be trusted; the note says why so agents
    // treat it as unknown instead of a live value. `expired` = collector stopped (>24h); `reset_elapsed`
    // = the window already reset after this snapshot (e.g. a stale codex/hermes rollout).
    // NEVER publish a number we cannot stand behind. The OpenAI card showed "100% left" while the
    // operator was actually at ~64% used — it faithfully echoed an endpoint that turned out to be
    // metering something else. Their verdict, and it is the right rule: "it lies... it is worse
    // than not showing". So an UNVERIFIED pool renders "—" and says why, and its raw readings are
    // shown as EVIDENCE below, never as the headline.
    const staleMsg = p.expired ? 'No fresh quota data in 24h+'
      : p.reset_elapsed ? 'Quota window already reset — awaiting a fresh reading'
      : (p.verified === false ? (p.unverified_reason || 'Source not trusted for this account') : '');
    // Evidence line: what the source actually returned, labelled as such, so it informs without
    // pretending to be the operator's quota.
    const ev = [];
    if (f.used_pct != null) ev.push(`5h ${f.used_pct}% used`);
    if (w.used_pct != null) ev.push(`weekly ${w.used_pct}% used`);
    if (p.credits && p.credits.messages_left != null) ev.push(`~${p.credits.messages_left} msgs credit`);
    if (p.limit_reached) ev.push('limit reached');
    const evidence = ev.length ? `<div class="usage-pool-meta subtle">source says: ${esc(ev.join(' · '))}</div>` : '';
    const meta = staleMsg
      ? `<div class="usage-pool-meta usage-pool-expired">⚠ ${esc(staleMsg)}</div>${evidence}`
      : `<div class="usage-pool-meta">5h ${fleft} left${fiveHourReset ? ' · ' + esc(fiveHourReset) : ''}</div>`;
    return `<div class="usage-pool-card ${sev}"><div class="usage-pool-name"><span>${esc(name)}</span><span>${tags}</span></div>`
      + `<div class="usage-pool-weekly">${left}<span class="usage-pool-sub"> weekly left</span></div>`
      + `<div class="usage-pool-bar"><span style="width:${used}%"></span></div>`
      + meta + `</div>`;
  }).join('');
}

export function renderAnalyticsPage() {
  // Single KPI grid (2026-06-29): ops + stats cards render into ONE .metric-grid so the two rows
  // can't have mismatched card widths/rhythm — they pack uniformly as one auto-fit grid.
  const kpiHost = byId('analytics-ops');
  if (!kpiHost) return;
  renderUsagePools();
  renderUsageConsumption();
  const data = state.analytics.data;
  const rangeHost = byId('analytics-range');
  if (rangeHost) rangeHost.innerHTML = rangeSelectorHtml(state.analytics.range);
  if (!data) {
    // One coherent page-level empty state instead of a message + 6 stale/blank panels below it.
    kpiHost.innerHTML = '';
    const traffic = byId('analytics-traffic');
    if (traffic) traffic.innerHTML = `<p class="em">${state.analytics.loading ? 'Loading analytics…' : 'No analytics yet — open the page to load fleet metrics.'}</p>`;
    ['analytics-outcomes', 'analytics-leaderboard', 'analytics-channels', 'analytics-health', 'analytics-runs', 'analytics-failures'].forEach((id) => { const el = byId(id); if (el) el.innerHTML = ''; });
    return;
  }
  kpiHost.innerHTML = opsKpisHtml(data) + statCardsHtml(data);
  const traffic = byId('analytics-traffic');
  if (traffic) traffic.innerHTML = trafficChartHtml(data, state.analytics.range);
  const outcomes = byId('analytics-outcomes');
  if (outcomes) outcomes.innerHTML = dispatchOutcomesHtml(data);
  const leaderboard = byId('analytics-leaderboard');
  if (leaderboard) leaderboard.innerHTML = agentLeaderboardHtml(data);
  const channels = byId('analytics-channels');
  if (channels) channels.innerHTML = busiestChannelsHtml(data);
  const health = byId('analytics-health');
  if (health) health.innerHTML = healthGridHtml(data);
  const runs = byId('analytics-runs');
  if (runs) runs.innerHTML = runStatusMixHtml(data.runsByStatus || {});
  const failures = byId('analytics-failures');
  if (failures) failures.innerHTML = failureReasonsHtml(data);
}
