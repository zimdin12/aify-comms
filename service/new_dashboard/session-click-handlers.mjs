// Click-handler bodies for the session workspace's filter and selection controls.
//
// All three lived inside app.js's delegated click handler and were unreachable by any test. They travel
// with the two helpers only they used — `SESSION_LIVE_KINDS` and `persistSessionStatusFilter`, which had
// no other caller in app.js at all, so this is their whole population rather than a split one.
//
// `renderSessionWorkspace` is INJECTED. It stays in app.js (it reaches most of the render web), and a
// parameter of the same name leaves each body byte-identical to the branch it left.

import { sessionAgentId } from './record-fields.mjs';
import { SESSION_FILTER_KINDS, selectedSession } from './session-rail.mjs';
import { state } from './state.mjs';
import { LIVE_AGENT_STATUSES } from './status.js';

// H1: these were hand-copies of status_engine.VALID_STATUSES. They now alias the single JS owner in
// status.js, which is bound to the Python source by a test.
const SESSION_LIVE_KINDS = LIVE_AGENT_STATUSES;

export function persistSessionStatusFilter() {
  try { localStorage.setItem('aifySessionStatusFilter', JSON.stringify([...state.sessionStatusFilter])); } catch { /* ignore */ }
}

export function applySessionStatusPreset(sessionStatusPreset, renderSessionWorkspace) {
  const which = sessionStatusPreset.dataset.sessionStatusPreset;
  state.sessionStatusFilter = new Set(which === 'all' ? SESSION_FILTER_KINDS : which === 'live' ? SESSION_LIVE_KINDS : []);
  persistSessionStatusFilter();
  renderSessionWorkspace();
}

export function toggleSessionStatusFilter(sessionStatusFilter, renderSessionWorkspace) {
  const k = sessionStatusFilter.dataset.sessionStatusFilter;
  if (state.sessionStatusFilter.has(k)) state.sessionStatusFilter.delete(k);
  else state.sessionStatusFilter.add(k);
  persistSessionStatusFilter();
  renderSessionWorkspace();
}

export function toggleSessionCheckbox(sessionCheckbox, renderSessionWorkspace) {
  const id = sessionCheckbox.dataset.sessionCheckbox;
  if (sessionCheckbox.checked) state.selectedSessionIds.add(id);
  else state.selectedSessionIds.delete(id);
  renderSessionWorkspace();
}

export function openAgentSessions(agentOpenSessions, renderSessionWorkspace, setPage, closeInspector) {
  const sid = agentOpenSessions.dataset.agentOpenSessions;
  if (sid) { state.selectedSessionId = sid; renderSessionWorkspace(); }
  setPage('sessions');
  closeInspector();
}

export function selectSessionRow(sessionSelect, renderSessionWorkspace) {
  state.selectedSessionId = sessionSelect.dataset.sessionSelect;
  const session = selectedSession();
  state.selectedConversation = session ? sessionAgentId(session) || 'dashboard' : 'dashboard';
  renderSessionWorkspace();
}

// The session detail tab selector.
export function selectSessionTab(sessionTab, renderSessionWorkspace) {
  state.selectedSessionTab = sessionTab.dataset.sessionTab || 'console';
  renderSessionWorkspace();
}
