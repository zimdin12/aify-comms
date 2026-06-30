// analytics.js — WS-C global analytics surface.
//
// Pure HTML builders for the Analytics page, ported from the old 8800 dashboard's
// renderAnalytics/renderTrafficChart/renderRunStatusMix. Consumes GET /api/v1/analytics
// (every field of which the new dashboard previously ignored). Kept pure (DOM-free) so the
// chart geometry is unit-testable; app.js owns fetching + mounting.

import { esc, relTime } from './util.js';
import { resolveStatus } from './status.js';

// ── Fleet pulse (Chat landing dashboard) ─────────────────────────────────────
// Window-scoped, glanceable fleet state: comms rate, working-utilization, and a
// board of online agents (last worked + in-window activity). Consumes
// GET /analytics/pulse?window_minutes=N. Pure builders; app.js fetches + mounts.

export const PULSE_WINDOWS = [
  { m: 10, label: '10m' }, { m: 30, label: '30m' }, { m: 60, label: '1h' },
  { m: 180, label: '3h' }, { m: 360, label: '6h' }, { m: 720, label: '12h' }, { m: 1440, label: '24h' },
];

export function pulseWindowSelectorHtml(active = 60) {
  return PULSE_WINDOWS.map((w) =>
    `<button type="button" class="${w.m === active ? 'active' : ''}" data-pulse-window="${w.m}">${esc(w.label)}</button>`
  ).join('');
}

function pulseWorkedLabel(iso) {
  if (!iso) return 'no work yet';
  const t = Date.parse(String(iso));
  if (!Number.isFinite(t)) return 'no work yet';
  return `worked ${relTime(iso)} ago`;
}

export function fleetPulseHtml(data, windowMinutes = 60) {
  const winLabel = (PULSE_WINDOWS.find((w) => w.m === windowMinutes) || { label: `${windowMinutes}m` }).label;
  // Title lives in the conversation header bar; here we just offer the window selector.
  const head = `<div class="pulse-head"><span class="pulse-head-label em">Comms performance + online agents</span>`
    + `<div class="segmented pulse-window" role="group" aria-label="Pulse window">${pulseWindowSelectorHtml(windowMinutes)}</div></div>`;
  if (!data || data.ok === false) {
    return `<div class="pulse">${head}<p class="em">${data ? 'Pulse unavailable.' : 'Loading fleet pulse…'}</p></div>`;
  }
  const m = data.messages || {};
  const util = data.fleetUtilizationPct;
  const utilTone = util == null ? '' : (util >= 50 ? 'good' : util >= 15 ? 'warn' : '');
  const overdue = Number(data.overdueReplyContracts || 0);
  const card = (n, l, sub, tone) => `<div class="metric${tone ? ` ${tone}` : ''}" data-tone="${tone || ''}">`
    + `<b>${esc(String(n))}</b><span>${esc(l)}</span>${sub ? `<small class="pulse-sub">${esc(sub)}</small>` : ''}</div>`;
  const dot = (k, label) => `<span class="status-dot ${esc(k)}" role="img" title="${esc(label || k)}" aria-label="${esc(label || k)}"></span>`;
  const rows = (data.agents || []).filter((a) => a && a.id).map((a) => {
    const st = resolveStatus(a.status);
    const worked = a.workingNow ? '<span class="pulse-now">● working now</span>' : esc(pulseWorkedLabel(a.lastWorkedAt));
    return `<button class="pulse-agent" data-chat-open="dm:${esc(a.id)}" title="Open chat with ${esc(a.id)}">`
      + `<span class="pulse-agent-id">${dot(st.dotKind, st.label)}<strong>${esc(a.id)}</strong>`
      + `<small>${esc(a.role || '')}${a.runtime ? ` · ${esc(a.runtime)}` : ''}${a.mode ? ` · ${esc(a.mode)}` : ''}</small></span>`
      + `<span class="pulse-agent-meta"><span class="pulse-worked">${worked}</span>`
      + `<span class="pulse-counts">${Number(a.messagesInWindow || 0)} msg · ${Number(a.workingMinutesInWindow || 0)}m work</span></span></button>`;
  }).join('');
  return `<div class="pulse">${head}
    <div class="pulse-cards metric-grid">
      ${card(m.count ?? 0, `Messages · ${winLabel}`, `${m.perHour ?? 0}/hr`)}
      ${card(util == null ? '—' : `${util}%`, 'Utilization', `${data.fleetWorkingMinutes || 0}m working`, utilTone)}
      ${card(data.workingNow ?? 0, 'Working now', `${data.onlineAgents || 0} online`)}
      ${card(data.openReplyContracts ?? 0, 'Open replies', overdue ? `${overdue} overdue` : 'all current', overdue ? 'bad' : '')}
    </div>
    <div class="pulse-board-head"><h4>Online agents</h4><span class="em">${data.onlineAgents || 0} online · working first</span></div>
    <div class="pulse-board">${rows || '<p class="em">No online agents right now.</p>'}</div>
  </div>`;
}

