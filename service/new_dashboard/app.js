// Dashboard Next SPA entry. ES module (DASHBOARD_REBUILD_PLAN §0.1): pure cores live in
// sibling modules and are imported here; app.js remains the orchestrator (render + actions +
// the single delegated event handler + init) until later Phase-0 slices split those too.
import { esc, fileSizeLabel, relTime, tsMs, usageFmtTokens, usageResetLabel } from './util.js';
import { createTerminalInputPoster, createTerminalInputHandler, forceTerminalRepaint, waitForTerminalSize, wheelInputSequence } from './terminal-input.mjs';
import { continueCliCommand, continueCliDetails, continueCliInfo, resumeMachineNote } from './cli-resume.mjs';
import { collapseSupersededSessions, countSupersededSessions } from './sessions-list.mjs';
import { AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';
import { hermesGatewayUrlToHttp, chooseSessionConsoleWidget } from './console-chooser.js';
import { byId, toast, uiConfirm, uiPrompt, installRejectionToast } from './ui.js';
import { createChatController } from './chat.js';
import { inspectorRefreshDecision } from './inspector-refresh.mjs';
import { createNotifier, readEnabled, writeEnabled, requestPermission } from './notify.mjs';
import { THEMES, applyTheme, applyCachedTheme, previewTheme, paletteFromSettings } from './theme.js';
import { settingsFieldHtml } from './settings-fields.mjs';
import {
  asAgentArray,
  asArray,
  contractActionable,
  contractCategory,
  environmentRoots,
  environmentRuntimes,
  messageId,
  messageIdOf,
  messageRunId,
  runPendingControlCount,
  runTargetAgent,
  sessionAgentId,
  sessionEnvironmentId,
  sessionId,
  sessionRuntime,
} from './record-fields.mjs';
import { environmentStartCommand } from './environment-start-command.mjs';
import { renderRunEvent } from './run-event.mjs';
import { applyRenderedWidth } from './terminal-width.mjs';
import { trafficChartHtml, statCardsHtml, healthGridHtml, runStatusMixHtml, rangeSelectorHtml, rangeDef, opsKpisHtml, dispatchOutcomesHtml, agentLeaderboardHtml, busiestChannelsHtml, failureReasonsHtml } from './analytics.js';
import { state } from './state.mjs';
import { SESSION_FILTER_KINDS, agentForSession, agentForTerminal, ensureSelectedSession, renderModeSwitchChip, renderSessionModeLabel, renderSessionRail, selectedSession, selectedSessionIds, toggleSupersededSessions } from './session-rail.mjs';
import { applyThemeChoice, previewAppearance, refreshActiveTerminalTheme, renderSettings, selectSettingsTab, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';
import { openAgentDrawer, sessionForAgent, syncInspectorToSelection } from './agent-drawer.mjs';
import { MAINTENANCE_ACTIONS, applyContractView, applyWorkView, contractCard, diagnosticKey, filtered, jumpFromDiagnostic, matchesGlobalFilter, pruneDiagnosticSelection, renderActivityFeed, renderAttention, renderContractBoard, toggleDiagnosticSelection } from './work-loop-panels.mjs';
import { codexConsoleAppendLine, codexConsoleClose, codexConsoleConnect, codexConsoleConnections, codexConsoleSendTurn } from './codex-console.mjs';
import { openIdentityDirectory } from './identity-directory.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { renderSessionActivity, runFrom } from './session-activity.mjs';
import { openEnvironmentRootsEditor, renderEnvironmentSpawnOptions, renderEnvironmentSummary, renderRuntime, renderSpawnRequests } from './environments-panels.mjs';
import { metric, renderDiagnosticsSummary, renderMetrics, renderUsageConsumption, selectedDiagnostics } from './summary-tiles.mjs';
import { copyActiveConsole, copyText } from './clipboard.mjs';
import { openAgentEditForm, openCompactionHistory, openContinueForm, openMessageDetail } from './inspector-forms.mjs';
import { renderRunInspectorControls, runInspectorCapabilities, sessionForRun } from './run-inspector-controls.mjs';
import { persistChatDrafts, persistChatPrefs, syncChatChips, toggleChatCompact, toggleChatPeek } from './chat-prefs.mjs';
import { runAgentControl, startColdAgent, switchAgentModeFromRow, switchModeFromChip, toggleFavouriteRow } from './agent-click-handlers.mjs';
import { runConsoleAction } from './console-click-handlers.mjs';
import { consoleAwaitingInputHint, updateAwaitPill } from './console-await.mjs';
import { mountXtermForTerminal as mountXtermForTerminalImpl } from './xterm-mount.mjs';
import { renderSessionConsole as renderSessionConsoleImpl } from './session-console.mjs';
import { handleGlobalKeydown } from './keyboard-shortcuts.mjs';
import { renderInstallSnippet, updateStaticLinks } from './static-links.mjs';
import { lookup } from './record-lookup.mjs';
import { pages } from './page-titles.mjs';
import { _agentSig, _chatChanSig, _chatConvSig, _contractSig, _envSig, _msgSig, _runSig, _spawnReqSig } from './render-memo.mjs';
import { renderSection } from './render-memo.mjs';
import { preferredNavCollapsed, setNavCollapsed, toggleSessionGroupCollapsed } from './layout-prefs.mjs';
import { RUN_INSPECTOR_EVENT_LIMIT, loadRunDetails, loadRunEvents, patchRun, runQueryPath, runSourceMessage, syncRunFilterOptions } from './run-helpers.mjs';
import { navigateToPage, openEnvironmentSpawn, openHermesTabFromRow, selectAnalyticsRange } from './nav-click-handlers.mjs';
import { openChatConversation, openChatReply, runChannelAction, setChatView, setPulseWindow } from './chat-click-handlers.mjs';
import { applySessionStatusPreset, openAgentSessions, selectSessionRow, selectSessionTab, toggleSessionCheckbox, toggleSessionStatusFilter } from './session-click-handlers.mjs';
import { resolveApiOrigin } from './api-origin.mjs';
import { setApiBase, api } from './api-client.mjs';
import { attachChatFile, deleteSharedFileFromRow, loadFiles, renderFiles, uploadPastedImage, uploadSharedFile } from './shared-files.mjs';
import { chatLoadChannels, chatLoadConversation, chatSendMessage, sendRunFollowup } from './message-transport.mjs';
import { runRefreshCycle } from './refresh-cycle.mjs';
import { connectRealtimeSocket, initRealtimeSocket, wireRealtimeResumeReconnect } from './realtime-socket.mjs';
import { handleRunInspectorControl, initRunInspector, loadMoreRunEvents, loadRunsForStatus, openRunInspector, renderRunInspector, renderRuns, requestRunControl, toggleRunEventOrder } from './run-inspector.mjs';
import { deleteSessionById, initAgentSessionActions, openAgentChat, removeAgent, requestBulkSessionControl, requestSessionControl, resolveAgentSession, stopAgentWorker, submitAgentEdit, submitContinue, switchAgentSessionMode } from './agent-session-actions.mjs';
import { loadAnalytics, renderAnalyticsPage, renderUsagePools } from './analytics-page.mjs';
import { loadVersionBadge } from './version-badge.mjs';
import { awaitTerminalSize, disposeActiveXterm } from './xterm-lifecycle.mjs';

// resolveApiOrigin moved to ./api-origin.mjs in v0.5.4.

const apiOrigin = resolveApiOrigin();
const apiBase = `${apiOrigin}/api/v1`;

// The Help card's install snippet is rendered from the origin the operator actually opened the
// dashboard on. It used to hard-code one machine's LAN IP, which was wrong for every other reader
// (and published that address in a public repo). `apiOrigin` already resolves ?api= > stored
// override > this page's host, so the snippet matches whatever they are really talking to.
// renderInstallSnippet moved to ./static-links.mjs in v0.5.4.
// RUN_INSPECTOR_EVENT_LIMIT moved to ./run-helpers.mjs in v0.5.4.

// state moved to ./state.mjs in v0.5.4 — see that module for why the earlier measurement said it would not help.

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

// pages moved to ./page-titles.mjs in v0.5.4.

// byId moved to ./ui.js in v0.5.4 — it is a DOM lookup, and ui.js already owns the DOM helpers.
let refreshTimer = null;
// In-flight guard: refresh() fires a ~10-request bundle; refreshSoon() can be triggered by
// every WS event. Without this, under poll load (slow single-worker service) bundles pile up
// faster than they drain and saturate the browser's ~6-connection-per-origin limit — which
// starves lazily-loaded pages (e.g. Analytics) of their own fetches. Coalesce: at most one
// bundle in flight; if more arrive while it runs, run exactly one more afterwards.
let _refreshInFlight = false;
let _refreshQueued = false;
// dashboardSocket moved to ./realtime-socket.mjs in v0.5.4 — its only readers went with it.

// Chat-first landing controller (chat.js). Adapters bridge the pure module to app state:
// sendMessage routes DM→/messages/send (trigger+toast ladder) vs channel→/channels/{n}/send;
// loadConversation fetches a channel's messages; loadChannels refreshes the rail's channels.
// chatLoadChannels moved to ./message-transport.mjs in v0.5.4.
// chatLoadConversation moved to ./message-transport.mjs in v0.5.4.
// chatSendMessage moved to ./message-transport.mjs in v0.5.4.

// WS-I1/I2: per-message read/unread, unsend, and mark-conversation-read. The recipient for a
// read toggle is the viewing identity (POST /messages/{id}/read {agentId, read}).
async function markMessageRead(msgId, read) {
  try {
    await api(`/messages/${encodeURIComponent(msgId)}/read`, { method: 'POST', body: JSON.stringify({ agentId: state.chat.identity, read }) });
    const m = state.messages.find((x) => messageIdOf(x) === msgId);
    if (m) m.read = read;
    chatController.render();
  } catch (err) { toast(`Read update failed: ${err?.message || err}`, 'error'); }
}

async function unsendMessage(messageId) {
  if (!await uiConfirm('Unsend this message? It will be removed for the recipient.')) return;
  try {
    await api(`/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE' });
    state.messages = state.messages.filter((m) => messageIdOf(m) !== messageId);
    toast('Message unsent', 'ok');
    chatController.render();
    refreshSoon();
  } catch (err) { toast(`Unsend failed: ${err?.message || err}`, 'error'); }
}

async function markConversationRead(agentId, { quiet = false } = {}) {
  // Only messages addressed TO the viewing identity: state.messages is the fleet-wide feed, so
  // filtering by sender alone also grabbed the agent's messages to OTHER agents — the server
  // correctly 403s those (reader must be the recipient), spamming errors on every chat open.
  const me = state.chat.identity;
  const unread = state.messages.filter((m) =>
    String(m.from || '') === agentId && m.read === false
    && String(m.to || m.targetAgentId || m.target_agent_id || '') === me); // same fallback chain as chat.js unread count
  if (!unread.length) { if (!quiet) toast('Nothing unread', 'ok'); return; }
  try {
    await Promise.all(unread.map((m) => api(`/messages/${encodeURIComponent(messageIdOf(m))}/read`, { method: 'POST', body: JSON.stringify({ agentId: me, read: true }) })));
    unread.forEach((m) => { m.read = true; });
    if (!quiet) toast(`Marked ${unread.length} read`, 'ok');
    chatController.render();
  } catch (err) { toast(`Mark-read failed: ${err?.message || err}`, 'error'); }
}

// messageIdOf moved to ./record-fields.mjs in v0.5.4.

// Favorites (WS-F): PATCH /agents/{id}/favorite, optimistic so the rail re-sorts immediately.
async function toggleFavorite(agentId) {
  const agent = state.agents.find((a) => a.id === agentId);
  const next = !(agent && agent.favorited);
  if (agent) agent.favorited = next; // optimistic
  chatController.render();
  try {
    await api(`/agents/${encodeURIComponent(agentId)}/favorite`, { method: 'PATCH', body: JSON.stringify({ favorited: next }) });
  } catch (err) {
    if (agent) agent.favorited = !next; // revert
    chatController.render();
    toast(`Favorite failed: ${err?.message || err}`, 'error');
  }
}
const chatController = createChatController({
  state, byId,
  sendMessage: chatSendMessage,
  loadChannels: chatLoadChannels,
  refresh: () => refresh(),
  loadConversation: chatLoadConversation,
  loadAgentAnalytics: (id) => api(`/analytics/agent/${encodeURIComponent(id)}`),
  mountChatConsole: (agentId, hostEl) => mountChatConsole(agentId, hostEl),
  loadPulse: (mins) => api(`/analytics/pulse?window_minutes=${encodeURIComponent(mins)}`),
  persistDrafts: () => persistChatDrafts(),
  // Replying to a peer clears their unread badge — quiet, since the send already toasts.
  markConversationRead: (agentId, opts) => markConversationRead(agentId, opts),
  // Keep the details drawer pointed at whatever the operator just selected — otherwise its
  // lifecycle buttons act on the agent they navigated away from. See syncInspectorToSelection.
  onSelectionChange: () => syncInspectorToSelection(),
});

// Mount an agent's live console inline inside the Chat conversation pane. Reuses the exact
// Sessions terminal widget (PTY xterm / hermes iframe / codex synth / start-console offer).
// Signature-guarded: called on every render while Console is open, but only rebuilds the host
// when the resolved console actually changed — so a freshly-started console auto-appears while
// idle polls don't remount (and flicker) the live xterm.
function mountChatConsole(agentId, hostEl) {
  if (!hostEl) return;
  const session = sessionForAgent(agentId);
  const sig = session
    ? [sessionId(session), session.status || '', session.terminalStatus || session.terminal_status || '',
       agentForSession(session)?.runtimeState?.virtualTerminalId || '',
       // Include the auto-attach sources (2026-06-19 review) so a terminal that first goes live
       // via the top-level PTY / console pointer / session-bound id changes the sig and mounts
       // inline immediately, instead of lagging a poll until it lands in state.sessionTerminals.
       agentForSession(session)?.runtimeState?.terminalId || '',
       agentForSession(session)?.runtimeState?.consoleTerminal?.terminalId || '',
       session.terminalId || session.terminal?.id || session.terminal_id || '',
       (state.sessionTerminals?.get?.(sessionId(session))) || ''].join('|')
    : 'none';
  // Unchanged sig → leave the mounted widget alone — EXCEPT when the single global xterm now
  // lives in another host (the Sessions page re-mounts it) and THIS host is visible: that's the
  // "dead chat console after visiting Sessions" bug (review finding #2) — fall through so the
  // inner renderSessionConsole guard re-mounts it here. The visibility check keeps a hidden
  // chat host from stealing the xterm back while the operator is on the Sessions page, and
  // non-xterm widgets (hermes iframe / codex synth) are flicker-safe via the inner consoleKey
  // guard, which no-ops when nothing material changed.
  const xtermElsewhere = state.activeXterm && !hostEl.contains(state.activeXterm.container);
  if (hostEl.dataset.consoleSig === sig && !(xtermElsewhere && hostEl.offsetParent !== null)) return;
  hostEl.dataset.consoleSig = sig;
  if (!session) {
    disposeActiveXterm();
    const agent = (state.agents || []).find((a) => a.id === agentId);
    const resident = String(agent?.sessionMode || '').toLowerCase() === 'resident';
    hostEl.innerHTML = resident
      ? '<div class="empty-state"><span class="empty-icon">🖥️</span><strong>Resident agent</strong>'
        + `<p>${esc(agentId)} runs in its own CLI (a <code>${esc(agent?.runtime || 'runtime')}-aify</code> terminal you launched) — there's no dashboard-owned console to show here. Switch it to managed from <strong>Details</strong> to get one.</p></div>`
      // A managed agent with NO session row used to dead-end here: the start buttons all live
      // further down the session path, which this early return never reaches, so the ONLY way to
      // bring a cold agent up was to send it a message ("why can't I start hermes models?").
      // Cold-start itself was never broken — there was simply no button. Give it one.
      : '<div class="empty-state"><span class="empty-icon">🖥️</span><strong>No live console</strong>'
        + `<p>${esc(agentId)} has no worker running. Start one now — it resumes the agent's saved session if it has one, so its conversation is kept. (Sending a message also starts it.)</p>`
        + `<div class="console-start-actions"><button class="primary" data-agent-action="start" data-agent-id="${esc(agentId)}">Start agent</button></div></div>`;
    return;
  }
  renderSessionConsole(session, hostEl, { source: 'chat' });
}

// Shared files (Phase 1.4b): list/upload/delete artifacts via /shared.
// loadFiles moved to ./shared-files.mjs in v0.5.4.
// fileSizeLabel moved to ./util.js in v0.5.4.
// renderFiles moved to ./shared-files.mjs in v0.5.4.
// uploadSharedFile moved to ./shared-files.mjs in v0.5.4.
// WS-F: attach a file from the chat composer — upload to /shared, insert a reference into the body.
// attachChatFile moved to ./shared-files.mjs in v0.5.4.

// deleteSharedFile moved to ./shared-files.mjs in v0.5.4.

// Channels management (Phase 1.4): create/join/leave/read scoped to the viewing identity.
async function chatCreateChannel(name) {
  const clean = String(name || '').trim();
  if (!clean) return;
  await api('/channels', { method: 'POST', body: JSON.stringify({ name: clean, createdBy: state.chat.identity }) });
  await chatLoadChannels();
  state.chat.selected = `channel:${clean}`;
  try { await chatLoadConversation(clean); } catch (_) {}
  chatController.render();
  toast(`Created #${clean}`, 'ok');
}
async function chatChannelAction(action, name) {
  const identity = state.chat.identity;
  try {
    if (action === 'delete') {
      if (!await uiConfirm(`Delete channel #${name}? This removes the channel and its membership for everyone.`)) return;
      await api(`/channels/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (state.chat.selected === `channel:${name}`) chatController.close();
      await chatLoadChannels();
      chatController.render();
      toast(`Deleted #${name}`, 'ok');
      return;
    }
    if (action === 'join') await api(`/channels/${encodeURIComponent(name)}/join`, { method: 'POST', body: JSON.stringify({ agentId: identity }) });
    else if (action === 'leave') await api(`/channels/${encodeURIComponent(name)}/leave`, { method: 'POST', body: JSON.stringify({ agentId: identity }) });
    else if (action === 'read') await api(`/channels/${encodeURIComponent(name)}/read`, { method: 'POST', body: JSON.stringify({ agentId: identity }) });
    await chatLoadChannels();
    chatController.render();
    toast(`${action === 'read' ? 'Marked read' : action === 'join' ? 'Joined' : 'Left'} #${name}`, 'ok');
  } catch (err) { toast(`${action} failed: ${err?.message || err}`, 'error'); }
}

// I7: add/remove ANOTHER agent to/from a channel (join/leave take an agentId).
async function addChannelMember(name) {
  const sel = byId(`chat-add-member-${name}`);
  const agentId = sel?.value || '';
  if (!agentId) { toast('Pick an agent to add', 'warn'); return; }
  try {
    await api(`/channels/${encodeURIComponent(name)}/join`, { method: 'POST', body: JSON.stringify({ agentId }) });
    await chatLoadChannels();
    chatController.render();
    toast(`${agentId} added to #${name}`, 'ok');
  } catch (err) { toast(`Add member failed: ${err?.message || err}`, 'error'); }
}
async function removeChannelMember(name, agentId) {
  if (!await uiConfirm(`Remove ${agentId} from #${name}? They stop receiving fan-out; history remains.`, { tone: 'danger', confirmLabel: 'Remove' })) return;
  try {
    await api(`/channels/${encodeURIComponent(name)}/leave`, { method: 'POST', body: JSON.stringify({ agentId }) });
    await chatLoadChannels();
    chatController.render();
    toast(`${agentId} removed from #${name}`, 'ok');
  } catch (err) { toast(`Remove member failed: ${err?.message || err}`, 'error'); }
}

// statusWhyContext moved to ./status.js in v0.5.4.

function evaluateFlowGates() {
  Object.values(flowGates).forEach((gate) => {
    gate.enabled = Boolean(gate.assertion());
  });
  return flowGates;
}

// api moved to ./api-client.mjs in v0.5.4.
setApiBase(apiBase, apiOrigin);

// awaitTerminalSize moved to ./xterm-lifecycle.mjs in v0.5.4.

function refreshSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

// The realtime socket cluster — connect, resume-nudge, resume wiring and the four mutable names
// they own — moved to ./realtime-socket.mjs in v0.5.4. Its dependencies are supplied by the
// initRealtimeSocket call in this file's init block, which MUST run before the first connect.

// Desktop notifications. All decisions (is it for the operator, is the tab focused, has this
// already fired) live in notify.mjs where they are unit-tested — this file keeps only the wiring,
// because app.js is only reachable by source-regex tests that cannot fail on wrong logic.
let notificationsEnabled = readEnabled(typeof localStorage !== 'undefined' ? localStorage : null);
const dashboardNotifier = createNotifier({
  isEnabled: () => notificationsEnabled,
  isFocused: () => typeof document !== 'undefined' && document.visibilityState === 'visible',
  // Channel notifications are MEMBERSHIP-gated (review finding): the dashboard can see every
  // channel, not just the ones it joined, so "any channel_message" would notify on traffic the
  // operator never subscribed to. Reads the same `members` array the chat UI already uses for
  // join/leave. Returns false while the channel list is still loading — notify.mjs fails closed
  // on purpose, and this is the source of that "unknown".
  isChannelSubscribed: (channel) => {
    const list = (state.chat && state.chat.channels) || [];
    const row = list.find((c) => String(c && c.name) === String(channel));
    return !!(row && Array.isArray(row.members) && row.members.includes('dashboard'));
  },
});

async function toggleNotifications(on) {
  if (on) {
    // Must come from a user gesture — a page that asks on load gets denied permanently.
    const result = await requestPermission();
    if (result !== 'granted') {
      toast(result === 'denied'
        ? 'Notifications are blocked for this site — allow them in your browser settings.'
        : 'Notification permission was not granted.');
      return false;
    }
  }
  notificationsEnabled = !!on;
  writeEnabled(typeof localStorage !== 'undefined' ? localStorage : null, notificationsEnabled);
  return notificationsEnabled;
}

// applyRealtimeEvent moved to ./realtime-socket.mjs in v0.5.4, with the socket it is wired to.

// runQueryPath moved to ./run-helpers.mjs in v0.5.4.

// The base refresh fetches only OPEN contracts, so the State dropdown's terminal options
// (Answered/Failed/Missing reply/Seen/Sent/Closed) had nothing to match. Reload from the server
// with the matching scope on change so every option works. (2026-06-29 fix.)
async function loadContractsForState(stateVal, render = true) {
  const v = String(stateVal || '').trim();
  let qs = '/contracts?limit=120';
  if (v === 'all') qs = '/contracts?includeClosed=true&limit=300';
  else if (v && v !== 'open') qs = `/contracts?state=${encodeURIComponent(v)}&limit=200`;
  try { const res = await api(qs); state.contracts = res.contracts || []; } catch (err) { toast(`Load contracts failed: ${err?.message || err}`, 'error'); }
  if (render) renderContracts();
}

// loadRunsForStatus moved to ./run-inspector.mjs in v0.5.4.

// asAgentArray moved to ./record-fields.mjs in v0.5.4.

// asArray moved to ./record-fields.mjs in v0.5.4.

async function refresh() {
  // Coalesce concurrent refreshes so the poll bundle can't pile up (see _refreshInFlight).
  if (_refreshInFlight) { _refreshQueued = true; return; }
  _refreshInFlight = true;
  try {
    await _refreshImpl();
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
    isLoading: !!state.inspector?.loadingMore,
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

// filtered moved to ./work-loop-panels.mjs in v0.5.4.

// Single-item version of the top-bar global Find (for callers that do their own filtering).
// matchesGlobalFilter moved to ./work-loop-panels.mjs in v0.5.4.

// Phase 0.4 (DASHBOARD_REBUILD_PLAN §0.4): per-section render keyed on an input signature
// computed in ONE place, so renderAll (run on every 15s poll, every WS event, and every
// filter keystroke) only rewrites a section's innerHTML when its inputs actually changed —
// no needless re-render, no flicker. The session workspace + console are intentionally NOT
// gated here: they keep their own proven internal guards (the xterm remount guard +
// terminalId cache) which already preserve live PTY/scroll/focus state across refreshes.
// _sectionSig moved to ./render-memo.mjs in v0.5.4.
// renderSection moved to ./render-memo.mjs in v0.5.4.
// Compact, stable fingerprints of just the fields a section renders from.
// _agentSig moved to ./render-memo.mjs in v0.5.4.
// _contractSig moved to ./render-memo.mjs in v0.5.4.
// _runSig moved to ./render-memo.mjs in v0.5.4.
// _envSig moved to ./render-memo.mjs in v0.5.4.
// _spawnReqSig moved to ./render-memo.mjs in v0.5.4.
// _msgSig moved to ./render-memo.mjs in v0.5.4.
// _chatChanSig moved to ./render-memo.mjs in v0.5.4.
// _chatConvSig moved to ./render-memo.mjs in v0.5.4.

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
  renderSection('settings', [state.settings], renderSettings);
  // Keep the analytics page live while it's the active page (re-fetch on the poll cycle).
  if (byId('page-analytics')?.classList.contains('active')) loadAnalytics();
  // Keep the Fleet pulse live while it's the Chat landing view (no conversation open).
  if (byId('page-chat')?.classList.contains('active') && !state.chat.selected && !state.chat.analytics.agent) {
    chatController.refreshPulse();
  }
}

// Legacy setting mirror. Mode-switch chips are now always visible; ownership
// changes are manual-only and no longer gated by this setting.
// Settings parity (Phase 1.7): curated, GROUPED editor over PUT /settings (which merges a
// partial). Rare/advanced knobs stay on the classic dashboard. Each item: key + type
// (toggle/number/text/select). The effort selects use the standard tiers.
// EFFORT_OPTS moved to ./settings-panel.mjs in v0.5.4.
// Pi accepts an empty effort meaning "OMP default" — preserve that as a selectable option.
// PI_EFFORT_OPTS moved to ./settings-panel.mjs in v0.5.4.
// SETTINGS_SCHEMA moved to ./settings-panel.mjs in v0.5.4.


// Short tab labels for the settings tab bar (the full group names are long).
// SETTINGS_TAB_LABELS moved to ./settings-panel.mjs in v0.5.4.
// SETTINGS_TAB_DESC moved to ./settings-panel.mjs in v0.5.4.
// HELP_TAB moved to ./settings-panel.mjs in v0.5.4.

// One aligned field row: label (+hint) on the left, control on the right. Toggles render a real
// switch. The theme picker spans the full width (select + preview tiles). Same input ids +
// data-setting-* attrs as before so saveSettings/previewAppearance/theme tiles keep working.
// settingsFieldHtml moved to ./settings-fields.mjs in v0.5.4 (with themePreviewTilesHtml, which
// only it calls and which stays private there).

// activeSettingsTab moved to ./settings-panel.mjs in v0.5.4.

// Tabbed settings: one panel visible at a time (short page), but ALL schema panels stay in the
// DOM so Save collects every field regardless of the active tab. Help is its own tab and toggles
// the static help-band.
// renderSettings moved to ./settings-panel.mjs in v0.5.4.

// Read the (possibly unsaved) Appearance editor controls into a partial settings object.
// readAppearanceInputs moved to ./settings-panel.mjs in v0.5.4.

// Live-preview the Appearance editor without saving (theme tile, select, or color picker).
// previewAppearance moved to ./settings-panel.mjs in v0.5.4.

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
        // Clamp to the rendered min/max — the PUT /settings endpoint does no bounds validation,
        // so an out-of-range value would otherwise persist verbatim.
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

// loadAnalytics moved to ./analytics-page.mjs in v0.5.4, with its caching note.

// usageResetLabel moved to ./util.js in v0.5.4.
// usageFmtTokens moved to ./util.js in v0.5.4.
// renderUsagePools moved to ./analytics-page.mjs in v0.5.4, with the note on what a pool is.
// renderUsageConsumption moved to ./summary-tiles.mjs in v0.5.4.

// renderAnalyticsPage moved to ./analytics-page.mjs in v0.5.4.

// metric moved to ./summary-tiles.mjs in v0.5.4.

// renderMetrics moved to ./summary-tiles.mjs in v0.5.4.

// contractCard moved to ./work-loop-panels.mjs in v0.5.4.

// contractActionable moved to ./record-fields.mjs in v0.5.4.

// renderAttention moved to ./work-loop-panels.mjs in v0.5.4.

// diagnosticKey moved to ./work-loop-panels.mjs in v0.5.4.

// selectedDiagnostics moved to ./summary-tiles.mjs in v0.5.4.

// pruneDiagnosticSelection moved to ./work-loop-panels.mjs in v0.5.4.

// renderDiagnosticsSummary moved to ./summary-tiles.mjs in v0.5.4.

// Work-loop maintenance actions (parity with old dashboard's hygiene buttons).
// Both endpoints are safe to run idempotently; they create fallback records for
// terminal runs that never recorded a handoff / never marked their source read.
// MAINTENANCE_ACTIONS moved to ./work-loop-panels.mjs in v0.5.4.

async function runMaintenance(action) {
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

function renderDiagnosticsBulkToolbar() {
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

// activityItems moved to ./work-loop-panels.mjs in v0.5.4.

// renderActivityFeed moved to ./work-loop-panels.mjs in v0.5.4.

// _statusWhyReturnFocus moved to ./status-why-popover.mjs in v0.5.4.
// openStatusWhy moved to ./status-why-popover.mjs in v0.5.4.

// closeStatusWhy moved to ./status-why-popover.mjs in v0.5.4.

// sessionId moved to ./record-fields.mjs in v0.5.4.

// sessionAgentId moved to ./record-fields.mjs in v0.5.4.

// sessionEnvironmentId moved to ./record-fields.mjs in v0.5.4.

// sessionRuntime moved to ./record-fields.mjs in v0.5.4.

// agentForSession moved to ./session-rail.mjs in v0.5.4.

// groupedSessionsByEnvironment moved to ./session-rail.mjs in v0.5.4.

// selectedSessionIds moved to ./session-rail.mjs in v0.5.4.

// selectedSession moved to ./session-rail.mjs in v0.5.4.

// ensureSelectedSession moved to ./session-rail.mjs in v0.5.4.

// messagesForSession moved to ./session-activity.mjs in v0.5.4.

// Single source of truth lives in messageIdOf(); kept as an alias so existing call sites work.
// messageId moved to ./record-fields.mjs in v0.5.4.

// messageRunId moved to ./record-fields.mjs in v0.5.4.

// runTargetAgent moved to ./record-fields.mjs in v0.5.4.

// sessionForAgent moved to ./agent-drawer.mjs in v0.5.4.

// sessionForRun moved to ./run-inspector-controls.mjs in v0.5.4.

// runSourceMessage moved to ./run-helpers.mjs in v0.5.4.

// renderSessionBulkToolbar moved to ./session-rail.mjs in v0.5.4.

// WS-F: status multiselect filter chips for the Sessions rail.
// Proof-based 6-state model only — `idle`/`stale` were removed in the status rewrite, so they must
// not appear as session filter chips (dead chips that match nothing).
// H1: these were hand-copies of status_engine.VALID_STATUSES. They now alias the single JS
// owner in status.js, which is bound to the Python source by a test.
// SESSION_FILTER_KINDS moved to ./session-rail.mjs in v0.5.4.
// SESSION_LIVE_KINDS moved to ./session-click-handlers.mjs in v0.5.4.
// renderSessionStatusFilter moved to ./session-rail.mjs in v0.5.4.

// persistSessionStatusFilter moved to ./session-click-handlers.mjs in v0.5.4.

// renderSessionRail moved to ./session-rail.mjs in v0.5.4.

// Persisted collapse state for session env-groups (WS-J collapsibles).
// sessionGroupCollapsed moved to ./session-rail.mjs in v0.5.4.
// toggleSessionGroupCollapsed moved to ./layout-prefs.mjs in v0.5.4.

// Read-only Activity log for a session (WS-J): recent runs + messages, NO composer (messaging
// lives in Chat). Merges the agent's dispatch runs and message thread, newest first.
// renderSessionActivity moved to ./session-activity.mjs in v0.5.4.

// Convert a hermes tui_gateway WS URL into its sibling HTTP root URL.
// Input:  ws://127.0.0.1:1234/api/ws?token=abc
// Output: http://127.0.0.1:1234/?token=abc
// Returns "" if the input isn't a recognizable loopback ws:// URL.
// --- Real PTY rendering via xterm.js ---------------------------------
// When the bridge spawns a managed agent via TerminalProcessManager
// (managed-claude PTY today; codex-aify / hermes-aify PTY soon), the
// bytes flow into a `terminal_session` row and are broadcast as
// terminal_output WS events. This mounts an xterm.js instance into
// the Session Console pane and pipes those bytes straight in — the
// operator sees the REAL Ink TUI in their browser, not a synth
// translation or an iframe of upstream's web UI.

// disposeActiveXterm moved to ./xterm-lifecycle.mjs in v0.5.4.


// Terminal theme derived from the active dashboard theme/palette (Hermes-style live theming).
// The console keeps a DARK background+foreground on purpose — the agent TUIs (Ink, etc.) are
// designed for dark terminals and a light background would misrender them — but the cursor and
// selection follow the dashboard's accent so the console reads as part of the themed UI. Accent
// comes from the live `--accent` CSS var (honors a CUSTOM palette), falling back to the preset.
// terminalAccentColor moved to ./settings-panel.mjs in v0.5.4.
// terminalThemeFromDashboard moved to ./settings-panel.mjs in v0.5.4.
// Re-theme the mounted console on a live theme/palette change. Updating term.options.theme alone
// leaves STALE colors on screen under the WebGL renderer, which caches glyph colors in its texture
// atlas — so we clear the atlas too (exactly what Hermes does on a theme switch). No-op when no
// console is mounted or when the DOM renderer is active (no atlas to clear).
//
// CHANGE-GATED: this is called from the ~15s poll (_refreshImpl applies settings every tick), not
// only on real theme edits. clearTextureAtlas() forces a full glyph re-rasterization, so calling it
// every poll would flicker an open console. Only act when the accent actually changed.
// refreshActiveTerminalTheme moved to ./settings-panel.mjs in v0.5.4.

// The IMPLEMENTATION lives in ./xterm-mount.mjs, together with the two counters only it reads. This is
// the binding that supplies `resyncActiveConsole`, which stays here because it reaches `refresh`.
// Deliberately NOT phrased as a `moved to` marker: `moved-names-resolve` treats a marker plus a local
// declaration of the same name as a fork, and it is right to — this is a shim, not a move.
const mountXtermForTerminal = (terminalId, agentId, container, opts) =>
  mountXtermForTerminalImpl(terminalId, agentId, container, opts, { resyncActiveConsole });


// Which agent owns this terminal? (Used to decide whether the PTY is OURS to resize.)
// agentForTerminal moved to ./session-rail.mjs in v0.5.4.

// Apply the server's rendered width to the xterm.
//
// MANAGED PTYs are OURS — resize THEM to the pane, never the pane to them (2026-07-14).
// This used to widen the xterm to the PTY's width unconditionally. For a managed terminal that is
// exactly backwards, and it could never converge: the PTY is 100 cols, so we widened the xterm to
// 100 cols (700px) inside a 660px box — the console visibly failed to fill its box and scrolled
// sideways — and that widening fired `term.onResize`, which pushed the PTY back to 100 cols. The
// two chased each other forever. Operator: "now it is not even from side to side (wide as the
// console box)". Measured: host 660px, xterm screen 700px.
//
// So: if we own the PTY (managed), keep the xterm at the pane's fitted size and let `onResize`
// push that size down to the PTY — the app then re-renders to fit the box exactly (and that full
// re-render also clears any garbage the screen had inherited). The wide-mirror path stays ONLY for
// a RESIDENT console, where the terminal belongs to the operator and we must not resize it.
// applyRenderedWidth moved to ./terminal-width.mjs in v0.5.4.

// Re-fetch the authoritative buffer and repaint (used by the Refresh button and on a
// detected seq gap, mirroring the old dashboard's resync path).
async function resyncActiveConsole({ forceRepaint = false } = {}) {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  // A PTY resize produces a burst of repaint frames. Those frames can themselves
  // expose a transient seq gap, which used to start another resync and another
  // -1/+1 resize pair. Coalesce recovery so one gap cannot fan out into the
  // observed 153↔154-cols resize/flicker loop.
  if (entry.resyncing) return;
  entry.resyncing = true;
  try {
    // Fetch at the pane's FITTED width (not the possibly-widened current width) so the server
    // can re-infer the source width and hand back the correct renderedCols.
    const fetchCols = Math.max(20, entry.fitCols || entry.term.cols);
    // REFRESH MUST ACTUALLY FIX IT. The operator's complaint was "refresh does not actually fix
    // it" — and they were right: it re-rendered the SAME poisoned screen. These TUIs repaint only
    // what changed, so a screen with wrong rows keeps them forever; the ONLY thing that forces a
    // full repaint is a genuine PTY resize (verified live). So Refresh now nudges the size (-1
    // col, then back), which makes the app redraw everything, and THEN pulls the clean snapshot.
    if (forceRepaint && entry.ownsPty) {
      try {
        await forceTerminalRepaint({
          cols: fetchCols,
          rows: entry.term.rows,
          resize: (nextCols, nextRows) => api(`/terminals/${encodeURIComponent(entry.terminalId)}/resize`, {
            method: 'POST',
            body: JSON.stringify({ cols: nextCols, rows: nextRows, requestedBy: 'dashboard-refresh' }),
          }),
          waitForSize: (nextCols, nextRows) => awaitTerminalSize(entry.terminalId, nextCols, nextRows),
        });
        await new Promise((res) => setTimeout(res, 700));
      } catch { /* best-effort */ }
    }
    const data = await api(`/terminals/${encodeURIComponent(entry.terminalId)}?cols=${fetchCols}&rows=${entry.term.rows}`);
    // reset() (not clear()) wipes any scrambled scrollback/alt-screen state before we
    // repaint the clean server-rendered snapshot — so Refresh actually un-scrambles.
    entry.term.reset();
    applyRenderedWidth(entry, entry.term, entry.container, data, Boolean(entry.ownsPty));
    const snapshot = data?.terminal?.snapshot;
    entry.term.write(String(snapshot || data?.terminal?.output || ''));
    const snapshotSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? entry.lastSeq);
    entry.lastSeq = Math.max(Number(entry.lastSeq) || -1, Number.isFinite(snapshotSeq) ? snapshotSeq : -1);
  } catch { /* keep current buffer */ }
  finally { entry.resyncing = false; }
}

// Clipboard copy that works on the http loopback origin (navigator.clipboard is undefined
// there) — falls back to a hidden textarea + execCommand, ported from the old dashboard.
// copyText moved to ./clipboard.mjs in v0.5.4.

// copyActiveConsole moved to ./clipboard.mjs in v0.5.4.

async function stopConsoleTerminal(terminalId) {
  if (!terminalId) return;
  if (!await uiConfirm('Stop this terminal? The agent returns to messenger ownership.')) return;
  try {
    await api(`/terminals/${encodeURIComponent(terminalId)}/stop`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard', body: '' }) });
    disposeActiveXterm();
    toast('Console stopped', 'ok');
    refreshSoon();
  } catch (err) { toast(`Stop failed: ${err?.message || err}`, 'error'); }
}

async function startConsoleForSession(sessionId, freshContext = false) {
  if (!sessionId) return;
  try {
    await api(`/sessions/${encodeURIComponent(sessionId)}/console/start`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard', freshContext }) });
    toast(freshContext ? 'Starting fresh console…' : 'Starting console…', 'ok');
    refreshSoon();
  } catch (err) { toast(`Start console failed: ${err?.message || err}`, 'error'); }
}

// Best-effort "waiting for input" detector on the console tail (ported pure helper). Drives
// the ⌛ await-input pill so the operator notices a console blocked on a prompt.
// Operator feedback 2026-07-02: the old pattern also matched a bare prompt cursor (`❯`,
// trailing `>`), which the claude TUI shows PERMANENTLY — the pill was on almost all the
// time and meant nothing. Only real interactive QUESTIONS count now; the steady "ready for
// input" state is not an alert.
// consoleAwaitingInputHint moved to ./console-await.mjs in v0.5.4.

// updateAwaitPill moved to ./console-await.mjs in v0.5.4.

// --- Codex live-console widget --------------------------------------
// Connects directly to a codex app-server WS (browser → ws://127.0.0.1:<port>),
// subscribes to events on the agent's threadId via initialize + thread/resume,
// and renders agent message deltas + turn lifecycle markers into a div.
// Symmetric in intent with the hermes iframe embed, but built custom because
// codex has no upstream web UI to embed — we render the JSON-RPC event stream
// ourselves. Send a turn/start when the operator types in the input box.

// codexConsoleConnections moved to ./codex-console.mjs in v0.5.4.

// Don't leak codex console sockets across an unload/navigation.
window.addEventListener('beforeunload', () => { codexConsoleConnections.forEach((e) => { try { e.ws?.close(); } catch {} }); });
// codexConsoleClose moved to ./codex-console.mjs in v0.5.4.

// codexConsoleAppendLine moved to ./codex-console.mjs in v0.5.4.

// codexConsoleAppendText moved to ./codex-console.mjs in v0.5.4.

// codexConsoleConnect moved to ./codex-console.mjs in v0.5.4.

// codexConsoleSendTurn moved to ./codex-console.mjs in v0.5.4.

// Manual resident<->managed mode-switch chip. Ownership changes are
// operator-driven only, so the switch is always visible for valid agents.
// renderModeSwitchChip moved to ./session-rail.mjs in v0.5.4.

// Optional inline label so operators can see the current sessionMode at a
// glance in the session header subtitle. Informational only.
// renderSessionModeLabel moved to ./session-rail.mjs in v0.5.4.

// switchAgentSessionMode moved to ./agent-session-actions.mjs in v0.5.4.

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
  byId('session-subtitle').textContent = session.workspace || session.cwd || 'Live terminal and lifecycle for this session.';
  byId('session-status').innerHTML = renderStatusChip(session.status || agentForSession(session).status || 'unknown', statusWhyContext('session', session, session.status || agentForSession(session).status || 'unknown'));
  renderSessionActivity(session);
  renderSessionConsole(session);
}

// (Phase 0.2 dead-code removal, 2026-06-16) renderAgents / renderMessages /
// renderConversations were never called by renderAll and targeted DOM ids that
// don't exist in index.html (agent-list / message-list / conversation-list) — they
// would null-crash if wired naively. Their data (agents/messages/conversations) is
// surfaced by the session rail + chat workspace instead. Removed; their landing surface
// returns as the chat-first slice (Phase 1).

// contractCategory moved to ./record-fields.mjs in v0.5.4.
// Work Loop board columns, ordered needs-attention → done. Each contract lands in
// the FIRST column whose match() is true (so `overdue` — a flag layered on any live
// state — always wins its urgency slot). `always` columns render even when empty so
// the board shape is stable in the default Open filter; terminal columns only appear
// when they actually hold cards (or when the State filter loaded them).
// CONTRACT_BOARD_COLUMNS moved to ./work-loop-panels.mjs in v0.5.4.

// renderContractBoard moved to ./work-loop-panels.mjs in v0.5.4.

function renderContracts() {
  const selected = byId('contract-state')?.value || 'open';
  const category = byId('contract-category')?.value || '';
  const contracts = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])
    .filter((contract) => selected === 'all' ? true
      : selected === 'open' ? ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state)
      : contract.state === selected)
    .filter((contract) => !category || contractCategory(contract) === category);
  const host = byId('contract-list');
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

// environmentRuntimes moved to ./record-fields.mjs in v0.5.4.

// environmentRoots moved to ./record-fields.mjs in v0.5.4.

// renderEnvironmentSummary moved to ./environments-panels.mjs in v0.5.4.

// renderEnvironmentSpawnOptions moved to ./environments-panels.mjs in v0.5.4.

// renderRuntime moved to ./environments-panels.mjs in v0.5.4.

// Spawn-requests queue/history (ported from 8800 renderSpawnRequests): surfaces queued/
// claimed/failed/done spawn requests on the Environments page so failed or stuck spawns have
// somewhere to appear. Reads GET /spawn-requests (loaded into state.spawnRequests on refresh).
// `done` is the one spawn status the canonical resolver doesn't know — alias it to completed.
// renderSpawnRequests moved to ./environments-panels.mjs in v0.5.4.

async function controlEnvironment(environmentId, action) {
  if ((action === 'stop' || action === 'forget') && !await uiConfirm(`${action === 'stop' ? 'Stop the bridge process' : 'Forget this environment'} "${environmentId}"?`)) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/control`, { method: 'POST', body: JSON.stringify({ action, requestedBy: 'dashboard' }) });
    toast(`Environment ${action} requested`, 'ok');
    refreshSoon();
  } catch (err) { toast(`Environment ${action} failed: ${err?.message || err}`, 'error'); }
}

// H4 — workspace-roots editor (parity with old dashboard's environment editor).
// Roots gate which cwd an agent may be spawned into. A dashboard override persists
// until "Reset to bridge roots" restores whatever the bridge process advertises.
// Build the host start command for an environment (ported from old dashboard
// environmentStartCommand) — the one-liner an operator runs to bring a dead bridge back.
// environmentStartCommand moved to ./environment-start-command.mjs in v0.5.4.

// openEnvironmentRootsEditor moved to ./environments-panels.mjs in v0.5.4.

async function submitEnvironmentRoots(environmentId) {
  const text = byId('env-edit-roots')?.value || '';
  const roots = text.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean);
  if (!roots.length) { toast('At least one root is required. Use “Reset to bridge roots” to restore advertised roots.', 'warn'); return; }
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ roots, requestedBy: 'dashboard' }) });
    toast('Workspace roots updated', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Root update failed: ${err?.message || err}`, 'error'); }
}

async function resetEnvironmentRoots(environmentId) {
  if (!await uiConfirm(`Reset "${environmentId}" to the roots advertised by its bridge process?`)) return;
  try {
    await api(`/environments/${encodeURIComponent(environmentId)}/roots`, { method: 'PATCH', body: JSON.stringify({ resetToBridgeAdvertised: true, requestedBy: 'dashboard' }) });
    toast('Workspace roots reset to bridge-advertised', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Root reset failed: ${err?.message || err}`, 'error'); }
}

// runFrom moved to ./session-activity.mjs in v0.5.4.
// runTo and runRuntime moved to ./run-inspector.mjs in v0.5.4 — their only readers went with it.

// Populate a filter <select> with distinct values, preserving the current selection.
// syncRunFilterOptions moved to ./run-helpers.mjs in v0.5.4.

// renderRuns moved to ./run-inspector.mjs in v0.5.4.

// (Phase 0.2 dead-code removal, 2026-06-16) renderAnalytics was never called and targeted
// analytics-grid / run-status-mix (absent from index.html). The analytics surface returns as
// a tab on the Control Room slice (Phase 1) consuming GET /analytics + GET /analytics/agent/{id}.

// loadRunDetails moved to ./run-helpers.mjs in v0.5.4.

// loadRunEvents moved to ./run-helpers.mjs in v0.5.4.

// runStatusContext moved to ./status.js in v0.5.4.

// runInspectorCapabilities moved to ./run-inspector-controls.mjs in v0.5.4.

// runPendingControlCount moved to ./record-fields.mjs in v0.5.4.

// renderEventBody moved to ./run-event.mjs in v0.5.4.

// renderRunEvent moved to ./run-event.mjs in v0.5.4.

// renderRunInspectorControls moved to ./run-inspector-controls.mjs in v0.5.4.

// renderRunInspector moved to ./run-inspector.mjs in v0.5.4.

// openRunInspector moved to ./run-inspector.mjs in v0.5.4.

async function inspect(kind, payload) {
  if (kind === 'run') {
    await openRunInspector({ runId: payload, source: 'generic' });
    return;
  }
  const data = typeof payload === 'string'
    ? lookup(kind, payload)
    : payload;
  state.inspector = { kind, runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' };
  byId('inspector-content').innerHTML = `<pre>${esc(JSON.stringify(data || {}, null, 2))}</pre>`;
  openInspector();
}

// Identity Directory (ported from 8800): an operator audit of every agent identity —
// role, runtime, session mode (resident/managed), bound environment, live status, and unread.
// Identity is the stable mailbox/role/routing behind chat; runtime control lives on Sessions.
// Renders into the shared inspector drawer (same pattern as the agent/history drawers). The
// per-row "Details" reuses openAgentDrawer; "Remove" reuses removeAgent (DELETE /agents/{id})
// — the only cleanup affordance the backend exposes for forgetting an offline CLI identity.
// openIdentityDirectory moved to ./identity-directory.mjs in v0.5.4.

// Agent-detail drawer (Phase 1.3): ONE drawer (the shared inspector) surfacing an agent's
// session/runtime/status + the key lifecycle actions, reusing the existing control functions
// — no duplicated action surface (the 8800 triplication the plan kills). Reachable from chat.
// Continue-in-CLI: the command to resume this agent's pinned native session in the
// operator's own terminal (mirror of the 8800 dashboard resume-command). Empty when
// there's no saved handle or the runtime has no resident resume (pi/opencode are
// managed-only). Linux/WSL shell form.
// Thin adapters over cli-resume.mjs (pure + unit-tested there). The runtime/agent-id accessors are
// injected because they live in this module.
// continueCliDetails moved to ./cli-resume.mjs in v0.5.4.

// continueCliCommand moved to ./cli-resume.mjs in v0.5.4.

// openAgentDrawer moved to ./agent-drawer.mjs in v0.5.4.

// Keep the details drawer in step with the conversation selection (2026-07-26, operator request:
// "when i click on another agent then details panel should close or it should switch to that
// selected agent"). Previously the drawer was opened once and never followed the selection, so it
// sat there describing the agent you had navigated AWAY from — which is worse than closing,
// because every lifecycle button in it (Stop worker, Restart, Reset, Delete session) then targets
// the wrong agent. Switching is the safer of the two behaviours the operator offered.
//  - selecting a DIFFERENT agent DM   → re-render the drawer for that agent
//  - selecting a channel / closing    → close the drawer (a channel has no agent lifecycle)
// No-op unless the drawer is actually open on an agent, so run/history drawers are untouched.
// syncInspectorToSelection moved to ./agent-drawer.mjs in v0.5.4.

// I9 — compaction / continuation lineage, derived from spawn records (metadata.continuedFrom*).
// openCompactionHistory moved to ./inspector-forms.mjs in v0.5.4.

// I3 — edit agent identity: rename, description, native session handle.
// openAgentEditForm moved to ./inspector-forms.mjs in v0.5.4.

// submitAgentEdit moved to ./agent-session-actions.mjs in v0.5.4.

// resolveAgentSession moved to ./agent-session-actions.mjs in v0.5.4, with its sticky-identity note.

// F8 — message detail surface in the inspector.
// openMessageDetail moved to ./inspector-forms.mjs in v0.5.4.

// F1 — Compact / Continue-as (handoff packet UX). Build a packet from recent messages and
// render an editable continuation form into the inspector; submit creates a managed-warm
// spawn-request seeded with the packet (POST /spawn-requests), same mechanism as 8800.
// buildHandoffPacket moved to ./inspector-forms.mjs in v0.5.4.

// openContinueForm moved to ./inspector-forms.mjs in v0.5.4.

// submitContinue moved to ./agent-session-actions.mjs in v0.5.4.

// removeAgent moved to ./agent-session-actions.mjs in v0.5.4.

// stopAgentWorker moved to ./agent-session-actions.mjs in v0.5.4, with the six lines explaining why it is confirmed.

// deleteSessionById moved to ./agent-session-actions.mjs in v0.5.4.

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

// setNavCollapsed moved to ./layout-prefs.mjs in v0.5.4.

// preferredNavCollapsed moved to ./layout-prefs.mjs in v0.5.4.

// requestRunControl moved to ./run-inspector.mjs in v0.5.4.

// requestSessionControl moved to ./agent-session-actions.mjs in v0.5.4.

// requestBulkSessionControl moved to ./agent-session-actions.mjs in v0.5.4.

// patchRun moved to ./run-helpers.mjs in v0.5.4.

async function closeWorkContract(runId, confirmAction = true, refreshAfter = true) {
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

async function remindWorkContract(runId, refreshAfter = true) {
  await api(`/contracts/reminders/run?runId=${encodeURIComponent(runId)}`, { method: 'POST' });
  if (refreshAfter) await refresh();
}

async function requestBulkDiagnosticAction(action) {
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

async function createSpawnRequest() {
  const environmentId = byId('env-spawn-environment')?.value || '';
  const runtime = byId('env-spawn-runtime')?.value || '';
  const agentId = byId('env-spawn-agent-id')?.value.trim() || '';
  const role = byId('env-spawn-role')?.value || 'coder';
  const workspace = byId('env-spawn-workspace')?.value.trim() || '';
  const initialMessage = byId('env-spawn-prompt')?.value.trim() || '';
  if (!environmentId || !runtime || !agentId || !workspace) {
    toast('Need environment, runtime, agent ID, and workspace.', 'warn');
    return;
  }
  const result = await api('/spawn-requests', {
    method: 'POST',
    body: JSON.stringify({
      createdBy: 'dashboard',
      environmentId,
      agentId,
      role,
      runtime,
      workspace,
      initialMessage,
      subject: initialMessage ? `Spawn ${agentId}` : '',
      mode: 'managed-warm',
    }),
  });
  byId('env-spawn-agent-id').value = '';
  byId('env-spawn-prompt').value = '';
  inspect('spawn-request', result.spawnRequest || result);
  await refresh();
}

// WS-J: open a message's thread in the real Chat page (not the removed Sessions composer).
function openMessageThread(messageIdValue) {
  const message = state.messages.find((item) => messageId(item) === String(messageIdValue));
  if (!message) return;
  const agentId = message.from === 'dashboard' ? message.to : message.from;
  openAgentChat(agentId);
}

// openAgentChat moved to ./agent-session-actions.mjs in v0.5.4.

function openRunConsole(run) {
  const session = sessionForRun(run);
  if (!session) return;
  state.selectedSessionId = sessionId(session);
  state.selectedConversation = sessionAgentId(session) || 'dashboard';
  state.selectedSessionTab = 'console';
  setPage('sessions');
  renderSessionWorkspace();
  closeInspector();
}

// sendRunFollowup moved to ./message-transport.mjs in v0.5.4.

// handleRunInspectorControl moved to ./run-inspector.mjs in v0.5.4.

// loadMoreRunEvents moved to ./run-inspector.mjs in v0.5.4.

// toggleRunEventOrder moved to ./run-inspector.mjs in v0.5.4.

// sendMessageWithTimeout moved to ./message-transport.mjs in v0.5.4.

// pastedImageName moved to ./shared-files.mjs in v0.5.4.

// uploadPastedImage moved to ./shared-files.mjs in v0.5.4.

// lookup moved to ./record-lookup.mjs in v0.5.4.

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

// updateStaticLinks moved to ./static-links.mjs in v0.5.4.

document.addEventListener('click', (event) => {
  const settingsTab = event.target.closest('[data-settings-tab]');
  if (settingsTab) {
    selectSettingsTab(settingsTab);
    return;
  }
  const themeChoice = event.target.closest('[data-theme-choice]');
  if (themeChoice) {
    applyThemeChoice(themeChoice);
    return;
  }
  const favToggle = event.target.closest('[data-fav-toggle]');
  if (favToggle) {
    toggleFavouriteRow(favToggle, event, toggleFavorite);
    return;
  }
  const msgRead = event.target.closest('[data-msg-read]');
  if (msgRead) { markMessageRead(msgRead.dataset.msgRead, msgRead.dataset.read === '0'); return; }
  const msgUnsend = event.target.closest('[data-msg-unsend]');
  if (msgUnsend) { unsendMessage(msgUnsend.dataset.msgUnsend); return; }
  const markConvRead = event.target.closest('[data-mark-conv-read]');
  if (markConvRead) { markConversationRead(markConvRead.dataset.markConvRead); return; }
  const chatReply = event.target.closest('[data-chat-reply]');
  if (chatReply) {
    openChatReply(chatReply, chatController);
    return;
  }
  if (event.target.closest('[data-chat-reply-clear]')) {
    state.chat.replyTo = null;
    chatController.renderConversation();
    return;
  }
  const msgDetail = event.target.closest('[data-message-detail]');
  if (msgDetail) {
    openMessageDetail(msgDetail.dataset.messageDetail);
    return;
  }
  const chatOpen = event.target.closest('[data-chat-open]');
  if (chatOpen) {
    openChatConversation(chatOpen, chatController, markConversationRead);
    return;
  }
  const pulseWindow = event.target.closest('[data-pulse-window]');
  if (pulseWindow) {
    setPulseWindow(pulseWindow, chatController);
    return;
  }
  const chatView = event.target.closest('[data-chat-view]');
  if (chatView) {
    setChatView(chatView, chatController);
    return;
  }
  // MUST stay scoped to button[...]: the grid section itself carries data-work-view as a
  // CSS state attribute, so a bare [data-work-view] closest() matches EVERY click inside
  // Work and swallows Inspect/Remind/Close (live regression 2026-07-02).
  const workView = event.target.closest('button[data-work-view]');
  if (workView) {
    applyWorkView(workView);
    return;
  }
  // Work Loop List ⇄ Board layout toggle. Scoped to button[data-contract-view] for
  // the same reason as work-view above (avoid swallowing card actions).
  const contractView = event.target.closest('button[data-contract-view]');
  if (contractView) {
    applyContractView(contractView, renderContracts);
    return;
  }
  const diagJump = event.target.closest('[data-diag-jump]');
  if (diagJump) {
    jumpFromDiagnostic(diagJump);
    return;
  }
  const chatAnalytics = event.target.closest('[data-chat-analytics]');
  if (chatAnalytics) {
    chatController.openAnalytics(chatAnalytics.dataset.chatAnalytics);
    return;
  }
  const agentDrawer = event.target.closest('[data-agent-drawer]');
  if (agentDrawer) {
    openAgentDrawer(agentDrawer.dataset.agentDrawer);
    return;
  }
  const agentControl = event.target.closest('[data-agent-control]');
  if (agentControl) {
    runAgentControl(agentControl, requestSessionControl);
    return;
  }
  const agentMode = event.target.closest('[data-agent-mode]');
  if (agentMode) {
    switchAgentModeFromRow(agentMode, switchAgentSessionMode);
    return;
  }
  const agentOpenSessions = event.target.closest('[data-agent-open-sessions]');
  if (agentOpenSessions) {
    openAgentSessions(agentOpenSessions, renderSessionWorkspace, setPage, closeInspector);
    return;
  }
  const toggleSuperseded = event.target.closest('[data-toggle-superseded]');
  if (toggleSuperseded) {
    toggleSupersededSessions();
    return;
  }
  const sessionStatusPreset = event.target.closest('[data-session-status-preset]');
  if (sessionStatusPreset) {
    applySessionStatusPreset(sessionStatusPreset, renderSessionWorkspace);
    return;
  }
  const sessionStatusFilter = event.target.closest('[data-session-status-filter]');
  if (sessionStatusFilter) {
    toggleSessionStatusFilter(sessionStatusFilter, renderSessionWorkspace);
    return;
  }
  const agentCompact = event.target.closest('[data-agent-compact]');
  if (agentCompact) { openContinueForm(agentCompact.dataset.agentCompact, false); return; }
  const agentContinue = event.target.closest('[data-agent-continue]');
  if (agentContinue) { openContinueForm(agentContinue.dataset.agentContinue, true); return; }
  const continueSubmit = event.target.closest('[data-continue-submit]');
  if (continueSubmit) { submitContinue(continueSubmit.dataset.continueSubmit, continueSubmit.dataset.split === '1'); return; }
  const agentEdit = event.target.closest('[data-agent-edit]');
  if (agentEdit) { openAgentEditForm(agentEdit.dataset.agentEdit); return; }
  const agentHistory = event.target.closest('[data-agent-history]');
  if (agentHistory) { openCompactionHistory(agentHistory.dataset.agentHistory); return; }
  const agentEditSubmit = event.target.closest('[data-agent-edit-submit]');
  if (agentEditSubmit) { submitAgentEdit(agentEditSubmit.dataset.agentEditSubmit); return; }
  const sessionConfirm = event.target.closest('[data-session-confirm]');
  if (sessionConfirm) { resolveAgentSession(sessionConfirm.dataset.sessionConfirm, 'confirm'); return; }
  const sessionKeep = event.target.closest('[data-session-keep]');
  if (sessionKeep) { resolveAgentSession(sessionKeep.dataset.sessionKeep, 'keep'); return; }
  const copyCli = event.target.closest('[data-copy-cli]');
  if (copyCli) { copyText(copyCli.dataset.copyCli || '').then((ok) => toast(ok ? 'Resume command copied' : 'Copy failed', ok ? 'ok' : 'error')); return; }
  const agentDetails = event.target.closest('[data-agent-details]');
  if (agentDetails) { openAgentDrawer(agentDetails.dataset.agentDetails); return; }
  const agentStopWorker = event.target.closest('[data-agent-stop-worker]');
  if (agentStopWorker) { stopAgentWorker(agentStopWorker.dataset.agentStopWorker); return; }
  const agentRemove = event.target.closest('[data-agent-remove]');
  if (agentRemove) { removeAgent(agentRemove.dataset.agentRemove); return; }
  const agentDeleteSession = event.target.closest('[data-agent-delete-session]');
  if (agentDeleteSession) { deleteSessionById(agentDeleteSession.dataset.agentDeleteSession); return; }
  const chanAction = event.target.closest('[data-chat-channel-action]');
  if (chanAction) {
    runChannelAction(chanAction, chatChannelAction);
    return;
  }
  const chanAddMember = event.target.closest('[data-channel-add-member]');
  if (chanAddMember) { addChannelMember(chanAddMember.dataset.channelAddMember); return; }
  const chanRemoveMember = event.target.closest('[data-channel-remove-member]');
  if (chanRemoveMember) { removeChannelMember(chanRemoveMember.dataset.channelRemoveMember, chanRemoveMember.dataset.member); return; }
  const fileDelete = event.target.closest('[data-file-delete]');
  if (fileDelete) {
    deleteSharedFileFromRow(fileDelete);
    return;
  }
  const openHermesTab = event.target.closest('[data-action="open-hermes-tab"]');
  if (openHermesTab) {
    openHermesTabFromRow(openHermesTab);
    return;
  }
  const codexConnect = event.target.closest('[data-action="codex-console-connect"]');
  if (codexConnect) {
    codexConsoleConnect(
      codexConnect.dataset.agentId,
      codexConnect.dataset.appServerUrl,
      codexConnect.dataset.threadId,
    );
    return;
  }
  const codexDisconnect = event.target.closest('[data-action="codex-console-disconnect"]');
  if (codexDisconnect) {
    codexConsoleClose(codexDisconnect.dataset.agentId);
    return;
  }
  const statusWhy = event.target.closest('[data-status-why]');
  if (statusWhy) {
    openStatusWhy(statusWhy);
    return;
  }
  if (event.target.closest('[data-close-status-why]')) {
    closeStatusWhy();
    return;
  }
  const consoleAction = event.target.closest('[data-console-action]');
  if (consoleAction) {
    runConsoleAction(consoleAction, resyncActiveConsole, stopConsoleTerminal, startConsoleForSession);
    return;
  }
  // Start a managed agent that has NO session at all (the cold-agent case — there was no way to
  // do this from the dashboard before). Spawns a worker through the same path a send uses, so a
  // saved session handle is RESUMED, not discarded.
  const agentAction = event.target.closest('[data-agent-action="start"]');
  if (agentAction) {
    startColdAgent(agentAction, refreshSoon);
    return;
  }
  const analyticsRange = event.target.closest('[data-analytics-range]');
  if (analyticsRange) {
    selectAnalyticsRange(analyticsRange, loadAnalytics);
    return;
  }
  const page = event.target.closest('[data-page], [data-page-jump]')?.dataset.page || event.target.closest('[data-page-jump]')?.dataset.pageJump;
  if (page) {
    navigateToPage(page, setPage, loadAnalytics);
    return;
  }
  const diagnosticSelect = event.target.closest('[data-diagnostic-select]');
  if (diagnosticSelect) {
    toggleDiagnosticSelection(diagnosticSelect, renderDiagnosticsBulkToolbar);
    return;
  }
  const diagnosticAction = event.target.closest('[data-diagnostic-action]');
  if (diagnosticAction) {
    requestBulkDiagnosticAction(diagnosticAction.dataset.diagnosticAction);
    return;
  }
  const maintAction = event.target.closest('[data-maint-action]');
  if (maintAction) {
    runMaintenance(maintAction.dataset.maintAction);
    return;
  }
  const envSpawn = event.target.closest('[data-env-spawn]');
  if (envSpawn) {
    openEnvironmentSpawn(envSpawn, setPage, renderEnvironmentSpawnOptions);
    return;
  }
  const envRoots = event.target.closest('[data-env-roots]');
  if (envRoots) { openEnvironmentRootsEditor(envRoots.dataset.envRoots); return; }
  const envRootsSubmit = event.target.closest('[data-env-roots-submit]');
  if (envRootsSubmit) { submitEnvironmentRoots(envRootsSubmit.dataset.envRootsSubmit); return; }
  const envRootsReset = event.target.closest('[data-env-roots-reset]');
  if (envRootsReset) { resetEnvironmentRoots(envRootsReset.dataset.envRootsReset); return; }
  const copyTextBtn = event.target.closest('[data-copy-text]');
  if (copyTextBtn) { copyText(copyTextBtn.dataset.copyText).then((ok) => toast(ok ? 'Copied to clipboard' : 'Copy failed', ok ? 'ok' : 'error')); return; }
  const envControl = event.target.closest('[data-env-control]');
  if (envControl) { controlEnvironment(envControl.dataset.envId, envControl.dataset.envControl); return; }
  const openChat = event.target.closest('[data-open-chat]');
  if (openChat) { openAgentChat(openChat.dataset.openChat); return; }
  const sessionCheckbox = event.target.closest('[data-session-checkbox]');
  if (sessionCheckbox) {
    toggleSessionCheckbox(sessionCheckbox, renderSessionWorkspace);
    return;
  }
  // Mode-switch chips can live inside selectable session rows. Handle them
  // before row selection so the click reaches PATCH /agents/{id}/session-mode.
  const modeSwitchButton = event.target.closest('[data-mode-switch]');
  if (modeSwitchButton) {
    switchModeFromChip(modeSwitchButton, event, switchAgentSessionMode);
    return;
  }
  const sessionSelect = event.target.closest('[data-session-select]');
  if (sessionSelect) {
    selectSessionRow(sessionSelect, renderSessionWorkspace);
    return;
  }
  const sessionTab = event.target.closest('[data-session-tab]');
  if (sessionTab) {
    selectSessionTab(sessionTab, renderSessionWorkspace);
    return;
  }
  const bulkSessionButton = event.target.closest('[data-bulk-session-action]');
  if (bulkSessionButton) {
    requestBulkSessionControl(bulkSessionButton.dataset.bulkSessionAction);
    return;
  }
  const runChip = event.target.closest('[data-run-chip]');
  if (runChip) {
    openRunInspector({ runId: runChip.dataset.runChip, source: 'chat', sourceMessageId: runChip.dataset.messageId || '' });
    return;
  }
  const runInspectorButton = event.target.closest('[data-run-inspector]');
  if (runInspectorButton) {
    openRunInspector({ runId: runInspectorButton.dataset.runInspector, source: runInspectorButton.dataset.runSource || 'programmatic' });
    return;
  }
  const runControlButton = event.target.closest('[data-run-control]');
  if (runControlButton) {
    handleRunInspectorControl(runControlButton.dataset.runControl);
    return;
  }
  const copyRunButton = event.target.closest('[data-copy-run-id]');
  if (copyRunButton) {
    // Use the execCommand-fallback copy (navigator.clipboard is undefined on the http LAN origin).
    copyText(copyRunButton.dataset.copyRunId || '').then((ok) => toast(ok ? 'Run ID copied' : 'Copy failed', ok ? 'ok' : 'error'));
    return;
  }
  const threadButton = event.target.closest('[data-open-thread-message]');
  if (threadButton) {
    openMessageThread(threadButton.dataset.openThreadMessage);
    return;
  }
  if (event.target.closest('#run-inspector-load-more')) {
    loadMoreRunEvents();
    return;
  }
  if (event.target.closest('#run-inspector-order-toggle')) {
    toggleRunEventOrder();
    return;
  }
  const closeContractButton = event.target.closest('[data-close-contract]');
  if (closeContractButton) { closeWorkContract(closeContractButton.dataset.closeContract); return; }
  const remindContractButton = event.target.closest('[data-remind-contract]');
  if (remindContractButton) { remindWorkContract(remindContractButton.dataset.remindContract); return; }
  const steerRunButton = event.target.closest('[data-steer-run]');
  if (steerRunButton) { requestRunControl(steerRunButton.dataset.steerRun); return; }
  const sessionControlButton = event.target.closest('[data-session-control]');
  if (sessionControlButton) {
    requestSessionControl(sessionControlButton.dataset.sessionId, sessionControlButton.dataset.sessionControl);
    return;
  }
  // (Removed the catch-all [data-kind] → JSON-inspector fallback: it hijacked clicks on the
  // empty area of any row/message and popped raw JSON. Explicit inspect buttons still work.)
});

document.addEventListener('keydown', (event) => {
  handleGlobalKeydown(event, closeInspector, toggleFavorite);
});

byId('refresh').addEventListener('click', refresh);

// Notification toggle. The click IS the user gesture the Notification permission prompt requires —
// asking on load gets a site denied permanently, so this is the only place permission is requested.
(() => {
  const btn = byId('notify-toggle');
  if (!btn) return;
  const paint = () => {
    btn.textContent = notificationsEnabled ? '🔔 Notify' : '🔕 Notify';
    btn.setAttribute('aria-pressed', notificationsEnabled ? 'true' : 'false');
    btn.classList.toggle('active', notificationsEnabled);
  };
  paint();
  btn.addEventListener('click', async () => {
    const on = await toggleNotifications(!notificationsEnabled);
    paint();
    if (on) toast('Desktop notifications on — messages addressed to you, when this tab is not focused', 'ok');
    else if (notificationsEnabled === false) toast('Desktop notifications off');
  });
})();
byId('global-filter').addEventListener('input', (event) => {
  state.filter = event.target.value;
  renderAll();
  renderSessionWorkspace(); // WS-H6: Find also narrows the Sessions rail
});
// Persist session env-group collapse (WS-J). `toggle` doesn't bubble → capture phase.
document.addEventListener('toggle', (event) => {
  const grp = event.target.closest?.('[data-env-group]');
  if (grp) toggleSessionGroupCollapsed(grp.dataset.envGroup, !grp.open);
}, true);
byId('contract-state')?.addEventListener('change', (event) => loadContractsForState(event.target.value));
byId('contract-category')?.addEventListener('change', renderContracts);
byId('run-status-filter')?.addEventListener('change', async (event) => {
  byId('api-status').textContent = 'filtering';
  byId('api-status').className = 'status-chip muted';
  try {
    await loadRunsForStatus(event.target.value);
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
  } catch (error) {
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
    toast(`Run filter failed: ${error?.message || error}`, 'error');
  }
});
byId('run-from-filter')?.addEventListener('change', (e) => { state.runFromFilter = e.target.value; renderRuns(); });
byId('run-to-filter')?.addEventListener('change', (e) => { state.runToFilter = e.target.value; renderRuns(); });
byId('run-runtime-filter')?.addEventListener('change', (e) => { state.runRuntimeFilter = e.target.value; renderRuns(); });
byId('run-search')?.addEventListener('input', (e) => { state.runSearch = e.target.value; renderRuns(); });
byId('env-spawn-environment')?.addEventListener('change', (event) => {
  byId('env-spawn-workspace').value = '';
  renderEnvironmentSpawnOptions(event.target.value);
});
byId('environment-spawn-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await createSpawnRequest();
  } catch (error) {
    toast(`Spawn request failed: ${error?.message || error}`, 'error');
  }
});
byId('send-reminders')?.addEventListener('click', async () => {
  if (!await uiConfirm('Send due reminders now? This pings agents with overdue work.', { confirmLabel: 'Send reminders' })) return;
  try {
    const result = await api('/contracts/reminders/run', { method: 'POST' });
    const n = Array.isArray(result?.reminded) ? result.reminded.length : (result?.sent ?? result?.count);
    toast(`Reminders: ${n != null ? `${n} sent` : 'done'}`, 'ok');
    await refresh();
  } catch (error) {
    toast(`Send reminders failed: ${error?.message || error}`, 'error');
  }
});
// Codex live-console input form: send turn/start via the existing WS
// the operator opened with "Connect live console".
document.addEventListener('submit', (event) => {
  const codexForm = event.target.closest('[data-action="codex-console-send"]');
  if (!codexForm) return;
  event.preventDefault();
  const agentId = codexForm.dataset.agentId;
  const input = codexForm.querySelector('input[type="text"]');
  const text = input?.value || '';
  codexConsoleSendTurn(agentId, text);
  if (input) input.value = '';
});

// Chat-first landing wiring (Phase 1).
byId('chat-filter')?.addEventListener('input', (event) => {
  state.chat.filter = event.target.value;
  chatController.renderRail();
});
byId('chat-msg-search')?.addEventListener('input', (event) => {
  state.chat.msgFilter = event.target.value;
  chatController.renderConversation();
});
// Collapsible chat sort/filters/channels panel (kept out of the way until needed).
// Chat tool tabs (2026-06-29): Find / Filters / Channels — replaces the ⚙ junk-drawer.
document.querySelectorAll('[data-chat-tool]').forEach((tab) => {
  tab.addEventListener('click', () => {
    const which = tab.dataset.chatTool;
    document.querySelectorAll('[data-chat-tool]').forEach((t) => {
      const on = t.dataset.chatTool === which;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('[data-chat-tool-panel]').forEach((p) => {
      p.hidden = p.dataset.chatToolPanel !== which;
    });
  });
});
// Clear all rail filters (scope→all, toggles off, status set empty, sort→activity).
byId('chat-clear-filters')?.addEventListener('click', () => {
  state.chat.scope = 'all';
  state.chat.unreadOnly = false; state.chat.liveOnly = false; state.chat.openOnly = false; state.chat.workingUp = false;
  state.chat.statusFilter = new Set();
  state.chat.sortMode = 'activity';
  const sortSel = byId('chat-sort'); if (sortSel) sortSel.value = 'activity';
  persistChatPrefs(); syncChatChips(); chatController.renderRail();
});
byId('chat-identity')?.addEventListener('change', (event) => {
  state.chat.identity = event.target.value || 'dashboard';
  if (state.chat.identity === 'all' && state.chat.view === 'console') {
    state.chat.view = 'messenger';
    disposeActiveXterm();
  }
  chatController.render();
});
byId('chat-identity-directory')?.addEventListener('click', () => openIdentityDirectory());
// Persist the rail filter prefs so "live only" (which hides offline/archived agents) and the
// other declutter toggles STICK across reloads — the old dashboard remembered these; not
// persisting them is why the rail re-cluttered with offline conversations on every refresh.
// persistChatPrefs moved to ./chat-prefs.mjs in v0.5.4.
// Reflect filter state into the always-visible chip bar (chips are static markup; only their
// active class tracks state, so the rail re-render never has to rebuild them).
// syncChatChips moved to ./chat-prefs.mjs in v0.5.4.
byId('chat-sort')?.addEventListener('change', (event) => {
  state.chat.sortMode = event.target.value || 'activity';
  persistChatPrefs();
  chatController.renderRail();
});
// Delegated handler for the filter-bar chips (scope / quick toggles / status filter).
byId('page-chat')?.addEventListener('click', (event) => {
  const scopeBtn = event.target.closest('[data-chat-scope]');
  if (scopeBtn) {
    state.chat.scope = scopeBtn.dataset.chatScope || 'all';
    persistChatPrefs(); syncChatChips(); chatController.renderRail();
    return;
  }
  const toggleBtn = event.target.closest('[data-chat-toggle]');
  if (toggleBtn) {
    const key = toggleBtn.dataset.chatToggle;
    state.chat[key] = !state.chat[key];
    persistChatPrefs(); syncChatChips(); chatController.renderRail();
    return;
  }
  const compactBtn = event.target.closest('[data-chat-compact-toggle]');
  if (compactBtn) {
    toggleChatCompact();
    return;
  }
  const peekBtn = event.target.closest('[data-chat-peek-toggle]');
  if (peekBtn) {
    toggleChatPeek();
    return;
  }
  const statusBtn = event.target.closest('[data-chat-status]');
  if (statusBtn) {
    const kind = statusBtn.dataset.chatStatus;
    if (!(state.chat.statusFilter instanceof Set)) state.chat.statusFilter = new Set();
    if (state.chat.statusFilter.has(kind)) state.chat.statusFilter.delete(kind);
    else state.chat.statusFilter.add(kind);
    persistChatPrefs(); syncChatChips(); chatController.renderRail();
    return;
  }
});
byId('chat-composer')?.addEventListener('submit', (event) => {
  event.preventDefault();
  chatController.send(); // Enter / Send = ordinary send (steer if the target supports it). Never queues.
});
// The Queue half of the split Send button: same send, queueIfBusy forced on for THIS message only.
byId('chat-send-queue')?.addEventListener('click', (event) => {
  event.preventDefault();
  chatController.send({ queue: true });
});
// Clicking composer chrome must not strand the operator unable to type (reported 2026-07-27:
// "if i press click outside of the chat textarea (to element with class=composer-advanced) then my
// cursor appears in front of textinput area and i cannot write").
//
// The Options panel is a <details>; its <summary> is focusable and the surrounding <div>s are not,
// so a click on either BLURS the textarea — the browser either moves focus to the summary or drops
// it entirely. Typing then goes nowhere, which reads as a dead composer.
//
// So: after a click anywhere in the composer that did NOT land on a real control, hand focus back to
// the textarea. Interactive targets are left alone — stealing focus from a select mid-choice, or from
// the file input, or from the Send/Queue buttons, would be its own bug. `closest()` covers clicks on
// a <label>'s text, which forward to their control.
byId('chat-composer')?.addEventListener('click', (event) => {
  const t = event.target;
  if (!t || typeof t.closest !== 'function') return;
  if (t.closest('input, textarea, select, button, a, label, summary, [contenteditable="true"]')) return;
  const bodyEl = byId('chat-composer-body');
  if (bodyEl && !bodyEl.disabled && document.activeElement !== bodyEl) bodyEl.focus();
});
// Toggling the Options disclosure leaves focus on the <summary>, so the very next keystroke is lost.
// Return it to the textarea once the panel has finished opening/closing.
byId('chat-composer')?.querySelector('.composer-advanced')?.addEventListener('toggle', () => {
  const bodyEl = byId('chat-composer-body');
  if (bodyEl && !bodyEl.disabled) bodyEl.focus();
});
// Draft persistence (2026-06-29 parity with old dashboard): mirror per-conversation drafts to
// localStorage so a half-written message + its rail "draft" badge survive a page reload.
// persistChatDrafts moved to ./chat-prefs.mjs in v0.5.4.
try { const _d = JSON.parse(localStorage.getItem('aifyChatDrafts') || '{}'); if (_d && typeof _d === 'object') state.chat.drafts = _d; } catch { /* keep {} */ }
// Draft preservation (WS-F): persist the composer body per conversation as the operator types.
byId('chat-composer-body')?.addEventListener('input', (event) => {
  const key = state.chat.selected;
  if (key) { state.chat.drafts = state.chat.drafts || {}; state.chat.drafts[key] = event.target.value; persistChatDrafts(); }
});
// Enter-to-send in chat (Shift+Enter inserts a newline) — WS-I11.
byId('chat-composer-body')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    chatController.send();
  }
});
// Chat artifact-attach (WS-F): upload the chosen file to /shared, insert a reference.
byId('chat-attach-input')?.addEventListener('change', (event) => {
  const file = event.target.files?.[0];
  if (file) attachChatFile(file).finally(() => { event.target.value = ''; });
});
byId('files-upload-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  uploadSharedFile().catch((err) => toast(`Upload failed: ${err?.message || err}`, 'error'));
});
byId('chat-new-channel-form')?.addEventListener('submit', (event) => {
  event.preventDefault();
  const input = byId('chat-new-channel');
  const name = (input?.value || '').trim();
  if (!name) return;
  chatCreateChannel(name).then(() => { if (input) input.value = ''; })
    .catch((err) => toast(`Create channel failed: ${err?.message || err}`, 'error'));
});

// WS-J: the Sessions composer was removed (it duplicated Chat). Messaging happens in Chat;
// Sessions is terminal + lifecycle only.
document.addEventListener('paste', (event) => {
  const target = event.target;
  // Image paste in the chat composer (the landing surface).
  if (!target || target.id !== 'chat-composer-body') return;
  const items = event.clipboardData?.items ? [...event.clipboardData.items] : [];
  const imageItem = items.find((item) => item.kind === 'file' && String(item.type || '').startsWith('image/'));
  if (!imageItem) return;
  const blob = imageItem.getAsFile();
  if (!blob) return;
  event.preventDefault();
  uploadPastedImage(blob, target).catch((error) => inspect('paste-error', { message: error.message || 'Image upload failed' }));
});
byId('close-inspector').addEventListener('click', closeInspector);
byId('toggle-nav').addEventListener('click', () => {
  setNavCollapsed(!byId('app-shell')?.classList.contains('nav-collapsed'));
});
let inspectorTouchStartY = 0;
byId('inspector').addEventListener('touchstart', (event) => {
  inspectorTouchStartY = event.touches?.[0]?.clientY || 0;
}, { passive: true });
byId('inspector').addEventListener('touchend', (event) => {
  const endY = event.changedTouches?.[0]?.clientY || 0;
  if (byId('inspector')?.classList.contains('run-inspector-sheet') && endY - inspectorTouchStartY > 70) {
    closeInspector();
  }
}, { passive: true });

// Version badge (Phase 1.8): show the running build SHA + a behind-count warning pill,
// ported from the 8800 dashboard. /version lives at the API ORIGIN root (not /api/v1).
// loadVersionBadge moved to ./version-badge.mjs in v0.5.4.

installRejectionToast();
applyCachedTheme(); // paint cached theme/title immediately so no default-palette flash before /settings
try { state.settingsTab = localStorage.getItem('aifySettingsTab') || ''; } catch { /* ignore */ }
try { const sf = JSON.parse(localStorage.getItem('aifySessionStatusFilter') || '[]'); if (Array.isArray(sf)) state.sessionStatusFilter = new Set(sf); } catch { /* ignore */ }
// Restore persisted chat rail prefs (sticky declutter) + reflect into the controls.
try {
  const p = JSON.parse(localStorage.getItem('aify.next.chatPrefs') || '{}') || {};
  state.chat.liveOnly = !!p.liveOnly;
  state.chat.openOnly = !!p.openOnly;
  state.chat.workingUp = !!p.workingUp;
  state.chat.unreadOnly = !!p.unreadOnly;
  if (typeof p.scope === 'string') state.chat.scope = p.scope;
  if (Array.isArray(p.statusFilter)) state.chat.statusFilter = new Set(p.statusFilter);
  if (p.sortMode) state.chat.sortMode = p.sortMode;
  state.chat.compact = !!p.compact;
  state.chat.peek = !!p.peek;
  const so = byId('chat-sort'); if (so) so.value = state.chat.sortMode;
  syncChatChips();
} catch { /* ignore */ }
// Default-collapse Needs-Attention so chat is the hero on landing (operator UX request).
// Honor an explicit user choice either way; with no saved preference, start collapsed (the
// header + quick-jumps stay visible as a slim one-line banner; the ▾ toggle re-expands).
try {
  if (localStorage.getItem('aify.next.attentionCollapsed') !== '0') {
    byId('attention-strip')?.classList.add('collapsed');
  }
} catch {
  byId('attention-strip')?.classList.add('collapsed');
}
loadVersionBadge();
setPage('chat'); // chat-first landing: sync the page title/subtitle with the default page
updateStaticLinks();
setNavCollapsed(preferredNavCollapsed());
// Restore the saved Work page view (Both / Work Loop / Runs) — survives reloads.
try {
  const wv = localStorage.getItem('aifyWorkView');
  if (wv && wv !== 'all') {
    document.querySelector('.diagnostics-grid')?.setAttribute('data-work-view', wv);
    document.querySelectorAll('button[data-work-view]').forEach((b) => b.classList.toggle('active', b.dataset.workView === wv));
  }
} catch { /* private mode */ }

initAgentSessionActions({ chatController, closeInspector, inspect, markConversationRead, refresh, refreshSoon, renderSessionWorkspace, setPage });
initRunInspector({ closeInspector, evaluateFlowGates, openInspector, openRunConsole, refresh, renderDiagnosticsBulkToolbar });
initRealtimeSocket({ dashboardNotifier, evaluateFlowGates, refreshSoon, resyncActiveConsole, scheduleRenderAll });
connectRealtimeSocket();
wireRealtimeResumeReconnect();
refresh();
// Poll fallback interval, honoring the `dashboard_refresh_seconds` setting (was hardcoded
// to 15s — the setting silently did nothing). Re-armed when the setting changes.
let __refreshTimer = null, __refreshSecs = 0;
function armRefreshTimer() {
  const secs = Math.max(5, Number(state.settings && state.settings.dashboard_refresh_seconds) || 15);
  if (secs === __refreshSecs && __refreshTimer) return;
  __refreshSecs = secs;
  if (__refreshTimer) clearInterval(__refreshTimer);
  __refreshTimer = setInterval(refresh, secs * 1000);
}
armRefreshTimer();
byId('attention-collapse')?.addEventListener('click', () => {
  const strip = byId('attention-strip');
  if (!strip) return;
  const collapsed = strip.classList.toggle('collapsed');
  try { localStorage.setItem('aify.next.attentionCollapsed', collapsed ? '1' : '0'); } catch { /* ignore */ }
});
byId('settings-save')?.addEventListener('click', () => {
  saveSettings().catch((err) => toast(`Save failed: ${err?.message || err}`, 'error'));
});
byId('settings-reset')?.addEventListener('click', () => {
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); // clear the edit-guard
  applyTheme(state.settings); // undo any live appearance preview
  refreshActiveTerminalTheme();
  renderSettings();           // repaint inputs from the last-saved settings
  toast('Reverted unsaved changes', 'ok');
});
// Live-preview Appearance edits (theme select, color pickers, title) without saving.
byId('settings-form')?.addEventListener('input', (event) => {
  if (event.target.closest('.settings-appearance')) previewAppearance();
});
byId('settings-form')?.addEventListener('change', (event) => {
  if (event.target.closest('.settings-appearance')) previewAppearance();
});

// The Help card snippet is static markup until we stamp the real origin into it. Done once at
// module load — the card is always in the DOM, just on a hidden page until Settings/Help is opened.
renderInstallSnippet();
