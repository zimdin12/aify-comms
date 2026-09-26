// Dashboard Next SPA entry. ES module (DASHBOARD_REBUILD_PLAN §0.1): pure cores live in
// sibling modules and are imported here; app.js remains the orchestrator (render + actions +
// the single delegated event handler + init) until later Phase-0 slices split those too.
import { STATUS_KINDS, renderStatusChip, resolveStatus, statusWhyContext } from './status.js';
import { byId, toast } from './ui.js';
import { createChatController } from './chat.js';
import { inspectorRefreshDecision } from './inspector-refresh.mjs';
import { applyTheme, settingUnchanged } from './theme.js';
import { sessionAgentId, sessionId } from './record-fields.mjs';
import { state } from './state.mjs';
import { agentForSession, ensureSelectedSession, renderSessionRail } from './session-rail.mjs';
import { refreshActiveTerminalTheme, renderSettings } from './settings-panel.mjs';
import { openAgentDrawer, syncInspectorToSelection } from './agent-drawer.mjs';
import { renderActivityFeed, renderAttention } from './work-loop-panels.mjs';
import { codexConsoleConnections } from './codex-console.mjs';
import { openIdentityDirectory } from './identity-directory.mjs';
import { renderSessionActivity } from './session-activity.mjs';
import { createSpawnRequest, initEnvironmentActions, renderEnvironmentSpawnOptions, renderEnvironmentSummary, renderRuntime, renderSpawnRequests } from './environments-panels.mjs';
import { renderDiagnosticsSummary, renderMetrics, selectedDiagnostics } from './summary-tiles.mjs';
import { openCompactionHistory, openMessageDetail } from './inspector-forms.mjs';
import { restoreChatDraft, persistChatDrafts } from './chat-prefs.mjs';
import { messageHistory } from './message-store.mjs';
import { mountXtermForTerminal as mountXtermForTerminalImpl } from './xterm-mount.mjs';
import { renderSessionConsole as renderSessionConsoleImpl } from './session-console.mjs';
import { renderInstallSnippet } from './static-links.mjs';
import { pages } from './page-titles.mjs';
import { _agentSig, _chatChanSig, _chatConvSig, _contractSig, _envSig, _msgSig, _runSig, _settingsSig, _spawnReqSig } from './render-memo.mjs';
import { renderSection } from './render-memo.mjs';
import { defaultApiOrigin, resolveApiOrigin } from './api-origin.mjs';
import { setApiBase, api } from './api-client.mjs';
import { adoptLegacyApiKey } from './api-key.mjs';
import { renderFiles } from './shared-files.mjs';
import { chatLoadChannels, chatLoadConversation, chatSendMessage } from './message-transport.mjs';
import { runRefreshCycle } from './refresh-cycle.mjs';
import { connectRealtimeSocket, initRealtimeSocket, wireRealtimeResumeReconnect } from './realtime-socket.mjs';
import { initRunInspector, openRunInspector, renderRuns } from './run-inspector.mjs';
import { initAgentSessionActions } from './agent-session-actions.mjs';
import { loadAnalytics } from './analytics-page.mjs';
import { closeWorkContract, initWorkLoopActions, loadContractsForState, renderContracts, renderDiagnosticsBulkToolbar } from './work-loop-actions.mjs';
import { initMessageActions, markConversationRead, markVisibleRead, mountChatConsole } from './message-actions.mjs';
import { initConsoleActions, openRunConsole, resyncActiveConsole } from './console-actions.mjs';
import { dispatchClick, initClickDispatch } from './click-dispatch.mjs';
import { dashboardNotifier } from './notifications.mjs';
import { restorePersistedPreferences, wireGlobalControls, wireInspectorGestures, wireSettingsControls } from './boot-wiring.mjs';
import { createRefreshGate } from './refresh-visibility.mjs';
import { ChangeDrivenRefresh } from './change-refresh.mjs';
import { loadSlices } from './slice-loaders.mjs';

adoptLegacyApiKey(defaultApiOrigin()); // a key stored before keys were bound to an origin belongs to the default one (api-key.mjs)
const apiOrigin = resolveApiOrigin();
const apiBase = `${apiOrigin}/api/v1`;