export const ANALYTICS_RANGES = [
  { key: 'hour', label: '24h', seriesKey: 'messagesPerHour', maxItems: 24, windowLabel: 'last 24 hours' },
  { key: 'day', label: '30d', seriesKey: 'messagesPerDay', maxItems: 30, windowLabel: 'last 30 days' },
  { key: 'month', label: '12m', seriesKey: 'messagesPerMonth', maxItems: 12, windowLabel: 'last 12 months' },
  { key: 'all', label: 'All', seriesKey: 'messagesPerAllTime', maxItems: 0, windowLabel: 'all time' },
];

export function rangeDef(range) {
  return ANALYTICS_RANGES.find((r) => r.key === range) || ANALYTICS_RANGES[0];
}

export function analyticsSeries(data = {}, range = 'hour') {
  const def = rangeDef(range);
  const items = data[def.seriesKey] || [];
  const maxItems = def.maxItems > 0 ? def.maxItems : Math.max(1, items.length);
  return { label: def.windowLabel, items, maxItems };
}

// Build the composite traffic SVG (grid + axis + bars + area + line + dots) + summary.
export function trafficChartHtml(data = {}, range = 'hour') {
  const series = analyticsSeries(data, range);
  const items = (series.items || []).slice(-series.maxItems);
  const counts = items.map((it) => Number(it.count || 0));
  const total = counts.reduce((s, c) => s + c, 0);
  const max = Math.max(1, ...counts);
  const avg = counts.length ? total / counts.length : 0;
  const last = counts.length ? counts[counts.length - 1] : 0;
  const w = 720, h = 240, padX = 26, padTop = 18, padBottom = 34;
  const innerW = w - padX * 2;
  const innerH = h - padTop - padBottom;
  const points = counts.map((count, i) => {
    // Center each point over its bar (band model) so the line/dots align with the bars,
    // instead of the old edge-to-edge (i/(n-1)) geometry that drifted off the columns.
    const x = padX + (i + 0.5) * (innerW / Math.max(1, counts.length));
    const y = padTop + innerH - (count / max) * innerH;
    return { x, y, count, item: items[i] };
  });
  const line = points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const area = points.length ? `${padX},${padTop + innerH} ${line} ${padX + innerW},${padTop + innerH}` : '';
  const bars = points.map((p, i) => {
    const gap = 3;
    const barW = Math.max(3, (innerW / Math.max(1, counts.length)) - gap);
    const x = padX + i * (innerW / Math.max(1, counts.length)) + gap / 2;
    const barH = Math.max(2, padTop + innerH - p.y);
    return `<rect class="chart-bar" x="${x.toFixed(1)}" y="${p.y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="3"><title>${esc(p.item?.label || '')}: ${p.count}</title></rect>`;
  }).join('');
  const dots = points
    .filter((_, i) => items.length <= 14 || i === 0 || i === items.length - 1 || counts[i] === max)
    .map((p) => `<circle class="chart-dot" cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="4"><title>${esc(p.item?.label || '')}: ${p.count}</title></circle>`)
    .join('');
  // Full-height transparent hover bands per bucket: the bars are only as tall as their value, so
  // hovering the empty space above a short bar hit nothing. A full-column band makes the whole
  // column hoverable (tooltip + highlight) regardless of bar height.
  const hoverBands = points.map((p, i) => {
    const colW = innerW / Math.max(1, counts.length);
    const bx = padX + i * colW;
    return `<rect class="chart-hover-band" x="${bx.toFixed(1)}" y="${padTop.toFixed(1)}" width="${colW.toFixed(1)}" height="${innerH.toFixed(1)}"><title>${esc(p.item?.label || '')}: ${p.count} message${p.count === 1 ? '' : 's'}</title></rect>`;
  }).join('');
  const labels = items.length ? [items[0], items[Math.floor(items.length / 2)], items[items.length - 1]] : [];
  return `<div class="chart-wrap">`
    + `<svg class="chart-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="Message traffic ${esc(series.label)}">`
    + `<defs><linearGradient id="trafficGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity=".34"/><stop offset="100%" stop-color="var(--tertiary)" stop-opacity="0"/></linearGradient></defs>`
    + [0, .25, .5, .75, 1].map((t) => `<line class="chart-grid" x1="${padX}" x2="${padX + innerW}" y1="${(padTop + innerH * t).toFixed(1)}" y2="${(padTop + innerH * t).toFixed(1)}"/>`).join('')
    + `<line class="chart-axis" x1="${padX}" x2="${padX + innerW}" y1="${padTop + innerH}" y2="${padTop + innerH}"/>`
    + bars
    + (area ? `<polygon class="chart-area" points="${area}"></polygon>` : '')
    + (line ? `<polyline class="chart-line" points="${line}"></polyline>` : '')
    + dots
    + hoverBands
    + `</svg>`
    + `<div class="chart-labels">${labels.map((it) => `<span title="${esc(it?.start || it?.label || '')}">${esc(it?.label || '')}</span>`).join('')}</div>`
    + `<div class="chart-summary"><div><b>${total}</b><span>Total messages</span></div><div><b>${Math.round(avg * 10) / 10}</b><span>Average</span></div><div><b>${last}</b><span>Latest bucket</span></div></div>`
    + `</div>`;
}

