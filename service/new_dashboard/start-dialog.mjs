// The Start dialog: one search box over every agent, and the one action that fits each (v0.7.7).
//
// THE OPERATOR: "starting agent maybe should open something that has a search". The dashboard had
// five ways to start or spawn, none searched, and the spawn form's Agent ID is free text. Opened from
// the top bar's "Start agent" or Ctrl+K.
//
// Enter does the fitting action: Start where `startOffer` says the service would start the agent,
// through the guarded `POST /agents/{id}/control`; the console for a live agent; the reason for any
// other. The route can still refuse, and its refusal is shown here. A name no agent matches offers
// Create, which opens the existing Environments spawn form with the ID filled in.
//
// One injected set of page functions, `initStartDialog`, like the other action modules.

import { api } from './api-client.mjs';
import { startOffer } from './agent-click-handlers.mjs';
import { state } from './state.mjs';
import { ACTIVE_AGENT_STATUSES, AGENT_STATUS_MEANINGS, resolveStatus } from './status.js';
import { byId, toast } from './ui.js';
import { esc } from './util.js';

let page = { setPage() {}, chatController: { open() {} }, refreshSoon() {} };

/** Supply the page functions the actions need. */
export function initStartDialog(deps) {
  page = deps;
}

/** The action for one agent: `start`, `console` for a live one, or `none` with the reason. */
function actionFor(agent) {
  const offer = startOffer(agent);
  if (offer.start) return { action: 'start', why: '' };
  const kind = resolveStatus(agent?.status).kind;
  if (ACTIVE_AGENT_STATUSES.includes(kind)) return { action: 'console', why: '' };
  return { action: 'none', why: offer.why || AGENT_STATUS_MEANINGS[kind] || kind };
}

/** The dialog's rows for `query`, by id; a Create row when nothing matches a non-empty query. */
export function startDialogRows(agents, query) {
  const needle = String(query ?? '').trim();
  const lower = needle.toLowerCase();
  const rows = (agents || [])
    .filter((agent) => agent?.id && agent.id !== 'dashboard')
    .filter((agent) => !lower || [agent.id, agent.role, agent.runtime, resolveStatus(agent.status).label]
      .join(' ').toLowerCase().includes(lower))
    .map((agent) => ({ id: String(agent.id), status: agent.status || 'unknown', ...actionFor(agent) }))
    .sort((a, b) => a.id.localeCompare(b.id));
  if (!rows.length && needle) rows.push({ id: needle, status: '', action: 'create', why: '' });
  return rows;
}

/**
 * Do what Enter on `row` means. `post` is the control route; `say` writes the dialog's status line;
 * `started` reports an accepted start; `close` closes the dialog. A refused start stays open with
 * the route's reason, because closing would hide it.
 */
export async function startDialogAct(row, { post, openConsole, openCreate, close, say, started }) {
  if (row.action === 'start') {
    say(`Starting ${row.id}…`);
    try {
      const answer = await post(row.id);
      started(answer?.alreadyRunning ? `${row.id} is already running`
        : `Starting ${row.id}; its console appears once the worker is up`);
      close();
    } catch (error) {
      say(`${row.id} was not started: ${error?.message || error}`);
    }
    return;
  }
  if (row.action === 'console') { close(); openConsole(row.id); return; }
  if (row.action === 'create') { close(); openCreate(row.id); return; }
  say(`${row.id}: ${row.why}`);
}

/**
 * `startDialogAct` for one open dialog, ignoring Enter and clicks while a start is in flight, as the
 * drawer's Start disables its button. Two Enters inside one request's round trip posted two starts.
 * A refused start releases it, so the operator can retry.
 */
export function startDialogActor(deps) {
  let inFlight = null;
  return (row) => {
    if (!row) return undefined;
    if (inFlight) return inFlight;
    const done = startDialogAct(row, deps);
    if (row.action !== 'start') return done;
    inFlight = done.finally(() => { inFlight = null; });
    return inFlight;
  };
}