// Agent status taxonomy: available (blue, wakeable/spawnable idle) → online
// (green, live worker idle) → working (animated, mid-turn). `ready` is an
// internal bridge readiness bit; if an older backend/cache still returns it,
// render it as online instead of introducing a second positive idle label.
const flowAssertions = {
  foundations: () => Boolean(STATUS_KINDS.unknown && state.terminalOwners && typeof connectRealtimeSocket === 'function'),
  sessions: () => Boolean(byId('session-rail') && byId('session-activity') && typeof renderSessionWorkspace === 'function'),
  runs: () => Boolean(state.stats.dispatch_runs_by_status !== undefined || byId('run-status-filter')),
  workLoop: () => Boolean(byId('send-reminders') && typeof closeWorkContract === 'function'),
  runInspector: () => Boolean(state.inspector.kind === 'run' && state.inspector.runId && byId('run-inspector-events') && byId('run-inspector-controls') && typeof resolveStatus === 'function'),
  statusWhy: () => Boolean(byId('status-why-popover') && typeof statusWhyContext === 'function'),
  activityFeed: () => Boolean(byId('activity-feed') && typeof renderActivityFeed === 'function'),
  diagnostics: () => Boolean(byId('diagnostics-summary') && byId('diagnostics-bulk-toolbar') && typeof selectedDiagnostics === 'function'),
  environments: () => Boolean(byId('environment-summary') && byId('environment-spawn-form') && typeof createSpawnRequest === 'function'),
};

const flowGates = {
  foundations: { enabled: false, assertion: flowAssertions.foundations },
  sessions: { enabled: false, assertion: flowAssertions.sessions },
  runs: { enabled: false, assertion: flowAssertions.runs },
  workLoop: { enabled: false, assertion: flowAssertions.workLoop },
  runInspector: { enabled: false, assertion: flowAssertions.runInspector },
  statusWhy: { enabled: false, assertion: flowAssertions.statusWhy },
  activityFeed: { enabled: false, assertion: flowAssertions.activityFeed },
  diagnostics: { enabled: false, assertion: flowAssertions.diagnostics },
  environments: { enabled: false, assertion: flowAssertions.environments },
};

let refreshTimer = null;
// In-flight guard: refresh() fires a ~10-request bundle; refreshSoon() can be triggered by
// every WS event. Without this, under poll load (slow single-worker service) bundles pile up
// faster than they drain and saturate the browser's ~6-connection-per-origin limit — which
// starves lazily-loaded pages (e.g. Analytics) of their own fetches. Coalesce: at most one
// bundle in flight; if more arrive while it runs, run exactly one more afterwards.
let _refreshInFlight = false;
let _refreshQueued = false;

// Chat-first landing controller (chat.js). Adapters bridge the pure module to app state:
// sendMessage routes DM→/messages/send (trigger+toast ladder) vs channel→/channels/{n}/send;
// loadConversation fetches a channel's messages; loadChannels refreshes the rail's channels.
const chatController = createChatController({
  state, byId, markVisibleRead,
  sendMessage: chatSendMessage,
  loadChannels: chatLoadChannels,
  refresh: () => refresh(),
  loadConversation: chatLoadConversation,
  loadAgentAnalytics: (id) => api(`/analytics/agent/${encodeURIComponent(id)}`),
  mountChatConsole: (agentId, hostEl) => mountChatConsole(agentId, hostEl),
  loadPulse: (mins) => api(`/analytics/pulse?window_minutes=${encodeURIComponent(mins)}`),
  persistDrafts: () => persistChatDrafts(),
  restoreDraft: () => restoreChatDraft(),
  // OLDER MESSAGES, ON DEMAND. The poll owns the newest page and replaces it every cycle; this
  // owns everything older and is only appended to, so the two cannot fight over one array.
  history: messageHistory, // the one store every message action reads (message-store.mjs)
  // Replying to a peer clears their unread badge — quiet, since the send already toasts.
  markConversationRead: (agentId, opts) => markConversationRead(agentId, opts),
  // Keep the details drawer pointed at whatever the operator just selected — otherwise its
  // lifecycle buttons act on the agent they navigated away from. See syncInspectorToSelection.
  onSelectionChange: () => syncInspectorToSelection(),
});