export function statCardsHtml(data = {}) {
  const runs = data.runsByStatus || {};
  const rangeLabel = data.rangeLabel || '';
  const failedRuns = Number(runs.failed || 0) + Number(runs.cancelled || 0);
  const failedSpawns = Number((data.spawnRequestsByStatus || {}).failed || 0);
  const card = (n, l, sub, tone, tip) => `<div class="sc${tone ? ` ${tone}` : ''}"${tip ? ` title="${esc(tip)}"` : ''}><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(sub)}</div></div>`;
  const win = rangeLabel || 'this window';
  return card(data.messageTotal || 0, 'Messages', rangeLabel, '', `Total chat messages sent across the fleet (${win}).`)
    + card(data.runTotal || 0, 'Runs', rangeLabel, '', `Dispatch runs created — every comms_send/wake that targets an agent (${win}).`)
    + card(runs.completed || 0, 'Completed', rangeLabel, '', `Runs that finished successfully (${win}).`)
    + card(failedRuns, 'Failed / Cancelled', rangeLabel, failedRuns ? 'warn' : '', `Runs that failed or were cancelled (${win}).`)
    + card(failedSpawns, 'Spawn failures', rangeLabel, failedSpawns ? 'warn' : '', `Agent spawn requests that failed to start (${win}).`)
    + card(runs.running || 0, 'Running runs', 'current window', '', 'Runs in progress right now (point-in-time, not windowed).');
}

