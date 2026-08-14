// The Sessions rail: grouping sessions by environment, the status filter, and the bulk-select toolbar.
//
// The first SUBJECT slice out of app.js in v0.5.4, and it only became possible once `state` and `byId` had
// owners of their own (`state.mjs`, `ui.js`). Before that, every render group in app.js read at least one
// name that app.js itself declared — and a module extracted from app.js cannot import those back without
// the upward import this series forbids everywhere, which here would also be a cycle. This closure now
// needs nothing from app.js at all; everything it uses comes from sibling leaf modules, imported downward.
//
// MEASURED AS A GROUP, not per function. Per-function measurement counts a call between two functions that
// move together in the same slice as a blocker, which makes every cohesive cluster look welded in place;
// it is what produced the withdrawn "app.js is not reducible by relocation" ruling.
//
// `SESSION_FILTER_KINDS` and `sessionGroupCollapsed` come along because nothing outside this closure reads
// them. That is the ownership test used throughout the series: count the DIRECT readers of a module-scope
// name, and if they are all inside the group, the group owns it. The previous attempt at a subject slice
// got exactly this wrong — it took a `const` whose initializer reached half the file, because the closure
// walk only expanded functions and never read a data declaration's own references. That slice was
// reverted; see docs/APP_JS_STATE_MODULE_PACKET.md.
//
// Every declaration below is byte-identical to the one that stood in app.js; the only substitution is the
// added `export `, which the reconstruction proof strips before comparing. Their leading comments stayed
// behind in app.js deliberately: `declarationSpan` returns the declaration alone, so a span that took its
// comments with it could not round-trip through the proof.


import { sessionAgentId, sessionEnvironmentId, sessionId, sessionRuntime } from './record-fields.mjs';
import { collapseSupersededSessions, countSupersededSessions } from './sessions-list.mjs';
import { state } from './state.mjs';
import { AGENT_STATUSES, renderStatusChip, resolveStatus, statusWhyContext } from './status.js';
import { byId } from './ui.js';
import { esc } from './util.js';