// Channels management (Phase 1.4): create/join/leave/read scoped to the viewing identity.
async function chatCreateChannel(name) {
  const clean = String(name || '').trim();
  if (!clean) return;
  await api('/channels', { method: 'POST', body: JSON.stringify({ name: clean, createdBy: state.chat.identity }) });
  await chatLoadChannels();
  state.chat.selected = `channel:${clean}`;
  try { await chatLoadConversation(clean); } catch (_) {}
  chatController.render();
  // This path selects a conversation WITHOUT going through open(), so it needs open()'s draft
  // handling too: a brand-new channel has no draft, and without this the previous chat's
  // half-written message stays in the box and is one Enter away from the wrong recipient.
  restoreChatDraft();
  toast(`Created #${clean}`, 'ok');
}

function evaluateFlowGates() {
  Object.values(flowGates).forEach((gate) => {
    gate.enabled = Boolean(gate.assertion());
  });
  return flowGates;
}

setApiBase(apiBase, apiOrigin);
const refreshGate = createRefreshGate({ onVisibleAgain: () => refresh() }); // a hidden tab fetches nothing and catches up once when shown (refresh-visibility.mjs)
const changeRefresh = new ChangeDrivenRefresh({ fullRefresh: () => refresh(), refreshSlices: (slices) => (refreshGate.admit() ? loadSlices(slices, { evaluateFlowGates, renderAll, refreshOpenInspector }) : Promise.resolve([])), pollSeconds: () => state.settings?.dashboard_refresh_seconds }); // what changed is refetched, and the timer runs only while the socket is down (change-refresh.mjs)

function refreshSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

async function refresh() {
  if (!refreshGate.admit()) return;
  // Coalesce concurrent refreshes so the poll bundle can't pile up (see _refreshInFlight).
  if (_refreshInFlight) { _refreshQueued = true; return; }
  _refreshInFlight = true;
  try {
    const started = changeRefresh.fullRefreshStarting();
    const failed = await _refreshImpl();
    changeRefresh.fullyRefreshed(started, failed); // current as of the start, except what failed (change-refresh.mjs)
  } finally {
    _refreshInFlight = false;
    if (_refreshQueued) { _refreshQueued = false; refreshSoon(); }
  }
}

// Re-render the open inspector drawer from current data, if its kind allows it.
// The per-kind rule lives in `inspector-refresh.mjs` (pure + tested); this function is only the
// wiring: map an allowed kind to the opener that rebuilds it.
function refreshOpenInspector() {
  const drawer = byId('inspector');
  const decision = inspectorRefreshDecision(state.inspector, {
    isOpen: !!drawer?.classList.contains('open'),
    // Editing focus, not mere containment: a focused BUTTON inside the drawer holds nothing that
    // can be lost, and treating it as "busy" suppressed every refresh (browser-verified).
    isEditingFocus: (() => {
      const el = document.activeElement;
      if (!el || !drawer || !drawer.contains(el)) return false;
      return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable === true;
    })(),
    isLoading: !!(state.inspector?.loadingMore || state.inspector?.loading),
  });
  if (decision !== 'refresh') return decision;
  const ins = state.inspector || {};
  try {
    switch (ins.kind) {
      case 'agent':
        if (ins.agentId) openAgentDrawer(ins.agentId);
        break;
      case 'run':
        if (ins.runId) openRunInspector({ runId: ins.runId, source: ins.source || 'refresh', sourceMessageId: ins.sourceMessageId || '' });
        break;
      case 'identity-directory':
        openIdentityDirectory();
        break;
      case 'history':
        if (ins.agentId) openCompactionHistory(ins.agentId);
        break;
      case 'message':
        if (ins.messageId) openMessageDetail(ins.messageId);
        break;
      default:
        return 'no-opener';
    }
  } catch (_e) {
    // A drawer that fails to re-render must never break the poll cycle that feeds the whole page.
    return 'failed';
  }
  return decision;
}