export function healthGridHtml(data = {}) {
  const failedSpawns = Number((data.spawnRequestsByStatus || {}).failed || 0);
  const card = (n, l, tone, tip) => `<div class="health-card${tone ? ` ${tone}` : ''}"${tip ? ` title="${esc(tip)}"` : ''}><b>${esc(n)}</b><span>${esc(l)}</span></div>`;
  return `<div class="health-grid">`
    + card(data.liveAgents || 0, 'Live agents', 'good', 'Agents with a live worker + fresh heartbeat right now.')
    + card(data.onlineAgents || 0, 'Online agents', '', 'Agents ready for work right now (online or working).')
    + card(data.workingAgents || 0, 'Working now', 'warn', 'Agents currently mid-turn (actively running).')
    + card(data.onlineEnvironments || 0, 'Online envs', '', 'Environment bridges reachable right now (managed agents can be spawned on these).')
    + card(data.spawnRequestTotal || 0, 'Spawn requests', '', 'Total spawn requests recorded in this window.')
    + card(failedSpawns, 'Spawn failures', failedSpawns ? 'bad' : '', 'Spawn requests that failed to start (e.g. unreachable env / runtime error).')
    + `</div>`;
}

export function runStatusMixHtml(runsByStatus = {}) {
  const entries = Object.entries(runsByStatus).filter(([, c]) => Number(c || 0) > 0);
  if (!entries.length) return '<p class="em">No tracked runs yet.</p>';
  const max = Math.max(1, ...entries.map(([, c]) => Number(c || 0)));
  return `<div class="status-bars">` + entries
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([status, count]) => {
      const width = Math.max(3, Math.round((Number(count || 0) / max) * 100));
      // Color the dot by run-status via the same data-status taxonomy as the fill
      // (run statuses queued/claimed/running/completed/failed/cancelled/lost have no
      // .status-dot.<x> rule, so a raw class rendered them all muted-grey — WS-6).
      return `<div class="status-bar-row"><span class="status-bar-label"><span class="status-dot" data-status="${esc(status)}"></span>${esc(status)}</span><span class="status-bar-track"><span class="status-bar-fill" data-status="${esc(status)}" style="width:${width}%"></span></span><span class="status-bar-value">${Number(count || 0)}</span></div>`;
    }).join('') + `</div>`;
}

// ── Fleet operational analytics (2026-06-17 "real analytics" round) ─────────────
// All builders are pure + null-safe; app.js fetches /analytics and mounts these.

function fmtMins(m) {
  if (m == null) return '—';
  const n = Number(m);
  if (!Number.isFinite(n)) return '—'; // non-numeric input must not render "NaNm"
  return n >= 60 ? `${Math.floor(n / 60)}h ${Math.round(n % 60)}m` : `${Math.round(n)}m`;
}

// Headline operational KPIs: dispatch success rate, fleet median reply, open + overdue contracts.
export function opsKpisHtml(data = {}) {
  const sr = data.successRate;
  const srTone = sr == null ? '' : (sr >= 90 ? 'good' : sr >= 70 ? 'warn' : 'bad');
  const overdue = Number(data.overdueReplyContracts || 0);
  const open = Number(data.openReplyContracts || 0);
  const card = (n, l, sub, tone, tip) => `<div class="sc${tone ? ` ${tone}` : ''}"${tip ? ` title="${esc(tip)}"` : ''}><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(sub)}</div></div>`;
  return card(sr == null ? '—' : `${sr}%`, 'Dispatch success', `${Number(data.runsCompleted || 0)} ok · ${Number(data.runsFailed || 0)} failed`, srTone, 'Share of FINISHED runs that completed vs failed/cancelled, in this window. 90%+ green, 70-90% amber, below red.')
    + card(fmtMins(data.fleetMedianReplyMinutes), 'Median reply', 'completed required replies', '', 'Median time agents took to send a REQUIRED reply (only runs whose reply already landed count).')
    + card(open, 'Open contracts', 'awaiting reply now', open ? 'warn' : '', 'Reply contracts still awaiting an answer right now (point-in-time).')
    + card(overdue, 'Overdue', '> 30 min unanswered', overdue ? 'bad' : '', 'Open reply contracts unanswered past the reminder threshold (default 30 min).');
}