export function agentForSession(session) {
  const agentId = sessionAgentId(session);
  return state.agents.find((agent) => String(agent.id) === agentId) || {};
}
export function groupedSessionsByEnvironment() {
  const groups = new Map();
  const filter = state.sessionStatusFilter;
  const find = state.filter.trim().toLowerCase();
  // Collapse an agent's SUPERSEDED rows (see sessions-list.mjs). Applied HERE, at the list render,
  // rather than where `state.sessions` is assigned: that array also builds `state.terminalOwners`
  // and backs `sessionForAgent`, so narrowing it would silently change lookups far from this page.
  const visibleSessions = state.showSupersededSessions
    ? state.sessions
    : collapseSupersededSessions(state.sessions, { agentIdOf: sessionAgentId });
  visibleSessions.forEach((session) => {
    // WS-F status multiselect: empty filter = all; otherwise keep only matching status kinds.
    if (filter && filter.size) {
      const agent = agentForSession(session);
      const kind = resolveStatus(session.status || agent.status || 'unknown').kind;
      if (!filter.has(kind)) return;
    }
    // WS-H6: the top-bar global Find also narrows Sessions (id / agent / workspace / runtime).
    if (find) {
      const hay = [sessionId(session), sessionAgentId(session), session.workspace || session.cwd, sessionRuntime(session), sessionEnvironmentId(session)].join(' ').toLowerCase();
      if (!hay.includes(find)) return;
    }
    const envId = sessionEnvironmentId(session);
    if (!groups.has(envId)) {
      const env = state.environments.find((item) => String(item.id || item.environmentId) === envId) || {};
      groups.set(envId, { id: envId, label: env.label || env.name || envId, sessions: [] });
    }
    groups.get(envId).sessions.push(session);
  });
  return [...groups.values()].sort((a, b) => String(a.label).localeCompare(String(b.label)));
}
export function selectedSessionIds() {
  return [...state.selectedSessionIds].filter((id) => state.sessions.some((session) => sessionId(session) === id));
}
export function renderSessionBulkToolbar() {
  const toolbar = byId('session-bulk-toolbar');
  const ids = selectedSessionIds();
  toolbar.hidden = ids.length === 0;
  toolbar.innerHTML = ids.length
    ? `<span>${ids.length} selected</span>
       <button class="ghost" data-bulk-session-action="recreate">Reset</button>
       <button class="ghost" data-bulk-session-action="restart">Restart</button>
       <button class="ghost danger" data-bulk-session-action="stop">Stop</button>
       <button class="ghost danger" data-bulk-session-action="delete">Delete</button>`
    : '';
}
export const SESSION_FILTER_KINDS = AGENT_STATUSES;
export function renderSessionStatusFilter() {
  const host = byId('session-status-filter');
  if (!host) return;
  const presets = `<span class="filter-presets">`
    + `<button type="button" class="filter-preset" data-session-status-preset="all">All</button>`
    + `<button type="button" class="filter-preset" data-session-status-preset="none">None</button>`
    + `<button type="button" class="filter-preset" data-session-status-preset="live">Live</button>`
    + `</span>`;
  const chips = SESSION_FILTER_KINDS.map((k) =>
    `<button type="button" class="session-filter-chip${state.sessionStatusFilter.has(k) ? ' active' : ''}" data-session-status-filter="${k}" aria-pressed="${state.sessionStatusFilter.has(k) ? 'true' : 'false'}">${k}</button>`
  ).join('');
  // "N hidden" so a filtered-empty rail reads as filtered, not "no sessions."
  let hiddenNote = '';
  const filter = state.sessionStatusFilter;
  if (filter && filter.size) {
    const hidden = state.sessions.filter((s) => !filter.has(resolveStatus(s.status || agentForSession(s).status || 'unknown').kind)).length;
    if (hidden) hiddenNote = `<span class="filter-hidden-note">${hidden} hidden by filter</span>`;
  }
  // Superseded rows are collapsed so one agent reads as ONE entry — but say how many, so the list
  // never silently shrinks (a quiet cap reads as "that is everything").
  const superseded = countSupersededSessions(state.sessions, { agentIdOf: sessionAgentId });
  if (state.showSupersededSessions) {
    hiddenNote += `<button type="button" class="filter-hidden-note" data-toggle-superseded title="Collapse older sessions again so each agent reads as one entry">showing older sessions — collapse</button>`;
  } else if (superseded) {
    hiddenNote += `<button type="button" class="filter-hidden-note" data-toggle-superseded title="Older non-live sessions for agents that already have a newer one. Click to show them — they are not reachable anywhere else, and Delete session is only offered on a visible row.">${superseded} older session${superseded === 1 ? '' : 's'} collapsed — show</button>`;
  }
  host.innerHTML = presets + chips + hiddenNote;
}
export function renderSessionRail() {
  const groups = groupedSessionsByEnvironment();
  renderSessionBulkToolbar();
  renderSessionStatusFilter();
  byId('session-rail').innerHTML = groups.length ? groups.map((group) => `
    <details class="session-env-group" data-env-group="${esc(group.id)}"${sessionGroupCollapsed(group.id) ? '' : ' open'}>
      <summary class="session-env-title">${esc(group.label)} <span>${group.sessions.length}</span></summary>
      ${group.sessions.map((session) => {
        const id = sessionId(session);
        const agent = agentForSession(session);
        const status = session.status || agent.status || 'unknown';
        const active = id === state.selectedSessionId ? ' active' : '';
        const checked = state.selectedSessionIds.has(id) ? ' checked' : '';
        return `
          <article class="session-row${active}" data-session-select="${esc(id)}" data-kind="session" data-id="${esc(id)}">
            <input class="session-check" type="checkbox" data-session-checkbox="${esc(id)}"${checked} title="Select session">
            <div class="session-row-body">
              <div class="item-title">
                <strong class="clip">${esc(sessionAgentId(session) || id)}</strong>
                <span class="item-title-status">${renderStatusChip(status, statusWhyContext('session', session, status))}${String(agent.status || '').startsWith('blocked') ? '<span class="chat-await-badge" title="Agent is blocked on an interactive prompt — open its Console">⌛ input</span>' : ''}</span>
              </div>
              <p class="preview">${esc(session.workspace || session.cwd || '')}</p>
              <span class="session-runtime-badge" data-runtime="${esc(sessionRuntime(session))}">${esc(sessionRuntime(session))}</span>
            </div>
          </article>`;
      }).join('')}
    </details>`).join('') : '<div class="empty-state"><span class="empty-icon">🖥️</span><strong>No sessions yet</strong><p>Spawn a managed session from Environments to get an agent running.</p><button class="primary" data-page-jump="environments">Spawn a session</button></div>';
}
export function sessionGroupCollapsed(envId) {
  try { return (JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []).includes(envId); } catch { return false; }
}

// ---------------------------------------------------------------------------------------------------
// Which session is CURRENT, appended in a later v0.5.4 slice.
//
// It joins this module rather than getting one of its own because it is the write side of what
// `selectedSessionIds()` above already reads: both maintain `state.selectedSessionIds`, and splitting a
// set's reader from its pruner across two modules is how they drift.
//
// `ensureSelectedSession` runs on every refresh and does three things that each fail quietly: it keeps a
// selection pointing at a session that still exists, falls back to the first when the current one is gone,
// and PRUNES multi-select ids whose sessions have disappeared — without that last step a bulk action fires
// against rows that are no longer there.

export function selectedSession() {
  return state.sessions.find((session) => sessionId(session) === state.selectedSessionId) || null;
}
export function ensureSelectedSession() {
  if (!state.sessions.length) {
    state.selectedSessionId = '';
    state.selectedConversation = 'dashboard';
    state.selectedSessionIds.clear();
    return null;
  }
  const current = selectedSession();
  const session = current || state.sessions[0];
  state.selectedSessionId = sessionId(session);
  state.selectedConversation = sessionAgentId(session) || 'dashboard';
  for (const id of [...state.selectedSessionIds]) {
    if (!state.sessions.some((item) => sessionId(item) === id)) state.selectedSessionIds.delete(id);
  }
  return session;
}
