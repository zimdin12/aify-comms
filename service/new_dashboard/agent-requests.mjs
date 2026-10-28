// What became of each change the operator asked of a DEFINED agent's host: its lifecycle requests (start, stop/hold,
// kill, restart, release, delete) and its definition requests (Edit). Asking only says "queued"; the host claims a
// request later and answers done, refused, failed or (a definition request no host claimed) expired, with why in
// `outcome`. Operator, 2026-10-28: the dashboard reads those outcomes back instead of stopping at the toast.
//
// The rows are pure; the loader asks both lists and reports a list it could not read rather than hiding it.

import { api } from './api-client.mjs';
import { esc, relTime } from './util.js';

/** The newest rows shown; the history lists hold every request ever asked. */
export const REQUEST_ROWS = 20;

const ACTION_WORDS = { stop: 'hold', kill: 'stop', release: 'release hold', restart: 'restart', start: 'start',
  spawn: 'spawn', delete: 'remove' };

/** PURE. One list of both kinds, newest first, each as the drawer words it. */
export function requestRows(lifecycle = [], definition = []) {
  const rows = [
    ...lifecycle.map((r) => ({
      kind: 'lifecycle',
      what: `${ACTION_WORDS[r.action] || r.action}${r.freshContext ? ' (fresh context)' : ''}`,
      status: r.status, outcome: r.outcome || '', by: r.requestedBy || '', at: r.createdAt || '',
    })),
    ...definition.map((r) => ({
      kind: 'definition',
      what: r.patch && r.patch.remove === true ? 'remove definition' : `edit ${Object.keys(r.patch || {}).join(', ')}`,
      status: r.status, outcome: r.outcome || '', by: r.requestedBy || '', at: r.createdAt || '',
    })),
  ];
  return rows.sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, REQUEST_ROWS);
}

/** PURE. The drawer section; `problems` names a list that could not be read. */
export function requestsSection(rows, problems = []) {
  const failed = problems.map((p) => `<p class="subtle">Could not read ${esc(p)}.</p>`).join('');
  if (!rows.length) return `<div class="agent-drawer-subhead">Requests to its host</div>${failed}<p class="subtle">None asked.</p>`;
  const body = rows.map((r) => `<div class="history-row">
      <div class="history-head"><strong>${esc(r.what)}</strong> <span class="request-status request-${esc(r.status)}">${esc(r.status)}</span></div>
      <dl class="agent-drawer-kv">
        <dt>When</dt><dd>${esc(relTime(r.at))} ago</dd>
        <dt>Requested by</dt><dd>${esc(r.by || 'not recorded')}</dd>
        ${r.outcome ? `<dt>Outcome</dt><dd>${esc(r.outcome)}</dd>` : ''}
      </dl></div>`).join('');
  return `<div class="agent-drawer-subhead">Requests to its host</div>${failed}${body}`;
}

/** Both lists for one agent, as the section's html. A list that fails is named; the other is still shown. */
export async function loadAgentRequests(agentId, ask = api) {
  const id = encodeURIComponent(agentId);
  const [lifecycle, definition] = await Promise.allSettled([
    ask(`/agents/${id}/lifecycle-requests`), ask(`/agent-definitions/${id}/requests`)]);
  const read = (answer) => (answer.status === 'fulfilled' && Array.isArray(answer.value?.requests) ? answer.value.requests : []);
  const problems = [[lifecycle, 'its lifecycle requests'], [definition, 'its definition requests']]
    .filter(([answer]) => answer.status !== 'fulfilled').map(([answer, what]) => `${what}: ${answer.reason?.message || answer.reason}`);
  return requestsSection(requestRows(read(lifecycle), read(definition)), problems);
}
