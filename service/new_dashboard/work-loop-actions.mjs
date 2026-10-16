// The Work Loop: contracts, diagnostics and the maintenance actions that resolve them.
//
// A "diagnostic" is a run the dashboard has flagged as needing attention, and the BULK action is what
// an operator uses to clear a screenful of them at once. That makes it the most dangerous control on
// the page — it is the only place a single click acts on a set the operator did not enumerate — so its
// selection handling is what the tests here are mostly about.
//
// One injected name: `refresh`. Everything else is already a sibling's export.

import { api } from './api-client.mjs';
import { sendOutcomeIsUnknown } from './message-transport.mjs';
import { noteSliceFailure } from './refresh-status.mjs';
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
  // `truncated` is kept, not dropped. The endpoint scans a bounded superset because a contract's
  // state is derived in Python and the SQL filter standing in for it is deliberately wider; when
  // that scan hits its ceiling the list is a page, not the whole answer. A truncated list that
  // does not admit it is truncated reads as "that is everything" -- which is exactly the defect
  // this endpoint just had, in its summary.
  //
  // AN ANSWER FOR A STATE NO LONGER SELECTED IS DROPPED: two quick changes could otherwise land out
  // of order and leave one filter's rows under the other. And the POLL PATH (render=false) does not
  // toast -- it re-applies the filter every cycle, so a failing fetch toasted on every one. It
  // rethrows instead, so the cycle records the slice as failed and retries it.
  const selected = () => {
    const el = typeof document === 'undefined' ? null : byId('contract-state');
    return el ? String(el.value || '').trim() : v;
  };
  try {
    const res = await api(qs);
    if (selected() !== v) return;
    state.contracts = res.contracts || [];
    state.contractsTruncated = Boolean(res.truncated);
  } catch (err) {
    if (!render) throw err;
    if (selected() !== v) return;
    noteSliceFailure('contract filter');
    toast(`Load contracts failed: ${err?.message || err}`, 'error');
  }
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
  // Said on screen rather than left to the reader to infer: under a capped scan, "no contracts
  // match" and "none exist" are different facts that look identical.
  const capped = state.contractsTruncated
    ? '<div class="mb mb-warn">Showing a partial scan — more may match than are listed.</div>'
    : '';
  if (!contracts.length) {
    host.innerHTML = capped + '<div class="empty-state"><span class="empty-icon">✓</span><strong>No contracts match</strong><p>No reply obligations in this filter.</p></div>';
  } else if (state.contractView === 'board') {
    host.innerHTML = capped + renderContractBoard(contracts);
  } else {
    host.innerHTML = capped + contracts.map(contractCard).join('');
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
  let answer;
  try {
    answer = await patchRun(runId, {
      status: 'completed',
      requireReply: false,
      summary: 'Closed from Work Loop by dashboard operator.',
      appendEvent: 'Closed from Work Loop by dashboard operator.',
      eventType: 'operator_closed',
    });
  } catch (err) {
    // The bulk action counts each item's failure itself; only the single Close reports here, where an
    // unhandled rejection used to leave the operator believing it had closed.
    if (!confirmAction) throw err;
    const why = err?.message || err;
    toast(sendOutcomeIsUnknown(err)
      ? `The close may have gone through (${why}). Refresh to check before closing it again.`
      : `Close failed: ${why}`, 'error');
    return null;
  }
  if (answer?.alreadyTerminal && confirmAction) toast('That run had already finished; its result was kept.', 'warn');
  if (refreshAfter) {
    // The service acknowledged the close, so a failed refresh is only a stale list, not a failed close.
    try { await refresh(); } catch (err) {
      if (!confirmAction) throw err;
      toast(`Closed, but the list did not refresh: ${err?.message || err}`, 'warn');
    }
  }
  return answer;
}

export async function remindWorkContract(runId, refreshAfter = true) {
  // The falsy-id guard every neighbour has — `closeWorkContract`, `stopAgentWorker`, `removeAgent`,
  // `deleteSessionById`, `requestSessionControl`. This one did not, and posted `?runId=` for the
  // server to reject. Both callers happen to supply an id today (the bulk path filters to contracts
  // that have one; the click handler reads an attribute that is always written), so it was latent —
  // but "reachable only through the paths we happen to have" is the state a guard exists to remove.
  if (!runId) return;
  const body = await api(`/contracts/reminders/run?runId=${encodeURIComponent(runId)}`, { method: 'POST' });
  if (refreshAfter) await refresh();
  // A 200 is not a reminder: the service skips a busy or gone target and says so in `skipped`.
  // Counted as reminded only when it is listed as reminded, so a missing answer is not a success.
  const skip = (body?.skipped || []).find((s) => s?.runId === runId);
  if (skip) throw new Error(skip.reason || 'skipped by the service');
  if (!(body?.reminded || []).some((r) => r?.runId === runId)) throw new Error('the service did not report it as reminded');
}

/** The single Remind button: the same call, with a toast saying what the service did. */
export async function remindOneContract(runId) {
  try {
    await remindWorkContract(runId);
    toast('Reminder sent.', 'ok');
  } catch (err) {
    toast(`Not reminded: ${err?.message || err}`, 'warn');
  }
}

/**
 * Try `act` on every item, whatever the others did (v0.7.7 D5: the first refusal used to end the
 * loop). Succeeded items leave the selection; failed ones stay selected for a retry. One toast counts both.
 */
async function settleEach(items, act, verb) {
  let firstError = null;
  let failed = 0;
  let alreadyDone = 0;
  for (const item of items) {
    try {
      const answer = await act(item);
      // A run that had already finished keeps its result and is not "closed" by this (0.9.8 audit).
      if (answer?.alreadyTerminal) alreadyDone += 1;
      state.selectedDiagnosticIds.delete(`${item.kind}:${item.id}`);
    } catch (err) {
      failed += 1;
      firstError ??= err;
    }
  }
  const done = items.length - failed - alreadyDone;
  const kept = alreadyDone ? `, ${alreadyDone} had already finished and kept their result` : '';
  if (!failed) {
    toast(`${done} ${verb}${kept}.`, 'ok');
  } else {
    toast(`${done} of ${items.length} ${verb}${kept}, ${failed} failed: ${firstError?.message || firstError}`, 'error');
  }
  await refresh();
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
    await settleEach(contracts, (item) => remindWorkContract(item.id, false), 'reminded');
    return;
  }
  if (action === 'close') {
    if (!await uiConfirm(`Close ${selected.length} selected Work item${selected.length === 1 ? '' : 's'} as operator-reviewed?`)) return;
    await settleEach(selected, (item) => (item.kind === 'contract'
      ? closeWorkContract(item.id, false, false)
      : patchRun(item.id, {
        status: 'completed',
        requireReply: false,
        summary: 'Closed from Work by dashboard operator.',
        appendEvent: 'Closed from Work by dashboard operator.',
        eventType: 'operator_closed',
      })), 'closed');
  }
}