// The IMPLEMENTATION of the poll cycle lives in ./refresh-cycle.mjs. The bag is built at CALL time,
// not here, so every name resolves however app.js has it at the moment the poll fires.
const _refreshImpl = () => runRefreshCycle({
  armRefreshTimer,
  chatController,
  evaluateFlowGates,
  loadContractsForState,
  refreshOpenInspector,
  renderAll,
});

// Coalesce render bursts (e.g. many agent_status events during fleet turn-churn) into one
// render per animation frame. renderSection is signature-gated so the DOM writes already
// dedupe, but the per-section JSON.stringify fingerprinting ran synchronously per event.
let _renderAllScheduled = false;
function scheduleRenderAll() {
  if (_renderAllScheduled) return;
  _renderAllScheduled = true;
  requestAnimationFrame(() => { _renderAllScheduled = false; renderAll(); });
}

function renderAll() {
  const f = state.filter || '';
  renderSection('chat', [_agentSig(), _msgSig(), _chatChanSig(), _chatConvSig(), state.chat.selected, state.chat.view, state.chat.filter, state.chat.identity, state.chat.liveOnly, state.chat.analytics.agent, !!state.chat.analytics.data], () => chatController.render());
  renderSection('metrics', [_agentSig(), _contractSig().map((c) => [c[1], c[3]]), state.stats], renderMetrics);
  renderSection('attention', [_contractSig(), f], renderAttention);
  // Session workspace + console: not signature-gated (own internal guards preserve live state).
  renderSessionWorkspace();
  renderSection('activity', [_runSig().map((r) => [r[0], r[1]]), _msgSig(), _contractSig().map((c) => [c[0], c[1]])], renderActivityFeed);
  renderDiagnosticsSummary();
  renderDiagnosticsBulkToolbar();
  renderSection('contracts', [_contractSig(), byId('contract-state')?.value || '', byId('contract-category')?.value || '', f], renderContracts);
  renderSection('envSummary', [_envSig()], renderEnvironmentSummary);
  renderEnvironmentSpawnOptions();
  renderSection('runtime', [_envSig()], renderRuntime);
  renderSection('spawnRequests', [_spawnReqSig()], renderSpawnRequests);
  renderSection('runs', [_runSig(), f, state.runStatusFilter || '', state.runFromFilter, state.runToFilter, state.runRuntimeFilter, state.runSearch, [...state.selectedDiagnosticIds]], renderRuns);
  renderSection('files', [state.files.map((x) => [x.name, x.size, x.sharedAt]), f], renderFiles);
  renderSection('settings', _settingsSig(), renderSettings);
  // Keep the analytics page live while it's the active page (re-fetch on the poll cycle).
  if (byId('page-analytics')?.classList.contains('active')) loadAnalytics();
  // Keep the Fleet pulse live while it's the Chat landing view (no conversation open).
  if (byId('page-chat')?.classList.contains('active') && !state.chat.selected && !state.chat.analytics.agent) {
    chatController.refreshPulse();
  }
}

async function saveSettings() {
  const statusEl = byId('settings-status');
  const payload = {};
  document.querySelectorAll('#settings-form [data-setting-key]').forEach((el) => {
    const key = el.dataset.settingKey;
    const type = el.dataset.settingType;
    if (type === 'toggle') payload[key] = el.checked;
    else if (type === 'number') {
      let n = Number(el.value);
      if (el.value !== '' && Number.isFinite(n)) {
        // Clamp to the rendered min/max, which are the service's own (GET /settings/schema).
        const min = el.min !== '' ? Number(el.min) : null;
        const max = el.max !== '' ? Number(el.max) : null;
        if (min != null && Number.isFinite(min)) n = Math.max(min, n);
        if (max != null && Number.isFinite(max)) n = Math.min(max, n);
        payload[key] = n;
      }
    }
    else if (type === 'csv') payload[key] = el.value.split(',').map((s) => s.trim()).filter(Boolean);
    else payload[key] = el.value; // text, select, theme, color
  });
  for (const key of Object.keys(payload)) if (settingUnchanged(key, payload[key], state.settings, payload.dashboard_theme)) delete payload[key]; // send only what changed (theme.js)
  if (statusEl) statusEl.textContent = 'Saving…';
  try {
    const res = await api('/settings', { method: 'PUT', body: JSON.stringify(payload) });
    state.settings = res && typeof res === 'object' ? res : { ...state.settings, ...payload };
    applyTheme(state.settings); // persist + paint the saved appearance
    refreshActiveTerminalTheme();
    armRefreshTimer(); // a changed dashboard_refresh_seconds takes effect immediately, not next poll
    if (statusEl) statusEl.textContent = 'Saved';
    toast('Settings saved', 'ok');
    renderSettings();
  } catch (error) {
    if (statusEl) statusEl.textContent = `Save failed: ${error?.message || error}`;
    toast(`Save failed: ${error?.message || error}`, 'error');
  }
}

