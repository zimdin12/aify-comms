// The Work Loop overview panels: what needs attention, what just happened, and the contract board.
//
// One subject, three panels, and the contract card they share. `contractCard` sits here rather than in a
// module of its own because both the board and the attention list render it and nothing else does — the
// ownership test used throughout this series is a count of DIRECT readers, not a guess at where a name
// sounds like it belongs.
//
// Extracted from app.js in v0.5.4 as a measured closure: eight declarations that need nothing from app.js,
// only sibling leaf modules imported downward. That became possible once `state` and `byId` were given
// owners of their own; before that every render group in app.js read at least one name app.js itself
// declared, and a module extracted from app.js cannot import those back without the upward import this
// series forbids — which here would also be a cycle.
//
// `renderContracts` — the LIST view beside this board — is deliberately not here. It reaches `apiBase`,
// which is evaluated at module load from `location` and `localStorage`, and pulling that in would make this
// module unimportable in Node and every module importing it too. Measured, that closure is 2,203 lines
// rather than the 63 the board alone costs. `apiBase` needs a ruling, not a script; see
// docs/APP_JS_APIBASE_PACKET.md.
//
// Every declaration is byte-identical to the one that stood in app.js; the only substitution is the added
// `export `, which the reconstruction proof strips before comparing. Leading comments stayed behind in
// app.js deliberately — `declarationSpan` returns the declaration alone, so a span that took its comments
// could not round-trip through the proof.


import { contractActionable, messageId, runTargetAgent } from './record-fields.mjs';
import { state } from './state.mjs';
import { renderStatusChip, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc, relTime, tsMs } from './util.js';

