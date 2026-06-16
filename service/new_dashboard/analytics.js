// analytics.js — WS-C global analytics surface.
//
// Pure HTML builders for the Analytics page, ported from the old 8800 dashboard's
// renderAnalytics/renderTrafficChart/renderRunStatusMix. Consumes GET /api/v1/analytics
// (every field of which the new dashboard previously ignored). Kept pure (DOM-free) so the
// chart geometry is unit-testable; app.js owns fetching + mounting.

import { esc } from './util.js';

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
    const x = padX + (counts.length <= 1 ? innerW : (i / (counts.length - 1)) * innerW);
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
  const card = (n, l, sub, tone) => `<div class="sc${tone ? ` ${tone}` : ''}"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div><div class="s">${esc(sub)}</div></div>`;
  return card(data.messageTotal || 0, 'Messages', rangeLabel)
    + card(data.runTotal || 0, 'Runs', rangeLabel)
    + card(runs.completed || 0, 'Completed', rangeLabel)
    + card(failedRuns, 'Failed / Cancelled', rangeLabel, failedRuns ? 'warn' : '')
    + card(failedSpawns, 'Spawn failures', rangeLabel, failedSpawns ? 'warn' : '')
    + card(runs.running || 0, 'Running runs', 'current window');
}

export function healthGridHtml(data = {}) {
  const failedSpawns = Number((data.spawnRequestsByStatus || {}).failed || 0);
  const card = (n, l, tone) => `<div class="health-card${tone ? ` ${tone}` : ''}"><b>${esc(n)}</b><span>${esc(l)}</span></div>`;
  return `<div class="health-grid">`
    + card(data.liveAgents || 0, 'Live agents', 'good')
    + card(data.onlineAgents || 0, 'Online agents')
    + card(data.workingAgents || 0, 'Working now', 'warn')
    + card(data.onlineEnvironments || 0, 'Online envs')
    + card(data.spawnRequestTotal || 0, 'Spawn requests')
    + card(failedSpawns, 'Spawn failures', failedSpawns ? 'bad' : '')
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
      return `<div class="status-bar-row"><span class="status-bar-label"><span class="status-dot ${esc(status)}"></span>${esc(status)}</span><span class="status-bar-track"><span class="status-bar-fill" style="width:${width}%"></span></span><span class="status-bar-value">${Number(count || 0)}</span></div>`;
    }).join('') + `</div>`;
}

export function rangeSelectorHtml(activeRange = 'hour') {
  return ANALYTICS_RANGES.map((r) =>
    `<button type="button" class="${r.key === activeRange ? 'active' : ''}" data-analytics-range="${r.key}">${esc(r.label)}</button>`
  ).join('');
}