/** Ctrl+K, outside a terminal: inside one it is the shell's kill-line and belongs to the agent. */
export function isStartHotkey(event) {
  return event?.ctrlKey === true && !event.shiftKey && !event.altKey && String(event.key).toLowerCase() === 'k'
    && !event.target?.closest?.('.xterm, .console-embed');
}

const HINTS = { start: 'Enter starts it', console: 'Enter opens its console' };

function rowHtml(row, at, active) {
  const dot = row.status ? `<span class="status-dot ${esc(resolveStatus(row.status).dotKind)}"></span> ` : '';
  const hint = row.action === 'create' ? `Create "${row.id}" in the spawn form` : HINTS[row.action] || row.why;
  return `<button type="button" class="chat-rail-item${at === active ? ' active' : ''}" role="option" aria-selected="${at === active}" data-start-row="${at}">`
    + `<span>${dot}<strong>${esc(row.id)}</strong> <span class="subtle">${esc(resolveStatus(row.status).label)}</span></span>`
    + `<span class="subtle">${esc(hint)}</span></button>`;
}

/** Open the dialog, or do nothing when one is already open. */
export function openStartDialog() {
  if (document.querySelector('.start-dialog')) return;
  const overlay = document.createElement('div');
  overlay.className = 'dialog-overlay';
  overlay.innerHTML = `<div class="dialog start-dialog" role="dialog" aria-modal="true" aria-labelledby="start-dialog-title">
      <h3 class="dialog-title" id="start-dialog-title">Start an agent</h3>
      <input class="dialog-input" type="search" autocomplete="off" placeholder="Search agents by name, role, runtime or status" aria-label="Search agents">
      <div class="chat-rail-list" role="listbox" aria-label="Agents" style="max-height: 50vh"></div>
      <p class="dialog-message" aria-live="polite"></p>
      <div class="dialog-actions"><button class="ghost dialog-cancel" type="button">Close</button></div>
    </div>`;
  document.body.appendChild(overlay);
  const input = overlay.querySelector('.dialog-input');
  const list = overlay.querySelector('[role="listbox"]');
  const status = overlay.querySelector('.dialog-message');
  const previouslyFocused = document.activeElement;
  let rows = [];
  let active = 0;
  const draw = () => {
    rows = startDialogRows(state.agents, input.value);
    active = Math.min(active, Math.max(0, rows.length - 1));
    list.innerHTML = rows.map((row, at) => rowHtml(row, at, active)).join('');
  };
  const close = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey, true);
    try { previouslyFocused?.focus?.(); } catch { /* the opener may be gone */ }
  };
  const act = startDialogActor({
    post: (id) => api(`/agents/${encodeURIComponent(id)}/control`, { method: 'POST', body: JSON.stringify({ action: 'start', from_agent: 'dashboard' }) }),
    openConsole: (id) => { page.setPage('chat'); state.chat.view = 'console'; page.chatController.open(`dm:${id}`); },
    openCreate: (id) => {
      page.setPage('environments');
      const field = byId('env-spawn-agent-id');
      if (field) { field.value = id; field.focus(); }
    },
    close,
    say: (text) => { status.textContent = text; },
    started: (text) => { toast(text, 'ok'); page.refreshSoon(); },
  });
  const onKey = (event) => {
    // Captured and stopped, so Escape does not also close the drawer beneath, as `openDialog` does.
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (rows.length) active = (active + (event.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length;
      list.innerHTML = rows.map((row, at) => rowHtml(row, at, active)).join('');
      return;
    }
    if (event.key === 'Enter' && event.target === input) { event.preventDefault(); event.stopPropagation(); act(rows[active]); }
  };
  document.addEventListener('keydown', onKey, true);
  input.addEventListener('input', () => { active = 0; status.textContent = ''; draw(); });
  list.addEventListener('click', (event) => {
    const button = event.target.closest('[data-start-row]');
    if (button) act(rows[Number(button.dataset.startRow)]);
  });
  overlay.querySelector('.dialog-cancel').addEventListener('click', close);
  overlay.addEventListener('click', (event) => { if (event.target === overlay) close(); });
  draw();
  setTimeout(() => input.focus(), 30);
}