export function filtered(items, fields) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => fields.some((field) => String(item[field] || '').toLowerCase().includes(needle)));
}
export function contractCard(contract, { selectable = true } = {}) {
  const actionable = contractActionable(contract);
  const key = diagnosticKey('contract', contract.id);
  const checked = state.selectedDiagnosticIds.has(key) ? ' checked' : '';
  return `
    <article class="contract" data-kind="contract" data-id="${esc(contract.id)}">
      ${selectable ? `<input class="diagnostic-check" type="checkbox" data-diagnostic-select="${esc(contract.id)}" data-diagnostic-kind="contract"${checked} title="Select Work Loop item">` : ''}
      <div>
        <div class="item-title">
          <strong class="clip">${esc(contract.subject || contract.id)}</strong>
          ${renderStatusChip(contract.overdue ? 'failed' : contract.state || contract.status, statusWhyContext('contract', contract, contract.overdue ? 'failed' : contract.state || contract.status, { label: contract.state || contract.status }))}
        </div>
        <p class="preview">${esc(contract.preview || '')}</p>
        <div class="contract-meta">
          ${esc(contract.from)} → ${esc(contract.targetAgentId)} · ${esc(contract.type)}${relTime(contract.requestedAt) ? ` · ${relTime(contract.requestedAt)} old` : ''} · ${contract.lastReminderAt ? `last reminded ${relTime(contract.lastReminderAt)} ago` : 'not reminded'}
        </div>
      </div>
      <div class="contract-actions">
        <button class="ghost" data-run-inspector="${esc(contract.id)}" data-run-source="work">Inspect</button>
        ${actionable ? `<button class="ghost" data-remind-contract="${esc(contract.id)}">Remind</button><button class="ghost danger" data-close-contract="${esc(contract.id)}">Close</button>` : ''}
      </div>
    </article>`;
}
export function renderAttention() {
  const items = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])
    .filter((c) => c.overdue || c.state === 'working' || c.state === 'queued')
    .slice(0, 8);
  const host = byId('attention-list');
  if (!host) return; // never let a missing node throw out of the unconditional renderAll loop
  // WS-G: when clear, collapse to a slim one-liner instead of a tall empty card.
  host.classList.toggle('is-clear', items.length === 0);
  host.innerHTML = items.length
    ? items.map((contract) => contractCard(contract, { selectable: false })).join('')
    : '<p class="attention-clear">✓ Work Loop clear — no overdue or in-flight replies.</p>';
}
export function diagnosticKey(kind, id) {
  return `${kind}:${id}`;
}
export function activityItems() {
  const runItems = state.runs.slice(0, 8).map((run) => ({
    kind: 'run',
    id: run.id,
    title: run.subject || run.id,
    meta: `${runTargetAgent(run) || 'unassigned'} · ${relTime(run.startedAt || run.requestedAt)} ago`,
    status: run.status || 'unknown',
    at: tsMs(run.startedAt || run.requestedAt) || 0,
    source: run,
  }));
  const messageItems = state.messages.slice(0, 8).map((message) => ({
    kind: 'message',
    id: messageId(message),
    title: message.subject || message.body || '(no subject)',
    meta: `${message.from || 'unknown'} → ${message.to || message.targetAgentId || 'dashboard'} · ${relTime(message.createdAt || message.timestamp || message.time)} ago`,
    status: message.read ? 'completed' : 'queued',
    at: tsMs(message.createdAt || message.timestamp || message.time) || 0,
    source: message,
  }));
  const contractItems = state.contracts.slice(0, 8).map((contract) => ({
    kind: 'contract',
    id: contract.id,
    title: contract.subject || contract.id,
    meta: `${contract.targetAgentId || 'unknown'} · ${relTime(contract.requestedAt)} old`,
    status: contract.overdue ? 'failed' : contract.state || contract.status || 'unknown',
    at: tsMs(contract.lastReminderAt || contract.requestedAt) || 0,
    source: contract,
  }));
  return [...runItems, ...messageItems, ...contractItems]
    .sort((a, b) => b.at - a.at)
    .slice(0, 10);
}
export function renderActivityFeed() {
  const feed = byId('activity-feed');
  if (!feed) return;
  const items = activityItems();
  feed.innerHTML = items.length ? items.map((item) => {
    const context = item.kind === 'run'
      ? statusWhyContext('run', item.source, item.status)
      : item.kind === 'contract'
        ? statusWhyContext('contract', item.source, item.status, { label: item.source.state || item.source.status || item.status })
        : statusWhyContext('message', item.source, item.status, { label: item.source.type || item.status, why: `Message from ${item.source.from || 'unknown'} to ${item.source.to || item.source.targetAgentId || 'dashboard'}.` });
    const inspectAttrs = item.kind === 'run' || item.kind === 'contract'
      ? `data-run-inspector="${esc(item.id)}" data-run-source="activity"`
      : `data-kind="message" data-id="${esc(item.id)}"`;
    return `
      <article class="activity-item" ${inspectAttrs}>
        <div class="item-title">
          <strong class="clip">${esc(item.title)}</strong>
          ${renderStatusChip(item.status, context)}
        </div>
        <p class="preview">${esc(item.meta)}</p>
      </article>`;
  }).join('') : '<div class="activity-item"><strong>No recent activity loaded</strong><p class="preview">Activity appears after messages, runs, or Work Loop updates.</p></div>';
}
export const CONTRACT_BOARD_COLUMNS = [
  { key: 'overdue',  label: 'Overdue',  always: true,  match: (c) => !!c.overdue },
  { key: 'working',  label: 'Working',  always: true,  match: (c) => c.state === 'working' },
  { key: 'queued',   label: 'Queued',   always: true,  match: (c) => c.state === 'queued' },
  { key: 'awaiting', label: 'Awaiting', always: true,  match: (c) => ['sent', 'seen', 'missing_reply'].includes(c.state) },
  { key: 'answered', label: 'Answered', always: false, match: (c) => ['answered', 'closed'].includes(c.state) },
  { key: 'failed',   label: 'Failed',   always: false, match: (c) => c.state === 'failed' },
];
export function renderContractBoard(contracts) {
  const buckets = new Map(CONTRACT_BOARD_COLUMNS.map((col) => [col.key, []]));
  const other = [];
  for (const contract of contracts) {
    const col = CONTRACT_BOARD_COLUMNS.find((c) => c.match(contract));
    (col ? buckets.get(col.key) : other).push(contract);
  }
  const columns = CONTRACT_BOARD_COLUMNS
    .filter((col) => col.always || buckets.get(col.key).length)
    .map((col) => {
      const cards = buckets.get(col.key);
      const body = cards.length
        ? cards.map((c) => contractCard(c)).join('')
        : '<p class="board-col-empty">Clear</p>';
      return `<div class="contract-board-col c-${col.key}">
        <div class="board-col-head"><span class="board-col-label">${esc(col.label)}</span><span class="board-col-count">${cards.length}</span></div>
        <div class="board-col-body">${body}</div>
      </div>`;
    });
  // Anything with an unrecognized state (forward-compat) gets its own trailing column
  // rather than silently vanishing from the board.
  if (other.length) {
    columns.push(`<div class="contract-board-col c-other">
      <div class="board-col-head"><span class="board-col-label">Other</span><span class="board-col-count">${other.length}</span></div>
      <div class="board-col-body">${other.map((c) => contractCard(c)).join('')}</div>
    </div>`);
  }
  return `<div class="contract-board">${columns.join('')}</div>`;
}