// Stacked completed/failed dispatch outcomes over the last 14 days.
export function dispatchOutcomesHtml(data = {}) {
  const days = Array.isArray(data.dispatchOutcomes) ? data.dispatchOutcomes : [];
  if (!days.length) return '<p class="em">No dispatch runs in the last 14 days.</p>';
  const max = Math.max(1, ...days.map((d) => Number(d.completed || 0) + Number(d.failed || 0)));
  return `<div class="outcome-chart">` + days.map((d) => {
    const comp = Number(d.completed || 0); const fail = Number(d.failed || 0); const tot = comp + fail;
    // Floor nonzero values at a visible 2% so a real run never renders as an invisible 0-height bar.
    const ch = comp ? Math.max(2, Math.round((comp / max) * 100)) : 0;
    const fh = fail ? Math.max(2, Math.round((fail / max) * 100)) : 0;
    return `<span class="outcome-col" title="${esc(String(d.date || '').slice(5))}: ${comp} completed · ${fail} failed">`
      + `<span class="outcome-stack">`
      + `<span class="outcome-fail" style="height:${fh}%"></span>`
      + `<span class="outcome-comp" style="height:${ch}%"></span>`
      + `</span><span class="outcome-x">${esc(String(d.date || '').slice(5))}</span><span class="outcome-n">${tot || ''}</span></span>`;
  }).join('') + `</div>`
    + `<div class="outcome-legend"><span><i class="swatch comp"></i>Completed</span><span><i class="swatch fail"></i>Failed</span></div>`;
}

// Top dispatch targets with per-agent success rate.
export function agentLeaderboardHtml(data = {}) {
  const rows = Array.isArray(data.agentLeaderboard) ? data.agentLeaderboard : [];
  if (!rows.length) return '<p class="em">No dispatch activity in this window.</p>';
  const max = Math.max(1, ...rows.map((r) => Number(r.total || 0)));
  return `<div class="lb">` + rows.map((r) => {
    const total = Number(r.total || 0);
    const w = Math.max(3, Math.round((total / max) * 100));
    const sr = r.successRate;
    const srTone = sr == null ? '' : (sr >= 90 ? 'good' : sr >= 70 ? 'warn' : 'bad');
    return `<div class="lb-row"><span class="lb-name clip">${esc(r.agent)}</span>`
      + `<span class="lb-track"><span class="lb-fill" style="width:${w}%"></span></span>`
      + `<span class="lb-meta">${Number(r.completed || 0)}/${total}${sr == null ? '' : ` <b class="lb-sr ${srTone}">${sr}%</b>`}</span></div>`;
  }).join('') + `</div>`;
}

// Busiest channels by message volume.
export function busiestChannelsHtml(data = {}) {
  const rows = Array.isArray(data.busiestChannels) ? data.busiestChannels : [];
  if (!rows.length) return '<p class="em">No channel traffic in this window.</p>';
  const max = Math.max(1, ...rows.map((r) => Number(r.count || 0)));
  return `<div class="lb">` + rows.map((r) => {
    const w = Math.max(3, Math.round((Number(r.count || 0) / max) * 100));
    return `<div class="lb-row"><span class="lb-name clip">#${esc(r.channel)}</span>`
      + `<span class="lb-track"><span class="lb-fill" style="width:${w}%"></span></span>`
      + `<span class="lb-meta">${Number(r.count || 0)}</span></div>`;
  }).join('') + `</div>`;
}

// Failure reasons — what's actually breaking, grouped by error text.
export function failureReasonsHtml(data = {}) {
  const rows = Array.isArray(data.failureReasons) ? data.failureReasons : [];
  if (!rows.length) return '<p class="em good-note">No failed or cancelled runs in this window. 🎉</p>';
  return `<ul class="fail-list">` + rows.map((r) =>
    `<li><span class="fail-n">${Number(r.count || 0)}×</span> <span class="fail-r clip" title="${esc(r.reason)}">${esc(r.reason)}</span></li>`
  ).join('') + `</ul>`;
}

export function rangeSelectorHtml(activeRange = 'hour') {
  return ANALYTICS_RANGES.map((r) =>
    `<button type="button" class="${r.key === activeRange ? 'active' : ''}" data-analytics-range="${r.key}">${esc(r.label)}</button>`
  ).join('');
}
