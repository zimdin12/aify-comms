// The overview summary tiles: fleet metrics, the diagnostics summary, and token consumption.
//
// They are one module because they are one thing: small stat panels built from the same `metric` tile
// helper. `metric` is read by two of them and nothing else, so it moves with them rather than becoming a
// shared utility with no owner — the ownership test throughout this series is a count of DIRECT readers.
//
// `selectedDiagnostics` comes along for the same reason: it is the diagnostics summary's own selection
// filter, and it is the piece with real logic — a selection that outlives the rows it pointed at would
// have the operator acting in bulk on records that are no longer there.
//
// Extracted from app.js in v0.5.4 as a measured closure needing only sibling leaf modules imported
// downward.
//
// The declarations are byte-identical to those that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Their leading comments stayed behind
// in app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its
// comments could not round-trip through the proof.

export function renderUsageConsumption() {
  const host = byId('usage-consumption');
  if (!host) return;
  const s = state.analytics.consumption;
  const byAgent = (s && s.by_agent) || {};
  const agents = Object.keys(byAgent);
  if (!agents.length) { host.innerHTML = '<p class="em">No per-agent token data yet (collector warming up).</p>'; return; }
  agents.sort((a, b) => (byAgent[b].output_tokens || 0) - (byAgent[a].output_tokens || 0));
  const rows = agents.map((a) => {
    const c = byAgent[a];
    return `<tr><td>${esc(a)}</td><td>${usageFmtTokens(c.input_tokens)}</td><td>${usageFmtTokens(c.output_tokens)}</td><td>${usageFmtTokens(c.cache_tokens)}</td></tr>`;
  }).join('');
  const t = (s && s.totals) || {};
  host.innerHTML = '<div class="table-wrap"><table class="usage-consumption-table"><thead><tr><th>Agent</th><th>In</th><th>Out</th><th>Cache</th></tr></thead>'
    + `<tbody>${rows}</tbody>`
    + `<tfoot><tr><td>Total</td><td>${usageFmtTokens(t.input_tokens)}</td><td>${usageFmtTokens(t.output_tokens)}</td><td>${usageFmtTokens(t.cache_tokens)}</td></tr></tfoot></table></div>`;
}

import { state } from './state.mjs';
import { resolveStatus } from './status.js';
import { byId } from './ui.js';
import { esc, usageFmtTokens } from './util.js';

export function metric(label, value, tone = 'neutral', attrs = '') {
  // attrs is caller-provided raw HTML attributes (e.g. a data-* jump target), never user input.
  return `<div class="metric${attrs ? ' metric-clickable' : ''}" data-tone="${esc(tone)}"${attrs}><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}
export function renderMetrics() {
  const working = state.agents.filter((a) => resolveStatus(a.status).kind === 'working').length;
  const blocked = state.agents.filter((a) => resolveStatus(a.status).kind === 'blocked').length;
  const active = state.agents.filter((a) => ['active', 'online', 'working', 'blocked'].includes(resolveStatus(a.status).kind)).length;
  const overdue = state.contracts.filter((c) => c.overdue).length;
  const queued = state.contracts.filter((c) => c.state === 'queued').length;
  byId('metrics').innerHTML = [
    metric('Active agents', active, 'ok'),
    metric('Working now', working, working ? 'working' : 'neutral'),
    metric('Blocked agents', blocked, blocked ? 'bad' : 'neutral'),
    metric('Overdue work', overdue, overdue ? 'warn' : 'neutral'),
    metric('Queued contracts', queued, queued ? 'queued' : 'neutral'),
  ].join('');
}
export function selectedDiagnostics() {
  const selected = [];
  const contractById = new Map(state.contracts.map((contract) => [String(contract.id), contract]));
  const runById = new Map(state.runs.map((run) => [String(run.id), run]));
  for (const key of state.selectedDiagnosticIds) {
    const [kind, ...rest] = String(key).split(':');
    const id = rest.join(':');
    if (kind === 'contract' && contractById.has(id)) selected.push({ kind, id, item: contractById.get(id) });
    if (kind === 'run' && runById.has(id)) selected.push({ kind, id, item: runById.get(id) });
  }
  return selected;
}
export function renderDiagnosticsSummary() {
  const target = byId('diagnostics-summary');
  if (!target) return;
  // Summary tiles describe the FLEET, not the current Work-Loop/Runs filter. Use the unfiltered
  // open-contracts snapshot (contractsBase) + fleet-wide /stats so changing a filter never moves
  // the headline numbers.
  const baseContracts = state.contractsBase || state.contracts;
  const runsByStatus = state.stats?.dispatch_runs_by_status || {};
  const openWork = baseContracts.filter((contract) => ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state)).length;
  const overdue = baseContracts.filter((contract) => contract.overdue).length;
  const activeRuns = (Number(runsByStatus.claimed) || 0) + (Number(runsByStatus.running) || 0);
  const failedRuns = Number(state.stats?.run_failures_24h) || 0;
  // Tiles are triage shortcuts: clicking jumps to the matching Work-Loop/Runs filter.
  const jump = (t) => ` data-diag-jump="${t}" role="button" tabindex="0" title="Filter to ${esc(t.replace('run:', ''))}"`;
  target.innerHTML = [
    metric('Open work', openWork, openWork ? 'warn' : 'neutral', jump('open')),
    metric('Overdue', overdue, overdue ? 'bad' : 'neutral', jump('overdue')),
    metric('Active runs', activeRuns, activeRuns ? 'working' : 'neutral', jump('run:running')),
    metric('Failed recent', failedRuns, failedRuns ? 'bad' : 'neutral', jump('run:failed')),
  ].join('');
}