// The two diagnostics-panel view controls, moved out of app.js's delegated click handler in v0.5.4.
// Both act on the panels this module renders: one switches the work grid's layout, the other jumps a
// filter select to the bucket a diagnostic card names. `byId` was already imported for the same reason.
export function applyWorkView(workView) {
  const v = workView.dataset.workView;
  const grid = document.querySelector('.diagnostics-grid');
  if (grid) grid.setAttribute('data-work-view', v);
  document.querySelectorAll('button[data-work-view]').forEach((b) => { const on = b.dataset.workView === v; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  try { localStorage.setItem('aifyWorkView', v); } catch { /* private mode */ }
}

export function jumpFromDiagnostic(diagJump) {
  const v = diagJump.dataset.diagJump || '';
  if (v.startsWith('run:')) {
    const sel = byId('run-status-filter'); if (sel) { sel.value = v.slice(4); sel.dispatchEvent(new Event('change', { bubbles: true })); }
  } else {
    const sel = byId('contract-state'); if (sel) { sel.value = v; sel.dispatchEvent(new Event('change', { bubbles: true })); }
  }
}

// The diagnostics bulk-selection checkbox, moved out of app.js's delegated click handler in v0.5.4. It
// keys on `diagnosticKey`, which is this module's own — the composite key is what lets runs and
// contracts share one selection Set without colliding on id.
export function toggleDiagnosticSelection(diagnosticSelect, renderDiagnosticsBulkToolbar) {
  const key = diagnosticKey(diagnosticSelect.dataset.diagnosticKind || 'run', diagnosticSelect.dataset.diagnosticSelect);
  if (diagnosticSelect.checked) state.selectedDiagnosticIds.add(key);
  else state.selectedDiagnosticIds.delete(key);
  renderDiagnosticsBulkToolbar();
}

// The Work Loop list/board layout toggle, moved out of app.js's delegated click handler in v0.5.4.
// It sits beside `applyWorkView` — the same shape for the other grid — and `renderContracts` is
// injected because it stays in app.js.
export function applyContractView(contractView, renderContracts) {
  const v = contractView.dataset.contractView === 'board' ? 'board' : 'list';
  state.contractView = v;
  try { localStorage.setItem('aifyContractView', v); } catch { /* private mode */ }
  renderContracts();
}

// Drops selections for records that no longer exist, moved out of app.js in v0.5.4. It keys on this
// module's own `diagnosticKey`, and without it a bulk action can address a run the operator can no
// longer see.
export function pruneDiagnosticSelection() {
  const live = new Set([
    ...state.contracts.map((contract) => diagnosticKey('contract', contract.id)),
    ...state.runs.map((run) => diagnosticKey('run', run.id)),
  ]);
  for (const key of [...state.selectedDiagnosticIds]) {
    if (!live.has(key)) state.selectedDiagnosticIds.delete(key);
  }
}

// The single-item form of `filtered` above, moved out of app.js in v0.5.4. The two are now visibly the
// SAME RULE written twice — one over a list, one over an item. Left as a pair rather than collapsed:
// `filtered` returns items and this returns a boolean, so unifying them changes both call sites for no
// behavioural gain. What the duplication needs is an AGREEMENT TEST, and there is one beside them.
export function matchesGlobalFilter(item, fields) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return true;
  return fields.some((field) => String(item[field] || '').toLowerCase().includes(needle));
}
