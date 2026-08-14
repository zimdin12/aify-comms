// Run and diagnostics data helpers, moved out of app.js in v0.5.4.
//
// Four small functions that were unreachable by any test while they lived in app.js. Three of them decide
// something a caller cannot see went wrong: an id that resolves to nothing, a query string missing a
// filter, an option list that silently stops updating.

import { api } from './api-client.mjs';
import { messageId } from './record-fields.mjs';
import { state } from './state.mjs';
import { byId } from './ui.js';
import { esc } from './util.js';

export function runQueryPath(status = state.runStatusFilter) {
  const params = new URLSearchParams({ limit: '80' });
  if (status) params.set('status', status);
  return `/dispatch/runs?${params.toString()}`;
}

export function runSourceMessage(run) {
  const id = String(run?.messageId || run?.message_id || state.inspector.sourceMessageId || '').trim();
  if (!id) return null;
  return state.messages.find((message) => messageId(message) === id) || null;
}

export function syncRunFilterOptions(id, values, current) {
  const sel = byId(id);
  if (!sel) return;
  const opts = ['', ...[...new Set(values.filter(Boolean))].sort()];
  const sig = opts.join('|');
  if (sel.dataset.optsSig === sig) { sel.value = current || ''; return; }
  sel.dataset.optsSig = sig;
  sel.innerHTML = opts.map((v) => `<option value="${esc(v)}"${v === (current || '') ? ' selected' : ''}>${v ? esc(v) : 'Any'}</option>`).join('');
}

export async function patchRun(runId, payload) {
  return api(`/dispatch/runs/${encodeURIComponent(runId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

// The run-inspector data loaders, moved out of app.js in v0.5.4. They join the query helpers above
// because they address the same endpoints; the page-size cap travels with them, since it is enforced
// in two places and splitting them would let the two drift.
export const RUN_INSPECTOR_EVENT_LIMIT = 50;
export async function loadRunDetails(runId) {
  const result = await api(`/dispatch/runs/${encodeURIComponent(runId)}`);
  return result.run || result;
}
export async function loadRunEvents(runId, { before = '', order = state.inspector.eventOrder || 'desc', limit = RUN_INSPECTOR_EVENT_LIMIT } = {}) {
  const params = new URLSearchParams();
  params.set('limit', String(Math.min(limit, RUN_INSPECTOR_EVENT_LIMIT)));
  params.set('order', order === 'asc' ? 'asc' : 'desc');
  if (before) params.set('before', before);
  return api(`/dispatch/runs/${encodeURIComponent(runId)}/events?${params.toString()}`);
}
