// The Work Loop: contracts, diagnostics and the maintenance actions that resolve them.
//
// A "diagnostic" is a run the dashboard has flagged as needing attention, and the BULK action is what
// an operator uses to clear a screenful of them at once. That makes it the most dangerous control on
// the page — it is the only place a single click acts on a set the operator did not enumerate — so its
// selection handling is what the tests here are mostly about.
//
// One injected name: `refresh`. Everything else is already a sibling's export.

import { api } from './api-client.mjs';
import { contractCategory } from './record-fields.mjs';
import { patchRun } from './run-helpers.mjs';
import { openRunInspector, renderRuns } from './run-inspector.mjs';
import { state } from './state.mjs';
import { selectedDiagnostics } from './summary-tiles.mjs';
import { byId, toast, uiConfirm } from './ui.js';
import { MAINTENANCE_ACTIONS, contractCard, filtered, pruneDiagnosticSelection, renderContractBoard } from './work-loop-panels.mjs';

let refresh = async () => {};

/** Supply app.js's poll. Throws rather than silently accepting a no-op. */
export function initWorkLoopActions(deps) {
  if (typeof deps?.refresh !== 'function') throw new TypeError('initWorkLoopActions requires refresh');
  ({ refresh } = deps);
}


// The base refresh fetches only OPEN contracts, so the State dropdown's terminal options
// (Answered/Failed/Missing reply/Seen/Sent/Closed) had nothing to match. Reload from the server
// with the matching scope on change so every option works. (2026-06-29 fix.)
export async function loadContractsForState(stateVal, render = true) {
  const v = String(stateVal || '').trim();
  let qs = '/contracts?limit=120';
  if (v === 'all') qs = '/contracts?includeClosed=true&limit=300';
  else if (v && v !== 'open') qs = `/contracts?state=${encodeURIComponent(v)}&limit=200`;
  try { const res = await api(qs); state.contracts = res.contracts || []; } catch (err) { toast(`Load contracts failed: ${err?.message || err}`, 'error'); }
  if (render) renderContracts();
}

export async function runMaintenance(action) {
  const def = MAINTENANCE_ACTIONS[action];
  if (!def) return;
  if (!(await uiConfirm(`${def.label}? This is safe to run while agents are working.`, { confirmLabel: def.label }))) return;
  try {
    const res = await api(def.path, { method: 'POST' });
    const n = (res && (res.repaired ?? res.mirrored ?? res.count ?? res.updated ?? res.fixed));
    toast(`${def.label}: ${n != null ? `${n} fixed` : 'done'}`, 'ok');
    refresh();
  } catch (err) {
    toast(`${def.label} failed: ${err && err.message ? err.message : err}`, 'error');
  }
}

export function renderDiagnosticsBulkToolbar() {
  const toolbar = byId('diagnostics-bulk-toolbar');
  if (!toolbar) return;
  pruneDiagnosticSelection();
  const selected = selectedDiagnostics();
  toolbar.hidden = selected.length === 0;
  if (!selected.length) {
    toolbar.innerHTML = '';
    return;
  }
  const contracts = selected.filter((item) => item.kind === 'contract').length;
  const runs = selected.filter((item) => item.kind === 'run').length;
  toolbar.innerHTML = `
    <span>${selected.length} selected · ${contracts} work · ${runs} runs</span>
    <button class="ghost" data-diagnostic-action="remind">Remind work</button>
    <button class="ghost danger" data-diagnostic-action="close">Close selected</button>
    <button class="ghost" data-diagnostic-action="inspect">Inspect first</button>
    <button class="ghost" data-diagnostic-action="clear">Clear</button>`;
}