// The IMPLEMENTATION lives in ./xterm-mount.mjs, together with the two counters only it reads. This is
// the binding that supplies `resyncActiveConsole`, which stays here because it reaches `refresh`.
// Deliberately NOT phrased as a `moved to` marker: `moved-names-resolve` treats a marker plus a local
// declaration of the same name as a fork, and it is right to — this is a shim, not a move.
const mountXtermForTerminal = (terminalId, agentId, container, opts) =>
  mountXtermForTerminalImpl(terminalId, agentId, container, opts, { resyncActiveConsole });

// Don't leak codex console sockets across an unload/navigation.
window.addEventListener('beforeunload', () => { codexConsoleConnections.forEach((e) => { try { e.ws?.close(); } catch {} }); });

// Renders an agent's live console (PTY xterm / hermes iframe / codex synth / start-console
// offer) into `targetEl`. Defaults to the Sessions page summary pane, but the Chat page passes
// its own host so the same terminal widget is reachable inline from a conversation.
// The IMPLEMENTATION lives in ./session-console.mjs. This binding supplies the three names that stay
// here because each reaches `refresh`. Not phrased as a `moved to` marker — see mountXtermForTerminal.
const renderSessionConsole = (session, targetEl, opts) =>
  renderSessionConsoleImpl(session, targetEl, opts, { mountXtermForTerminal, refresh, resyncActiveConsole });

function renderSessionWorkspace() {
  const session = ensureSelectedSession();
  renderSessionRail();
  const tab = state.selectedSessionTab === 'activity' ? 'activity' : 'console';
  document.querySelectorAll('[data-session-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.sessionTab === tab);
  });
  byId('session-console-panel').classList.toggle('active', tab === 'console');
  byId('session-activity-panel').classList.toggle('active', tab === 'activity');
  if (!session) {
    byId('session-title').textContent = 'No sessions loaded';
    byId('session-subtitle').textContent = 'Spawn or connect an agent to start a session workspace.';
    byId('session-status').innerHTML = renderStatusChip('unknown', statusWhyContext('session', {}, 'unknown'));
    byId('session-activity').innerHTML = '<div class="empty-state"><span class="empty-icon">🖥️</span><strong>No session selected</strong><p>Pick a session from the rail to see its live terminal and activity.</p></div>';
    byId('session-console-summary').innerHTML = '<div class="empty-state"><span class="empty-icon">🖥️</span><strong>No session selected</strong><p>Pick a session from the rail to open its live terminal.</p></div>';
    return;
  }
  const agentId = sessionAgentId(session);
  byId('session-title').textContent = agentId || sessionId(session);
  byId('session-subtitle').textContent = session.workspace || 'Live terminal and lifecycle for this session.';
  byId('session-status').innerHTML = renderStatusChip(session.status || agentForSession(session).status || 'unknown', statusWhyContext('session', session, session.status || agentForSession(session).status || 'unknown'));
  renderSessionActivity(session);
  renderSessionConsole(session);
}

function openInspector(request) {
  if (request && request.kind === 'run' && request.runId && state.inspector.runId !== String(request.runId)) {
    openRunInspector(request);
    return;
  }
  const inspector = byId('inspector');
  if (inspector && !inspector.classList.contains('open')) _inspectorReturnFocus = document.activeElement;
  inspector?.classList.add('open');
  inspector?.classList.toggle('run-inspector-sheet', state.inspector.kind === 'run' || request?.kind === 'run');
  // Move focus into the drawer so keyboard users land in the panel (and Escape can return them).
  setTimeout(() => { const f = inspector?.querySelector('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'); if (f) try { f.focus(); } catch {} }, 30);
}
let _inspectorReturnFocus = null;

