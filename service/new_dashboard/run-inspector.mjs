// The run inspector: the Runs list, the drawer, and every control an operator can press inside it.
//
// A "run" is one dispatched unit of work, and this is the only place an operator can see what happened
// to one — its timeline, its target agent, and the four controls (steer, interrupt, retry, follow-up)
// that act on a run already in flight. All of it lived in app.js, so none of it could be imported and
// none of it was tested.
//
// The six injected names are app.js's render orchestrator and the two generic drawer openers. They are
// injected rather than imported because each reaches `refresh`; importing one would pull the whole
// render web in here and undo the extraction. Init is explicit and follows realtime-socket.mjs: these
// functions are called from a dozen places in app.js, so threading a bag through every call site would
// be noise, and a bag that some call sites forgot would be worse than one that cannot be forgotten.

import { api } from './api-client.mjs';
import { sendRunFollowup } from './message-transport.mjs';
// `messageId` is called in the "Open in thread" button below and was never imported — a
// ReferenceError on a NORMAL render path (any run with a source message), not an error branch.
// `node --check` parses it; nothing renders this panel in a test. Found by the bridge's
// missing-sibling-import gate the day it was extended to the dashboard.
import { messageId, runPendingControlCount, runTargetAgent } from './record-fields.mjs';
import { renderRunEvent } from './run-event.mjs';
import { RUN_INSPECTOR_EVENT_LIMIT, loadRunDetails, loadRunEvents, patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from './run-helpers.mjs';
import { renderRunInspectorControls, runInspectorCapabilities } from './run-inspector-controls.mjs';
import { runFrom } from './session-activity.mjs';
import { state } from './state.mjs';
import { renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';
import { byId, toast, uiConfirm, uiPrompt } from './ui.js';
import { esc, relTime } from './util.js';
import { diagnosticKey, matchesGlobalFilter } from './work-loop-panels.mjs';

let closeInspector = () => {};
let evaluateFlowGates = () => {};
let openInspector = () => {};
let openRunConsole = () => {};
let refresh = async () => {};
let renderDiagnosticsBulkToolbar = () => {};

/** Supply the app.js-side dependencies. Throws on a partial bag rather than accepting no-ops. */
export function initRunInspector(deps) {
  const REQUIRED = ['closeInspector', 'evaluateFlowGates', 'openInspector', 'openRunConsole', 'refresh', 'renderDiagnosticsBulkToolbar'];
  const missing = REQUIRED.filter((k) => deps == null || typeof deps[k] !== 'function');
  if (missing.length) throw new TypeError(`initRunInspector requires ${missing.join(', ')}`);
  ({ closeInspector, evaluateFlowGates, openInspector, openRunConsole, refresh, renderDiagnosticsBulkToolbar } = deps);
}


export async function loadRunsForStatus(status = state.runStatusFilter, render = true) {
  state.runStatusFilter = status || '';
  const runs = await api(runQueryPath(state.runStatusFilter));
  state.runs = runs.runs || [];
  if (render) {
    renderRuns();
  }
  return state.runs;
}

const runTo = (r) => String(r.targetAgentId || r.target_agent || r.to || '');
const runRuntime = (r) => String(r.runtime || r.requestedRuntime || r.requested_runtime || '');

export function renderRuns() {
  // Populate from/to/runtime dropdowns from the loaded set (WS-H).
  syncRunFilterOptions('run-from-filter', state.runs.map(runFrom), state.runFromFilter);
  syncRunFilterOptions('run-to-filter', state.runs.map(runTo), state.runToFilter);
  syncRunFilterOptions('run-runtime-filter', state.runs.map(runRuntime), state.runRuntimeFilter);
  const search = String(state.runSearch || '').trim().toLowerCase();
  const runs = state.runs.filter((r) => {
    if (state.runFromFilter && runFrom(r) !== state.runFromFilter) return false;
    if (state.runToFilter && runTo(r) !== state.runToFilter) return false;
    if (state.runRuntimeFilter && runRuntime(r) !== state.runRuntimeFilter) return false;
    if (search) {
      const hay = [r.id, r.subject, r.summary, r.error, runFrom(r), runTo(r), r.mergedFromAgents].join(' ').toLowerCase();
      if (!hay.includes(search)) return false;
    }
    // also honor the top-bar global Find
    return matchesGlobalFilter(r, ['id', 'subject', 'targetAgentId', 'from', 'summary']);
  }).slice(0, 80);
  const note = byId('run-result-note');
  if (note) {
    const status = state.runStatusFilter ? `${state.runStatusFilter} ` : '';
    // WHICH FILTERS REACH THE SERVER, said only when it matters. Status is in the query string;
    // From, To, runtime and search are applied here, over the rows already fetched -- and the three
    // dropdowns are POPULATED from those same rows, so an agent whose last run fell off the page
    // cannot even be selected. Measured on the live database 2026-08-29: a limit=80 page reached back
    // to 26 August and offered one distinct sender.
    const scope = state.runsTruncated
      ? ' Older runs are not loaded: From, To, runtime and search cover only these, and only Status re-queries.'
      : '';
    note.textContent = `Showing ${runs.length} most recent matching ${status}run${runs.length === 1 ? '' : 's'}.${scope}`;
  }
  byId('run-list').innerHTML = runs.map((run) => `
    <article class="run-row" data-kind="run" data-id="${esc(run.id)}">
      <input class="diagnostic-check" type="checkbox" data-diagnostic-select="${esc(run.id)}" data-diagnostic-kind="run"${state.selectedDiagnosticIds.has(diagnosticKey('run', run.id)) ? ' checked' : ''} title="Select run">
      ${renderStatusChip(run.status, statusWhyContext('run', run, run.status))}
      <span>${esc(run.targetAgentId || run.target_agent || '')}</span>
      <div><strong class="clip">${esc(run.subject || run.id)}</strong><p class="preview">${esc(run.summary || run.error || '')}</p></div>
      <div class="run-actions">
        <button class="ghost" data-run-inspector="${esc(run.id)}" data-run-source="runs">Inspect</button>
        ${['claimed', 'running'].includes(resolveStatus(run.status).kind) ? `<button class="ghost" data-steer-run="${esc(run.id)}">Steer</button>` : ''}
      </div>
    </article>`).join('') || (state.runsTruncated
    // NOT "adjust the filters" when four of the five cannot reach further than the loaded page. That
    // sentence sends an operator round a loop that always ends where it started -- the same defect a
    // reviewer caught in the sessions note the same day.
    ? '<div class="empty-state"><span class="empty-icon">🔎</span><strong>None on this page</strong><p>None of the loaded runs match. Older runs are not loaded — only the Status filter re-queries the server.</p></div>'
    : '<div class="empty-state"><span class="empty-icon">📨</span><strong>No dispatch runs</strong><p>Runs appear here when an agent sends or receives work. Adjust the filters above if you expected to see some.</p></div>');
  renderDiagnosticsBulkToolbar();
}

export function renderRunInspector() {
  const run = state.inspector.run;
  if (!run) {
    byId('inspector-content').innerHTML = '<div class="run-inspector-loading">Loading run inspector...</div>';
    return;
  }
  const statusContext = runStatusContext(run);
  const sourceMessage = runSourceMessage(run);
  const sourceSubject = sourceMessage?.subject || run.subject || '(no subject)';
  const sourceBody = sourceMessage?.body || sourceMessage?.preview || run.body || run.summary || '';
  const events = state.inspector.events || [];
  const startedAt = run.startedAt || run.claimedAt || run.requestedAt;
  const duration = startedAt ? `${relTime(startedAt)} elapsed` : 'duration unknown';
  byId('inspector-content').innerHTML = `
    <section class="run-inspector">
      <header class="run-inspector-header">
        <div>
          <small>Run</small>
          <h3 class="clip">${esc(run.id)}</h3>
        </div>
        <button class="ghost" data-copy-run-id="${esc(run.id)}">Copy ID</button>
        <span>${esc(runTargetAgent(run) || 'unassigned')}</span>
        <span class="session-runtime-badge">${esc(run.runtime || run.requestedRuntime || 'runtime')}</span>
        ${renderStatusChip(run.status, statusContext)}
      </header>
      <div class="run-why-line">
        <span>${esc(run.from || 'unknown')} triggered</span>
        <span>${esc(startedAt || 'not started')}</span>
        <span>${esc(duration)}</span>
        ${statusContext.blockerReason ? `<span>${esc(statusContext.blockerReason)}</span>` : ''}
      </div>
      <section class="run-source-context">
        <div>
          <strong class="clip">${esc(sourceSubject)}</strong>
          <p class="preview">${esc(sourceBody).slice(0, 180)}</p>
        </div>
        ${sourceMessage ? `<button class="ghost" data-open-thread-message="${esc(messageId(sourceMessage))}">Open in thread</button>` : ''}
      </section>
      <div class="section-head">
        <h3>Event timeline</h3>
        <button class="ghost" id="run-inspector-order-toggle">${state.inspector.eventOrder === 'desc' ? 'Newest first' : 'Oldest first'}</button>
      </div>
      <div id="run-inspector-events" class="run-event-list">
        ${events.length ? events.map(renderRunEvent).join('') : '<div class="em">No events for this run yet.</div>'}
      </div>
      <div class="run-inspector-footer">
        <span>Showing ${events.length} most recent${state.inspector.hasMore ? ' — load more' : ''}</span>
        ${state.inspector.hasMore ? '<button class="ghost" id="run-inspector-load-more">Load more</button>' : ''}
      </div>
      ${renderRunInspectorControls(run)}
    </section>`;
  evaluateFlowGates();
}

export async function openRunInspector({ runId, source = 'programmatic', sourceMessageId = '' } = {}) {
  if (!runId) return;
  state.inspector = { kind: 'run', runId: String(runId), source, run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId };
  openInspector({ kind: 'run', runId, source });
  renderRunInspector();
  try {
    const [run, eventPage] = await Promise.all([
      loadRunDetails(runId),
      loadRunEvents(runId, { limit: RUN_INSPECTOR_EVENT_LIMIT }),
    ]);
    // Still-current check (review finding #7): clicking run B while run A's fetch is in
    // flight let A's slower response overwrite B's inspector. Bail if superseded.
    if (state.inspector?.kind !== 'run' || state.inspector.runId !== String(runId)) return;
    state.inspector.run = run;
    state.inspector.events = eventPage.events || [];
    state.inspector.hasMore = Boolean(eventPage.hasMore);
    renderRunInspector();
  } catch (error) {
    byId('inspector-content').innerHTML = `<pre>${esc(JSON.stringify({ error: error.message }, null, 2))}</pre>`;
  }
}

export async function requestRunControl(runId) {
  const body = await uiPrompt('Steer this active run');
  if (!body || !body.trim()) return;
  try {
    await api(`/dispatch/runs/${encodeURIComponent(runId)}/control`, {
      method: 'POST',
      body: JSON.stringify({ from_agent: 'dashboard', action: 'steer', body }),
    });
    await openRunInspector({ runId, source: 'runs' });
  } catch (err) { toast(`Steer failed: ${err?.message || err}`, 'error'); }
}

export async function handleRunInspectorControl(action) {
  const run = state.inspector.run;
  if (!run?.id || !action) return;
  const capabilities = runInspectorCapabilities(run);
  const enabled = {
    steer: capabilities.steer,
    interrupt: capabilities.interrupt,
    'queue-after': capabilities.queueAfter,
    retry: capabilities.retry,
    close: capabilities.close,
    'open-console': capabilities.openConsole,
  };
  if (!enabled[action]) return;
  if (action === 'open-console') {
    openRunConsole(run);
    return;
  }
  try {
  if (action === 'steer') {
    const body = await uiPrompt('Steer this active run');
    if (!body || !body.trim()) return;
    await api(`/dispatch/runs/${encodeURIComponent(run.id)}/control`, {
      method: 'POST',
      body: JSON.stringify({ from_agent: 'dashboard', action: 'steer', body }),
    });
  } else if (action === 'interrupt') {
    if (!await uiConfirm(`Interrupt this run? This will kill 1 active run + ${runPendingControlCount(run)} pending controls.`)) return;
    await api(`/dispatch/runs/${encodeURIComponent(run.id)}/control`, {
      method: 'POST',
      body: JSON.stringify({ from_agent: 'dashboard', action: 'interrupt', body: 'Interrupted from Dashboard Next run inspector.' }),
    });
  } else if (action === 'queue-after') {
    const body = await uiPrompt('Queue a follow-up after this run');
    if (!body || !body.trim()) return;
    await sendRunFollowup(run, { body });
  } else if (action === 'retry') {
    const target = runTargetAgent(run);
    if (!await uiConfirm(`Retry this run? A new follow-up request will be sent to ${target || 'the target'} (queued if busy). It does not interrupt anything currently running.`)) return;
    await sendRunFollowup(run, { retry: true });
  } else if (action === 'close') {
    if (!await uiConfirm('Close this run as operator-reviewed?')) return;
    await patchRun(run.id, {
      status: 'completed',
      requireReply: false,
      summary: 'Closed from run inspector by dashboard operator.',
      appendEvent: 'Closed from run inspector by dashboard operator.',
      eventType: 'operator_closed',
    });
  }
  await refresh();
  await openRunInspector({ runId: run.id, source: state.inspector.source || 'control', sourceMessageId: state.inspector.sourceMessageId || '' });
  } catch (err) { toast(`Run ${action} failed: ${err?.message || err}`, 'error'); }
}

export async function loadMoreRunEvents() {
  if (!state.inspector.runId || state.inspector.loadingMore) return;
  state.inspector.loadingMore = true;
  const last = state.inspector.events[state.inspector.events.length - 1];
  try {
    const page = await loadRunEvents(state.inspector.runId, {
      before: last?.id || '',
      order: state.inspector.eventOrder,
      limit: RUN_INSPECTOR_EVENT_LIMIT,
    });
    state.inspector.events = [...state.inspector.events, ...(page.events || [])];
    state.inspector.hasMore = Boolean(page.hasMore);
    renderRunInspector();
  } finally {
    state.inspector.loadingMore = false;
  }
}

export async function toggleRunEventOrder() {
  if (!state.inspector.runId) return;
  state.inspector.eventOrder = state.inspector.eventOrder === 'desc' ? 'asc' : 'desc';
  state.inspector.events = [];
  const page = await loadRunEvents(state.inspector.runId, { order: state.inspector.eventOrder, limit: RUN_INSPECTOR_EVENT_LIMIT });
  state.inspector.events = page.events || [];
  state.inspector.hasMore = Boolean(page.hasMore);
  renderRunInspector();
}