export function renderContracts() {
  const selected = byId('contract-state')?.value || 'open';
  const category = byId('contract-category')?.value || '';
  const contracts = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])
    .filter((contract) => selected === 'all' ? true
      : selected === 'open' ? ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state)
      : contract.state === selected)
    .filter((contract) => !category || contractCategory(contract) === category);
  const host = byId('contract-list');
  // The missing-host guard every neighbouring renderer has — `renderUsagePools`,
  // `renderDiagnosticsBulkToolbar`, `renderSessionConsole`. This one dereferenced `host` directly, and
  // it is called from the render orchestrator on EVERY poll, so the day `#contract-list` is renamed or
  // dropped from a page the whole dashboard stops re-rendering rather than just this panel.
  //
  // The bulk toolbar is still rendered on the way out: it lives in its own container and its selection
  // is pruned there, so skipping it would leave a stale count beside a panel that never drew.
  if (!host) { renderDiagnosticsBulkToolbar(); return; }
  host.classList.toggle('is-board', state.contractView === 'board');
  if (!contracts.length) {
    host.innerHTML = '<div class="empty-state"><span class="empty-icon">✓</span><strong>No contracts match</strong><p>No reply obligations in this filter.</p></div>';
  } else if (state.contractView === 'board') {
    host.innerHTML = renderContractBoard(contracts);
  } else {
    host.innerHTML = contracts.map(contractCard).join('');
  }
  // Keep the toggle buttons in sync (also on first paint / cross-tab restore).
  document.querySelectorAll('button[data-contract-view]').forEach((b) => {
    const on = b.dataset.contractView === state.contractView;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  renderDiagnosticsBulkToolbar();
}

export async function closeWorkContract(runId, confirmAction = true, refreshAfter = true) {
  if (confirmAction && !await uiConfirm('Close this Work Loop contract as operator-reviewed?')) return;
  await patchRun(runId, {
    status: 'completed',
    requireReply: false,
    summary: 'Closed from Work Loop by dashboard operator.',
    appendEvent: 'Closed from Work Loop by dashboard operator.',
    eventType: 'operator_closed',
  });
  if (refreshAfter) await refresh();
}

export async function remindWorkContract(runId, refreshAfter = true) {
  // The falsy-id guard every neighbour has — `closeWorkContract`, `stopAgentWorker`, `removeAgent`,
  // `deleteSessionById`, `requestSessionControl`. This one did not, and posted `?runId=` for the
  // server to reject. Both callers happen to supply an id today (the bulk path filters to contracts
  // that have one; the click handler reads an attribute that is always written), so it was latent —
  // but "reachable only through the paths we happen to have" is the state a guard exists to remove.
  if (!runId) return;
  await api(`/contracts/reminders/run?runId=${encodeURIComponent(runId)}`, { method: 'POST' });
  if (refreshAfter) await refresh();
}

export async function requestBulkDiagnosticAction(action) {
  const selected = selectedDiagnostics();
  if (!selected.length || !action) return;
  if (action === 'clear') {
    state.selectedDiagnosticIds.clear();
    renderContracts();
    renderRuns();
    return;
  }
  if (action === 'inspect') {
    const first = selected[0];
    if (first.kind === 'run') await openRunInspector({ runId: first.id, source: 'diagnostics-bulk' });
    else if (first.kind === 'contract') await openRunInspector({ runId: first.id, source: 'work' });
    return;
  }
  if (action === 'remind') {
    const contracts = selected.filter((entry) => entry.kind === 'contract');
    if (!contracts.length) {
      // Only reply-contracts can be reminded; don't silently drop a runs-only selection.
      toast('No reply-contract items in the selection to remind.', 'warn');
      return;
    }
    for (const item of contracts) {
      await remindWorkContract(item.id, false);
    }
    toast(`Reminder sent for ${contracts.length} contract${contracts.length === 1 ? '' : 's'}.`, 'ok');
    state.selectedDiagnosticIds.clear();
    await refresh();
    return;
  }
  if (action === 'close') {
    if (!await uiConfirm(`Close ${selected.length} selected diagnostics item${selected.length === 1 ? '' : 's'} as operator-reviewed?`)) return;
    for (const item of selected) {
      if (item.kind === 'contract') {
        await closeWorkContract(item.id, false, false);
      } else if (item.kind === 'run') {
        await patchRun(item.id, {
          status: 'completed',
          requireReply: false,
          summary: 'Closed from Diagnostics by dashboard operator.',
          appendEvent: 'Closed from Diagnostics by dashboard operator.',
          eventType: 'operator_closed',
        });
      }
    }
    state.selectedDiagnosticIds.clear();
    await refresh();
  }
}