function closeInspector() {
  const inspector = byId('inspector');
  inspector?.classList.remove('open');
  inspector?.classList.remove('run-inspector-sheet');
  state.inspector = { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' };
  byId('inspector-content').textContent = 'Select an item to inspect details.';
  try { if (_inspectorReturnFocus && _inspectorReturnFocus.focus) _inspectorReturnFocus.focus(); } catch {}
  _inspectorReturnFocus = null;
  evaluateFlowGates();
}

function setPage(page) {
  const [title, subtitle] = pages[page] || pages.sessions;
  byId('page-title').textContent = title;
  byId('page-subtitle').textContent = subtitle;
  document.querySelectorAll('.page').forEach((el) => el.classList.toggle('active', el.id === `page-${page}`));
  document.querySelectorAll('.nav-item[data-page]').forEach((el) => {
    const on = el.dataset.page === page;
    el.classList.toggle('active', on);
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  document.querySelectorAll('.mobile-tabbar [data-page]').forEach((el) => {
    const on = el.dataset.page === page;
    el.classList.toggle('active', on);
    if (on) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  // WS-G1: the Needs-Attention strip belongs to the landing surface only — showing it on every
  // page wasted ~210px and made every page feel sparse. Chat is the landing; show it there.
  const strip = byId('attention-strip');
  if (strip) strip.hidden = page !== 'chat';
  // Re-render the session workspace on switch so its now-visible console mounts immediately
  // (renderSessionConsole no-ops on a hidden host, so without this the terminal would only
  // appear on the next poll). Cheap + idempotent; on non-Sessions pages the console render
  // no-ops (host hidden) and only the title/rail update.
  renderSessionWorkspace();
}

// The delegated click dispatcher moved to ./click-dispatch.mjs in v0.5.4. Registering it stays here,
// so the boot sequence remains visible in one place.
document.addEventListener('click', dispatchClick);

// The boot-time listener wiring moved to ./boot-wiring.mjs in v0.5.4. The CALL stays here so the
// boot sequence is still readable in one place, in order.
wireGlobalControls({ chatController, closeInspector, refresh, renderAll, renderSessionWorkspace, saveSettings, chatCreateChannel });
// The inspector's swipe-to-close gesture moved to ./boot-wiring.mjs in v0.5.4, with the
// touch-start position it is the only reader of.
wireInspectorGestures();

// Preference restore + landing paint moved to ./boot-wiring.mjs in v0.5.4.
restorePersistedPreferences({ setPage });

initAgentSessionActions({ chatController, closeInspector, markConversationRead, refresh, refreshSoon, renderSessionWorkspace, setPage });
initMessageActions({ chatController, refreshSoon, renderSessionConsole });
initConsoleActions({ closeInspector, refresh, refreshSoon, setPage });
initEnvironmentActions({ closeInspector, refresh, refreshSoon });
initClickDispatch({ chatController, closeInspector, refreshSoon, renderSessionWorkspace, setPage });
initWorkLoopActions({ refresh });
initRunInspector({ closeInspector, evaluateFlowGates, openInspector, openRunConsole, refresh, renderDiagnosticsBulkToolbar });
initRealtimeSocket({ changeRefresh, dashboardNotifier, evaluateFlowGates, refreshSoon, resyncActiveConsole, scheduleRenderAll });
connectRealtimeSocket();
wireRealtimeResumeReconnect();
refresh();
// The timed poll honours `dashboard_refresh_seconds` and runs only while the socket is down (change-refresh.mjs).
function armRefreshTimer() { changeRefresh.armPoll(); }
armRefreshTimer();
// The Settings page's controls moved to ./boot-wiring.mjs in v0.5.4.
wireSettingsControls({ saveSettings });

// The Help card snippet is static markup until we stamp the real origin into it. Done once at
// module load — the card is always in the DOM, just on a hidden page until Settings/Help is opened.
renderInstallSnippet();
