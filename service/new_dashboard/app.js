// Dashboard Next SPA entry. ES module (DASHBOARD_REBUILD_PLAN §0.1): pure cores live in
// sibling modules and are imported here; app.js remains the orchestrator (render + actions +
// the single delegated event handler + init) until later Phase-0 slices split those too.
import { esc, fileSizeLabel, relTime, tsMs, usageFmtTokens, usageResetLabel } from './util.js';
import { createTerminalInputPoster, createTerminalInputHandler, forceTerminalRepaint, waitForTerminalSize, wheelInputSequence } from './terminal-input.mjs';
import { continueCliCommand, continueCliDetails, continueCliInfo, resumeMachineNote } from './cli-resume.mjs';
import { collapseSupersededSessions, countSupersededSessions } from './sessions-list.mjs';
import { AGENT_STATUSES, LIVE_AGENT_STATUSES, STATUS_KINDS, renderStatusChip, resolveStatus, runStatusContext, statusWhyContext } from './status.js';
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
import { SESSION_FILTER_KINDS, agentForSession, renderSessionRail, selectedSessionIds } from './session-rail.mjs';
import { previewAppearance, refreshActiveTerminalTheme, renderSettings, terminalAccentColor, terminalThemeFromDashboard } from './settings-panel.mjs';
import { openAgentDrawer, sessionForAgent, syncInspectorToSelection } from './agent-drawer.mjs';
import { contractCard, diagnosticKey, filtered, renderActivityFeed, renderAttention, renderContractBoard } from './work-loop-panels.mjs';
import { codexConsoleAppendLine, codexConsoleClose, codexConsoleConnect, codexConsoleConnections, codexConsoleSendTurn } from './codex-console.mjs';
import { openIdentityDirectory } from './identity-directory.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { renderSessionActivity, runFrom } from './session-activity.mjs';
import { openEnvironmentRootsEditor, renderEnvironmentSpawnOptions, renderEnvironmentSummary, renderRuntime, renderSpawnRequests } from './environments-panels.mjs';
import { metric, renderDiagnosticsSummary, renderMetrics, renderUsageConsumption, selectedDiagnostics } from './summary-tiles.mjs';
import { copyActiveConsole, copyText } from './clipboard.mjs';
import { openAgentEditForm, openContinueForm, openMessageDetail } from './inspector-forms.mjs';
import { renderRunInspectorControls, runInspectorCapabilities, sessionForRun } from './run-inspector-controls.mjs';

function resolveApiOrigin() {
  const params = new URLSearchParams(location.search);
  const requested = params.get('apiOrigin');
  if (requested) {
    localStorage.setItem('aify.next.apiOrigin', requested.replace(/\/+$/, ''));
    return requested.replace(/\/+$/, '');
  }
  const stored = localStorage.getItem('aify.next.apiOrigin');
  if (stored) return stored.replace(/\/+$/, '');
  const port = document.documentElement.dataset.defaultApiPort || '8800';
  return `${location.protocol}//${location.hostname}:${port}`;
}

const apiOrigin = resolveApiOrigin();
const apiBase = `${apiOrigin}/api/v1`;

// The Help card's install snippet is rendered from the origin the operator actually opened the
// dashboard on. It used to hard-code one machine's LAN IP, which was wrong for every other reader
// (and published that address in a public repo). `apiOrigin` already resolves ?api= > stored
// override > this page's host, so the snippet matches whatever they are really talking to.
function renderInstallSnippet() {
  const el = document.getElementById('help-install-cmd');
  if (el) el.textContent = `bash install.sh --client claude \
  ${apiOrigin} --with-hook`;
}
const RUN_INSPECTOR_EVENT_LIMIT = 50;

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

const pages = {
  chat: ['Chat', 'Direct messages and channels across the fleet — the operator landing surface.'],
  sessions: ['Sessions', 'Live terminal and lifecycle controls per session — messaging lives in Chat.'],
  environments: ['Environments', 'Connected bridges, runtimes, roots, and capacity.'],
  diagnostics: ['Work', 'Work-loop contracts and run/dispatch evidence.'],
  analytics: ['Analytics', 'Fleet-wide message traffic, run outcomes, and live capacity.'],
  files: ['Files', 'Shared artifacts (comms_share). Upload, download, and remove files.'],
  settings: ['Settings', 'Curated service and dashboard configuration. Saves apply to the live service.'],
};

// byId moved to ./ui.js in v0.5.4 — it is a DOM lookup, and ui.js already owns the DOM helpers.
let refreshTimer = null;
// In-flight guard: refresh() fires a ~10-request bundle; refreshSoon() can be triggered by
// every WS event. Without this, under poll load (slow single-worker service) bundles pile up
// faster than they drain and saturate the browser's ~6-connection-per-origin limit — which
// starves lazily-loaded pages (e.g. Analytics) of their own fetches. Coalesce: at most one
// bundle in flight; if more arrive while it runs, run exactly one more afterwards.
let _refreshInFlight = false;
let _refreshQueued = false;
let dashboardSocket = null;
let _consoleMountGen = 0; // bumped per mount so a font-await-parked mount can detect supersession

// Chat-first landing controller (chat.js). Adapters bridge the pure module to app state:
// sendMessage routes DM→/messages/send (trigger+toast ladder) vs channel→/channels/{n}/send;
// loadConversation fetches a channel's messages; loadChannels refreshes the rail's channels.
async function chatLoadChannels() {
  try {
    // Pass the viewer id — /channels only computes per-channel unread_count when agentId is
    // supplied; without it every channel's unread badge was permanently 0.
    const res = await api(`/channels?agentId=${encodeURIComponent(state.chat.identity)}`);
    state.chat.channels = res.channels || res || [];
  } catch (_) { /* keep prior list */ }
}
async function chatLoadConversation(name) {
  const res = await api(`/channels/${encodeURIComponent(name)}?limit=80&agentId=${encodeURIComponent(state.chat.identity)}`);
  state.chat.channelMessages[name] = res.messages || res.channel?.messages || [];
}
async function chatSendMessage({ isChannel, target, identity, body, expectsReply, queueIfBusy, inReplyTo, type, priority, subject }) {
  if (isChannel) {
    // ChannelMessage requires from_agent + channel (the bare {from, body} 422'd). type/priority
    // ARE accepted by the model; subject/inReplyTo are not part of the channel contract.
    return api(`/channels/${encodeURIComponent(target)}/send`, {
      method: 'POST',
      body: JSON.stringify({
        from_agent: identity, channel: target, body,
        ...(type ? { type } : {}),
        ...(priority && priority !== 'normal' ? { priority } : {}),
        ...(queueIfBusy ? { queueIfBusy: true } : {}),
      }),
    });
  }
  // Explicit composer type wins; fall back to the expects-reply heuristic for back-compat.
  const finalType = type || (expectsReply ? 'request' : 'info');
  // Explicit subject wins; otherwise derive a short one from the body as before.
  const finalSubject = (subject && subject.trim()) ? subject.trim() : body.slice(0, 80);
  return sendMessageWithTimeout({
    from_agent: identity, to: target, type: finalType,
    subject: finalSubject, body, trigger: true,
    queueIfBusy: !!queueIfBusy, requireReply: !!expectsReply,
    ...(priority && priority !== 'normal' ? { priority } : {}),
    ...(inReplyTo ? { inReplyTo } : {}),
  });
}

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
async function loadFiles() {
  try { const res = await api('/shared'); state.files = res.files || res || []; } catch (_) { /* keep prior */ }
}
// fileSizeLabel moved to ./util.js in v0.5.4.
function renderFiles() {
  const host = byId('files-list');
  if (!host) return;
  const files = filtered(state.files, ['name', 'from', 'description']);
  host.innerHTML = files.length ? files.map((f) => `
    <article class="file-row" data-kind="file" data-id="${esc(f.name)}">
      <div class="file-main">
        <strong class="clip">${esc(f.name)}</strong>
        <p class="preview">${esc(f.description || '')}</p>
        <small>${esc(f.from || 'unknown')} · ${esc(fileSizeLabel(f.size))}${f.sharedAt ? ' · ' + esc(relTime(f.sharedAt)) + ' ago' : ''}</small>
      </div>
      <div class="file-actions">
        <a class="ghost" href="${apiBase}/shared/${encodeURIComponent(f.name)}" target="_blank" rel="noreferrer">Download</a>
        <button class="ghost danger" data-file-delete="${esc(f.name)}">Delete</button>
      </div>
    </article>`).join('') : '<div class="empty-state"><span class="empty-icon">📂</span><strong>No shared files</strong><p>Upload an artifact above, or share one from an agent with comms_share.</p></div>';
}
async function uploadSharedFile() {
  const input = byId('files-upload-input');
  const file = input?.files?.[0];
  if (!file) { toast('Choose a file to upload', 'warn'); return; }
  // Pre-check the configured size cap so we don't push a huge file just to get a 413.
  const maxMb = Number(state.settings?.max_shared_size_mb || 0);
  if (maxMb && file.size > maxMb * 1024 * 1024) {
    toast(`File is ${Math.round(file.size / (1024 * 1024))} MB — over the ${maxMb} MB limit (Settings → Max shared file size).`, 'error');
    return;
  }
  const name = (byId('files-upload-name')?.value || '').trim() || file.name;
  const description = (byId('files-upload-desc')?.value || '').trim();
  const form = new FormData();
  form.append('from_agent', 'dashboard');
  form.append('name', name);
  form.append('description', description);
  form.append('file', file, name);
  await api('/shared', { method: 'POST', body: form, headers: {} });
  if (input) input.value = '';
  if (byId('files-upload-name')) byId('files-upload-name').value = '';
  if (byId('files-upload-desc')) byId('files-upload-desc').value = '';
  await loadFiles();
  renderFiles();
  toast(`Uploaded ${name}`, 'ok');
}
// WS-F: attach a file from the chat composer — upload to /shared, insert a reference into the body.
async function attachChatFile(file) {
  if (!file) return;
  const name = file.name;
  const form = new FormData();
  form.append('from_agent', state.chat.identity || 'dashboard');
  form.append('name', name);
  form.append('description', `Shared from chat by ${state.chat.identity || 'dashboard'}`);
  form.append('file', file, name);
  try {
    await api('/shared', { method: 'POST', body: form, headers: {} });
    const bodyEl = byId('chat-composer-body');
    if (bodyEl) {
      const ref = `[shared:${name}]`;
      bodyEl.value = bodyEl.value ? `${bodyEl.value} ${ref}` : ref;
      bodyEl.focus();
      const key = state.chat.selected;
      if (key) { state.chat.drafts = state.chat.drafts || {}; state.chat.drafts[key] = bodyEl.value; persistChatDrafts(); }
    }
    await loadFiles();
    toast(`Attached ${name}`, 'ok');
  } catch (err) { toast(`Attach failed: ${err?.message || err}`, 'error'); }
}

async function deleteSharedFile(name) {
  if (!(await uiConfirm(`Delete shared file "${name}"? This removes it for everyone.`, { tone: 'danger', confirmLabel: 'Delete' }))) return;
  await api(`/shared/${encodeURIComponent(name)}`, { method: 'DELETE' });
  await loadFiles();
  renderFiles();
  toast(`Deleted ${name}`, 'ok');
}

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

async function api(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    // FastAPI validation errors return `detail` as an array of {loc,msg,...}; the old
    // `data.detail` coerced that to "[object Object]". Flatten to readable text.
    let detail = data.error || data.detail || response.statusText;
    if (Array.isArray(detail)) detail = detail.map((d) => (d && d.msg) ? d.msg : JSON.stringify(d)).join('; ');
    else if (detail && typeof detail === 'object') detail = JSON.stringify(detail);
    throw new Error(detail);
  }
  return data;
}

function awaitTerminalSize(terminalId, cols, rows) {
  return waitForTerminalSize({
    cols,
    rows,
    readSize: async () => (await api(`/terminals/${encodeURIComponent(terminalId)}`)).terminal,
  });
}

function refreshSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

let _wsReconnectAttempts = 0;
const WS_CONNECTING_TIMEOUT_MS = 8000;
function connectRealtimeSocket() {
  if (dashboardSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(dashboardSocket.readyState)) return;
  const wsOrigin = apiOrigin.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
  try {
    dashboardSocket = new WebSocket(`${wsOrigin}/ws`);
  } catch {
    state.realtimeConnected = false;
    return;
  }
  const sock = dashboardSocket;
  // Half-open-socket watchdog (Hermes parity, their NS-591). After a laptop sleep or a mobile
  // radio handoff a socket can sit in CONNECTING forever — neither onopen nor onclose ever fires,
  // so the CONNECTING guard above wedges reconnect permanently. If THIS socket is still CONNECTING
  // after the timeout, force-close it so onclose → backoff reconnect can recover. The timer is
  // scoped PER SOCKET (via `sock` + a local id): a shared global id could be cleared by a different
  // socket's onclose during a resume-overlap and leave a half-open successor unwatched.
  const watchdog = setTimeout(() => {
    if (sock.readyState === WebSocket.CONNECTING) { try { sock.close(); } catch {} }
  }, WS_CONNECTING_TIMEOUT_MS);
  sock.onopen = () => {
    clearTimeout(watchdog);
    const wasReconnect = state.realtimeConnected === false && _wsReconnectAttempts > 0;
    state.realtimeConnected = true;
    _wsReconnectAttempts = 0; // healthy connection → reset backoff to fast retry
    evaluateFlowGates();
    // After a dropped-then-reconnected WS (deploy, network blip, laptop sleep), any live
    // terminal_output frames emitted during the outage were missed — an IDLE agent emits no
    // new frame to trip the sequence-gap resync, so the mounted console shows STALE canvas
    // and typed keystrokes echo into a frame the tab never repaints ("can't write into the
    // terminal"). Re-sync the mounted console on reconnect so it repaints the authoritative
    // buffer immediately. Also pull fresh roster/session data.
    if (wasReconnect) {
      if (state.activeXterm && state.activeXterm.term) resyncActiveConsole().catch(() => {});
      refreshSoon();
    }
  };
  sock.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data || '{}');
      applyRealtimeEvent(payload.event, payload.data || {});
    } catch {}
  };
  sock.onclose = () => {
    clearTimeout(watchdog);
    state.realtimeConnected = false;
    // Exponential backoff (capped) instead of hammering /ws every 2.5s. The single-worker
    // service restarts on every deploy; a flat retry from every open tab piles load on exactly
    // when it's weakest. Reset to fast on a successful open (see onopen below).
    _wsReconnectAttempts = Math.min(_wsReconnectAttempts + 1, 6);
    const delay = Math.min(30000, 1500 * 2 ** _wsReconnectAttempts);
    setTimeout(connectRealtimeSocket, delay);
  };
}

// Reconnect on page-resume (Hermes parity). When a backgrounded/slept tab wakes, its socket is
// often CLOSED with a long backoff timer still pending (up to 30s away) — the operator stares at a
// stale console. On any resume signal, if we're not OPEN, reconnect NOW (short-circuiting the
// backoff). A stuck-CONNECTING socket is force-closed first so the CONNECTING guard can't block the
// fresh connect. Throttled so a burst of resume events (focus+visibilitychange+online together)
// fires one reconnect.
let _wsResumeNudgeAt = 0;
function nudgeRealtimeSocketOnResume() {
  const now = Date.now();
  if (now - _wsResumeNudgeAt < 1000) return;
  _wsResumeNudgeAt = now;
  const rs = dashboardSocket ? dashboardSocket.readyState : WebSocket.CLOSED;
  // OPEN → nothing to do. CONNECTING → leave it: it's either progressing (aborting a healthy slow
  // connect just churns) or genuinely stuck, in which case the per-socket watchdog kills it within
  // 8s. Only a CLOSED/CLOSING socket needs an immediate reconnect (short-circuiting the backoff).
  if (rs === WebSocket.OPEN || rs === WebSocket.CONNECTING) return;
  connectRealtimeSocket();
}
function wireRealtimeResumeReconnect() {
  const onResume = (ev) => {
    if (ev && ev.type === 'visibilitychange' && document.visibilityState !== 'visible') return;
    nudgeRealtimeSocketOnResume();
  };
  for (const [target, ev] of [[document, 'visibilitychange'], [window, 'pageshow'], [window, 'focus'], [window, 'online']]) {
    try { target.addEventListener(ev, onResume); } catch {}
  }
}

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

function applyRealtimeEvent(event, data = {}) {
  // Fire-and-forget, and deliberately BEFORE the routing below: a notification must never depend
  // on which branch the event takes, and must never be able to break the dashboard's own handling
  // of it. The notifier swallows its own errors and returns a reason string.
  try { dashboardNotifier.handle(event, data); } catch {}
  if (event === 'terminal_started' && data.terminalId && data.agentId) {
    state.terminalOwners.set(String(data.terminalId), String(data.agentId));
    refreshSoon();
    return;
  }
  if (event === 'terminal_output' && data.terminalId) {
    const owner = state.terminalOwners.get(String(data.terminalId));
    if (owner && data.agentId && data.agentId !== owner) return;
    if (data.agentId) state.terminalOwners.set(String(data.terminalId), String(data.agentId));
    // Live PTY rendering: if this terminal is currently mounted in the Session Console pane,
    // write the new bytes straight to the xterm.js instance — no DOM refresh for the stream.
    const entry = state.activeXterm;
    // Skip painting when the console pane is hidden (operator switched pages): the xterm stays
    // mounted but offscreen, so writing to it just burns CPU and grows scrollback invisibly.
    // It re-syncs from the authoritative buffer on next mount/visible render.
    if (entry && entry.container && entry.container.offsetParent === null) return;
    if (entry && String(entry.terminalId) === String(data.terminalId) && data.output) {
      // Seq-based dedup + gap-resync (WS-D): the server tags frames with a monotonic seq.
      // Drop frames we've already painted; on a gap (missed a frame, e.g. WS reconnect blip)
      // re-fetch the authoritative buffer instead of painting out-of-order bytes.
      const seq = Number(data.seq);
      if (Number.isFinite(seq) && entry.lastSeq >= 0) {
        if (seq <= entry.lastSeq) { return; }
        if (seq > entry.lastSeq + 1) { resyncActiveConsole().catch(() => {}); return; }
      }
      if (Number.isFinite(seq)) entry.lastSeq = seq;
      try {
        if (entry.term) entry.term.write(data.output);
        else if (entry.fallbackPre) { entry.fallbackPre.textContent += data.output; entry.fallbackPre.scrollTop = entry.fallbackPre.scrollHeight; }
        entry.recentText = (String(entry.recentText || '') + String(data.output)).slice(-600);
        updateAwaitPill();
      } catch {}
    }
    // NOTE: do NOT refreshSoon() here. terminal_output streams every 1-4s; a full data
    // refetch per frame made the api-status chip flap 'refreshing'↔'live' every second and
    // wasted the 9-endpoint refetch. Live bytes are written to xterm above; agent/roster data
    // changes arrive via the granular agent_status / other WS events below.
    return;
  }
  // Granular consumption (Phase 1.2): a status change patches the agent in place and
  // re-renders (signature-gated) WITHOUT the 9-endpoint full refetch — the dashboard's
  // biggest poll-load reduction. Only fall back to refreshSoon for events that change data
  // the client can't synthesize from the event payload.
  if (event === 'agent_status' && data.agentId) {
    const agent = state.agents.find((a) => a.id === data.agentId);
    if (agent) {
      if (data.status) { agent.status = data.status; agent.statusRaw = data.status; }
      if (data.statusNote !== undefined) agent.statusNote = data.statusNote;
      scheduleRenderAll();
      return;
    }
    refreshSoon(); // unknown agent — a registration we haven't loaded yet
    return;
  }
  if ([
    'message_sent',
    'dispatch_queued',
    'dispatch_claimed',
    'dispatch_updated',
    'dispatch_control_requested',
    'dispatch_control_updated',
    'contract_reminders_sent',
    'settings_updated',
    'session_control_requested',
    'session_deleted',
    'agent_registered',
  ].includes(event)) {
    refreshSoon();
  }
}

function runQueryPath(status = state.runStatusFilter) {
  const params = new URLSearchParams({ limit: '80' });
  if (status) params.set('status', status);
  return `/dispatch/runs?${params.toString()}`;
}

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

async function loadRunsForStatus(status = state.runStatusFilter, render = true) {
  state.runStatusFilter = status || '';
  const runs = await api(runQueryPath(state.runStatusFilter));
  state.runs = runs.runs || [];
  if (render) {
    renderRuns();
  }
  return state.runs;
}

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

async function _refreshImpl() {
  // Only flip the chip to "refreshing" if the cycle is actually SLOW (>500ms). Fast polls
  // (the common case) finish before this fires, so the chip stays a steady "live" instead of
  // flickering live↔refreshing every cycle.
  const slowChipTimer = setTimeout(() => {
    const c = byId('api-status'); if (c) { c.textContent = 'refreshing'; c.className = 'status-chip muted'; }
  }, 500);
  // RESILIENT POLL (2026-06-18): use allSettled, not Promise.all. The single-worker service can
  // transiently drop a request under poll load ("Failed to fetch"); with Promise.all ONE such blip
  // rejected the whole refresh → no state updated, renderAll never ran → the entire dashboard
  // (incl. every agent's status) froze on its last render and looked stale/"wrong". Now each slice
  // applies independently; a slice whose fetch blipped keeps its last-good value, and we always
  // re-render with whatever fresh data arrived this cycle.
  const settled = await Promise.allSettled([
    api('/agents'),                                                       // 0
    api('/contracts?limit=80'),                                           // 1
    api('/messages/inbox/dashboard?filter=all&peek=true&limit=80'),       // 2
    api('/messages/recent?limit=80'),                                     // 3
    api(runQueryPath()),                                                  // 4
    api('/sessions?limit=80'),                                            // 5
    api('/environments'),                                                 // 6
    api('/spawn-requests?limit=200'),                                     // 7
    api('/stats'),                                                        // 8
    api('/settings'),                                                     // 9
  ]);
  const ok = (i) => settled[i].status === 'fulfilled';
  const val = (i) => (ok(i) ? settled[i].value : undefined);
  const failed = settled.filter((s) => s.status === 'rejected').length;

  if (ok(0)) state.agents = asAgentArray(val(0));
  if (ok(1)) { state.contracts = val(1).contracts || []; state.contractsBase = state.contracts; }
  // Keep a non-default Work-loop State filter alive across polls: the base fetch is
  // open-scope, so a terminal selection (Answered/Failed/Missing reply/…) emptied ~15s
  // after choosing it when the poll overwrote state.contracts (review finding #4).
  // contractsBase keeps the open set for the metrics; state.contracts follows the filter.
  const contractStateSel = byId('contract-state')?.value || '';
  if (ok(1) && contractStateSel && contractStateSel !== 'open') {
    try { await loadContractsForState(contractStateSel, false); } catch (_) { /* keep base */ }
  }
  // messages: prefer recent, fall back to inbox, then keep prior — only touch if either succeeded.
  if (ok(2) || ok(3)) {
    state.messages = (ok(3) && val(3).messages) || (ok(2) && val(2).messages) || state.messages || [];
  }
  if (ok(4)) state.runs = val(4).runs || [];
  if (ok(5)) {
    state.sessions = asArray(val(5), 'sessions');
    state.sessions.forEach((session) => {
      const terminalId = session.terminalId || session.terminal?.id;
      const agentId = session.agentId || session.agent_id;
      if (terminalId && agentId) state.terminalOwners.set(String(terminalId), String(agentId));
    });
  }
  if (ok(6)) state.environments = asArray(val(6), 'environments');
  if (ok(7)) state.spawnRequests = asArray(val(7), 'spawnRequests');
  if (ok(8)) state.stats = val(8) || {};
  if (ok(9) && val(9) && typeof val(9) === 'object') {
    state.settings = val(9);
    applyTheme(state.settings); // apply the server-stored appearance (theme/palette/title)
    refreshActiveTerminalTheme(); // keep a mounted console's accent in sync
    armRefreshTimer(); // honor dashboard_refresh_seconds (no-op unless it changed)
  }
  try { await chatLoadChannels(); } catch (_) { /* keep prior channels */ }
  // Keep an OPEN channel conversation live: channel messages are otherwise fetched only on
  // open/send, so the rail badge ticked up while the open timeline stayed frozen (review
  // finding #5). The conversation sig covers the re-render.
  if (String(state.chat.selected || '').startsWith('channel:')) {
    try { await chatLoadConversation(state.chat.selected.slice('channel:'.length)); } catch (_) { /* keep prior view */ }
  }
  // Stale-selection guard (review finding #10): if the open conversation's agent/channel was
  // removed (here or by another client), close back to the overview — otherwise the header,
  // timeline, and composer stay live against a dead entity and a send goes nowhere useful.
  {
    const sel = String(state.chat.selected || '');
    if (sel.startsWith('dm:') && ok(0) && !(state.agents || []).some((a) => a.id === sel.slice(3))) chatController.close();
    else if (sel.startsWith('channel:') && Array.isArray(state.chat.channels)
      && !state.chat.channels.some((c) => c && c.name === sel.slice('channel:'.length))) chatController.close();
  }
  try { await loadFiles(); } catch (_) { /* keep prior files */ }
  // Only flip to "loaded" once the roster actually arrived: with the server fully down all
  // slices reject, and loaded=true made the rail show a misleading "No agents." while the
  // chip said reconnecting (review finding #12). Until then the rail keeps its loading state.
  if (ok(0)) state.loaded = true;
  evaluateFlowGates();
  renderAll();
  // Status chip: green while the CORE roster (agents) is fresh, even if a non-critical slice
  // blipped (don't alarm the operator over a transient). Only show "reconnecting" when the core
  // roster itself didn't refresh — we keep last-good and retry next cycle (no scary "API error").
  // OPERATOR-REPORTED 2026-08-11: "when i have inspector open and status changes, it does not
  // update." There was no refresh path at all — every opener rendered once and nothing re-rendered
  // it, so the drawer was a snapshot while the rows behind it moved. Re-render it here, per poll.
  //
  // Which drawers may be re-rendered is NOT decided inline: `inspector-refresh.mjs` owns that, fails
  // closed on any unclassified kind, and refuses while a form is open, focus is inside, or the
  // drawer's own fetch is in flight. Re-rendering a form would eat what the operator was typing,
  // which is a worse bug than a stale panel.
  refreshOpenInspector();
  clearTimeout(slowChipTimer); // cycle finished — cancel the pending "refreshing" flip
  if (failed === 0) {
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
  } else if (ok(0)) {
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
  } else {
    byId('api-status').textContent = 'reconnecting';
    byId('api-status').className = 'status-chip warn';
  }
}

// filtered moved to ./work-loop-panels.mjs in v0.5.4.

// Single-item version of the top-bar global Find (for callers that do their own filtering).
function matchesGlobalFilter(item, fields) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return true;
  return fields.some((field) => String(item[field] || '').toLowerCase().includes(needle));
}

// Phase 0.4 (DASHBOARD_REBUILD_PLAN §0.4): per-section render keyed on an input signature
// computed in ONE place, so renderAll (run on every 15s poll, every WS event, and every
// filter keystroke) only rewrites a section's innerHTML when its inputs actually changed —
// no needless re-render, no flicker. The session workspace + console are intentionally NOT
// gated here: they keep their own proven internal guards (the xterm remount guard +
// terminalId cache) which already preserve live PTY/scroll/focus state across refreshes.
const _sectionSig = Object.create(null);
function renderSection(key, signature, renderFn) {
  const sig = JSON.stringify(signature);
  if (_sectionSig[key] === sig) return;
  _sectionSig[key] = sig;
  renderFn();
}
// Compact, stable fingerprints of just the fields a section renders from.
const _agentSig = () => state.agents.map((a) => [a.id, a.status]);
const _contractSig = () => state.contracts.map((c) => [c.id, c.state, c.status, c.overdue, c.subject]);
const _runSig = () => state.runs.map((r) => [r.id, r.status, r.subject, r.summary, r.targetAgentId || r.target_agent]);
const _envSig = () => state.environments.map((e) => [e.id, e.status, e.label]);
const _spawnReqSig = () => state.spawnRequests.map((r) => [r.id, r.status, r.agentId, r.error, r.updatedAt]);
const _msgSig = () => state.messages.map((m) => [m.id, m.from, m.subject, m.read]);
const _chatChanSig = () => (state.chat.channels || []).map((c) => [c.name, c.unreadCount, c.memberCount]);
const _chatConvSig = () => Object.entries(state.chat.channelMessages || {}).map(([k, v]) => [k, (v || []).length]);

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

// Fetch the analytics page's data (analytics + usage pools + consumption) into state,
// then render. Throttled + in-flight-guarded so the WS-driven renderAll loop (which can
// fire many times/sec) collapses to at most one fetch per ~12s — the backend is
// single-worker + lock-sensitive. Pass force=true on page-open / range-change / manual.
async function loadAnalytics(force = false) {
  if (state.analytics.loading) return;
  if (!force && state.analytics.lastMs && (Date.now() - state.analytics.lastMs) < 12000) {
    renderAnalyticsPage();
    return;
  }
  const range = rangeDef(state.analytics.range).key;
  state.analytics.loading = true;
  try {
    const [data, usage, consumption] = await Promise.all([
      api(`/analytics?range=${encodeURIComponent(range)}`).catch(() => null),
      api('/usage').catch(() => null),
      api('/usage/consumption').catch(() => null),
    ]);
    if (data && typeof data === 'object') state.analytics.data = data;
    else if (!state.analytics.data) state.analytics.data = {};
    // Keep last-good usage on a transient failure (never blank a live quota number);
    // flag it stale so the panel can say so.
    if (usage) { state.analytics.usage = usage; state.analytics.usageStale = false; }
    else if (state.analytics.usage) state.analytics.usageStale = true;
    if (consumption) state.analytics.consumption = consumption;
    state.analytics.lastMs = Date.now();
  } catch (error) {
    if (!state.analytics.data) state.analytics.data = {};
    toast(`Analytics failed: ${error?.message || error}`, 'error');
  } finally {
    state.analytics.loading = false;
    renderAnalyticsPage();
  }
}

// usageResetLabel moved to ./util.js in v0.5.4.
// usageFmtTokens moved to ./util.js in v0.5.4.
// Usage/quota Pools band + Consumption section (2026-06-26). Advisory — read-only.
function renderUsagePools() {
  const host = byId('usage-pools');
  if (!host) return;
  const pools = (state.analytics.usage && state.analytics.usage.pools) || [];
  if (!pools.length) { host.innerHTML = '<p class="em">Usage collector warming up…</p>'; return; }
  // Stale notice when the last refresh failed (we keep showing last-good rather than blanking).
  const staleNote = state.analytics.usageStale ? '<p class="subtle usage-stale-note">⚠ Last usage refresh failed — showing last known values.</p>' : '';
  const LABELS = {
    'anthropic-claude-max': 'Anthropic · Claude Max',
    'openai-chatgpt-codex': 'OpenAI · ChatGPT (Codex + Hermes)',
    'local-ollama': 'Local · Ollama',
  };
  host.innerHTML = staleNote + pools.map((p) => {
    const w = p.weekly || {}, f = p.five_hour || {};
    const sev = (p.severity && p.severity !== 'normal') ? p.severity : '';
    const left = (p.verified === false || w.left_pct == null) ? '—' : w.left_pct + '%';
    const fleft = (f.left_pct == null) ? '—' : f.left_pct + '%';
    const used = (p.verified === false || w.used_pct == null) ? 0 : Math.max(0, Math.min(100, w.used_pct));
    const fiveHourReset = f.resets_at ? usageResetLabel(f.resets_at) : '';
    const tags = (p.unknown ? '<span class="usage-tag">unknown</span>' : '') + (p.stale ? '<span class="usage-tag">stale</span>' : '');
    const name = LABELS[p.source_id] || p.source_id;
    // Backend blanks the numbers (→ "—") when they can't be trusted; the note says why so agents
    // treat it as unknown instead of a live value. `expired` = collector stopped (>24h); `reset_elapsed`
    // = the window already reset after this snapshot (e.g. a stale codex/hermes rollout).
    // NEVER publish a number we cannot stand behind. The OpenAI card showed "100% left" while the
    // operator was actually at ~64% used — it faithfully echoed an endpoint that turned out to be
    // metering something else. Their verdict, and it is the right rule: "it lies... it is worse
    // than not showing". So an UNVERIFIED pool renders "—" and says why, and its raw readings are
    // shown as EVIDENCE below, never as the headline.
    const staleMsg = p.expired ? 'No fresh quota data in 24h+'
      : p.reset_elapsed ? 'Quota window already reset — awaiting a fresh reading'
      : (p.verified === false ? (p.unverified_reason || 'Source not trusted for this account') : '');
    // Evidence line: what the source actually returned, labelled as such, so it informs without
    // pretending to be the operator's quota.
    const ev = [];
    if (f.used_pct != null) ev.push(`5h ${f.used_pct}% used`);
    if (w.used_pct != null) ev.push(`weekly ${w.used_pct}% used`);
    if (p.credits && p.credits.messages_left != null) ev.push(`~${p.credits.messages_left} msgs credit`);
    if (p.limit_reached) ev.push('limit reached');
    const evidence = ev.length ? `<div class="usage-pool-meta subtle">source says: ${esc(ev.join(' · '))}</div>` : '';
    const meta = staleMsg
      ? `<div class="usage-pool-meta usage-pool-expired">⚠ ${esc(staleMsg)}</div>${evidence}`
      : `<div class="usage-pool-meta">5h ${fleft} left${fiveHourReset ? ' · ' + esc(fiveHourReset) : ''}</div>`;
    return `<div class="usage-pool-card ${sev}"><div class="usage-pool-name"><span>${esc(name)}</span><span>${tags}</span></div>`
      + `<div class="usage-pool-weekly">${left}<span class="usage-pool-sub"> weekly left</span></div>`
      + `<div class="usage-pool-bar"><span style="width:${used}%"></span></div>`
      + meta + `</div>`;
  }).join('');
}
// renderUsageConsumption moved to ./summary-tiles.mjs in v0.5.4.

function renderAnalyticsPage() {
  // Single KPI grid (2026-06-29): ops + stats cards render into ONE .metric-grid so the two rows
  // can't have mismatched card widths/rhythm — they pack uniformly as one auto-fit grid.
  const kpiHost = byId('analytics-ops');
  if (!kpiHost) return;
  renderUsagePools();
  renderUsageConsumption();
  const data = state.analytics.data;
  const rangeHost = byId('analytics-range');
  if (rangeHost) rangeHost.innerHTML = rangeSelectorHtml(state.analytics.range);
  if (!data) {
    // One coherent page-level empty state instead of a message + 6 stale/blank panels below it.
    kpiHost.innerHTML = '';
    const traffic = byId('analytics-traffic');
    if (traffic) traffic.innerHTML = `<p class="em">${state.analytics.loading ? 'Loading analytics…' : 'No analytics yet — open the page to load fleet metrics.'}</p>`;
    ['analytics-outcomes', 'analytics-leaderboard', 'analytics-channels', 'analytics-health', 'analytics-runs', 'analytics-failures'].forEach((id) => { const el = byId(id); if (el) el.innerHTML = ''; });
    return;
  }
  kpiHost.innerHTML = opsKpisHtml(data) + statCardsHtml(data);
  const traffic = byId('analytics-traffic');
  if (traffic) traffic.innerHTML = trafficChartHtml(data, state.analytics.range);
  const outcomes = byId('analytics-outcomes');
  if (outcomes) outcomes.innerHTML = dispatchOutcomesHtml(data);
  const leaderboard = byId('analytics-leaderboard');
  if (leaderboard) leaderboard.innerHTML = agentLeaderboardHtml(data);
  const channels = byId('analytics-channels');
  if (channels) channels.innerHTML = busiestChannelsHtml(data);
  const health = byId('analytics-health');
  if (health) health.innerHTML = healthGridHtml(data);
  const runs = byId('analytics-runs');
  if (runs) runs.innerHTML = runStatusMixHtml(data.runsByStatus || {});
  const failures = byId('analytics-failures');
  if (failures) failures.innerHTML = failureReasonsHtml(data);
}

// metric moved to ./summary-tiles.mjs in v0.5.4.

// renderMetrics moved to ./summary-tiles.mjs in v0.5.4.

// contractCard moved to ./work-loop-panels.mjs in v0.5.4.

// contractActionable moved to ./record-fields.mjs in v0.5.4.

// renderAttention moved to ./work-loop-panels.mjs in v0.5.4.

// diagnosticKey moved to ./work-loop-panels.mjs in v0.5.4.

// selectedDiagnostics moved to ./summary-tiles.mjs in v0.5.4.

function pruneDiagnosticSelection() {
  const live = new Set([
    ...state.contracts.map((contract) => diagnosticKey('contract', contract.id)),
    ...state.runs.map((run) => diagnosticKey('run', run.id)),
  ]);
  for (const key of [...state.selectedDiagnosticIds]) {
    if (!live.has(key)) state.selectedDiagnosticIds.delete(key);
  }
}

// renderDiagnosticsSummary moved to ./summary-tiles.mjs in v0.5.4.

// Work-loop maintenance actions (parity with old dashboard's hygiene buttons).
// Both endpoints are safe to run idempotently; they create fallback records for
// terminal runs that never recorded a handoff / never marked their source read.
const MAINTENANCE_ACTIONS = {
  'repair-reads': { path: '/contracts/hygiene/repair-read-receipts', label: 'Repair delivered reads' },
  'repair-handoffs': { path: '/dispatch/handoffs/repair', label: 'Repair handoffs' },
};

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

function selectedSession() {
  return state.sessions.find((session) => sessionId(session) === state.selectedSessionId) || null;
}

function ensureSelectedSession() {
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

// messagesForSession moved to ./session-activity.mjs in v0.5.4.

// Single source of truth lives in messageIdOf(); kept as an alias so existing call sites work.
// messageId moved to ./record-fields.mjs in v0.5.4.

// messageRunId moved to ./record-fields.mjs in v0.5.4.

// runTargetAgent moved to ./record-fields.mjs in v0.5.4.

// sessionForAgent moved to ./agent-drawer.mjs in v0.5.4.

// sessionForRun moved to ./run-inspector-controls.mjs in v0.5.4.

function runSourceMessage(run) {
  const id = String(run?.messageId || run?.message_id || state.inspector.sourceMessageId || '').trim();
  if (!id) return null;
  return state.messages.find((message) => messageId(message) === id) || null;
}

// renderSessionBulkToolbar moved to ./session-rail.mjs in v0.5.4.

// WS-F: status multiselect filter chips for the Sessions rail.
// Proof-based 6-state model only — `idle`/`stale` were removed in the status rewrite, so they must
// not appear as session filter chips (dead chips that match nothing).
// H1: these were hand-copies of status_engine.VALID_STATUSES. They now alias the single JS
// owner in status.js, which is bound to the Python source by a test.
// SESSION_FILTER_KINDS moved to ./session-rail.mjs in v0.5.4.
const SESSION_LIVE_KINDS = LIVE_AGENT_STATUSES;
// renderSessionStatusFilter moved to ./session-rail.mjs in v0.5.4.

function persistSessionStatusFilter() {
  try { localStorage.setItem('aifySessionStatusFilter', JSON.stringify([...state.sessionStatusFilter])); } catch { /* ignore */ }
}

// renderSessionRail moved to ./session-rail.mjs in v0.5.4.

// Persisted collapse state for session env-groups (WS-J collapsibles).
// sessionGroupCollapsed moved to ./session-rail.mjs in v0.5.4.
function toggleSessionGroupCollapsed(envId, collapsed) {
  try {
    const set = new Set(JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []);
    if (collapsed) set.add(envId); else set.delete(envId);
    localStorage.setItem('aifyCollapsedSessionGroups', JSON.stringify([...set]));
  } catch { /* ignore */ }
}

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

function disposeActiveXterm() {
  const entry = state.activeXterm;
  if (!entry) return;
  try { entry.resizeObserver?.disconnect(); } catch {}
  try { if (entry.wheelHandler && entry.container) entry.container.removeEventListener('wheel', entry.wheelHandler); } catch {}
  try { entry.term.dispose(); } catch {}
  state.activeXterm = null;
}

let consoleInputBlockedToastAt = 0;

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

async function mountXtermForTerminal(terminalId, agentId, container, { canInput = true } = {}) {
  if (!container || !terminalId) return;
  if (typeof window.Terminal === 'undefined') {
    // Non-xterm fallback: a scrolling text dump of the buffered output (parity with the old
    // dashboard's console-output-fallback) rather than just an error line.
    container.innerHTML = '<pre class="console-output-fallback" aria-live="polite"></pre>';
    const pre = container.querySelector('pre');
    try {
      const data = await api(`/terminals/${encodeURIComponent(terminalId)}`);
      if (pre) { pre.textContent = String(data?.terminal?.output || ''); pre.scrollTop = pre.scrollHeight; }
    } catch { if (pre) pre.textContent = '[xterm.js unavailable and history fetch failed]'; }
    state.activeXterm = { terminalId, agentId, term: null, fitAddon: null, container, fallbackPre: pre, lastSeq: -1, canInput };
    return;
  }
  if (
    state.activeXterm
    && state.activeXterm.terminalId === terminalId
    && state.activeXterm.container === container
    && container.isConnected !== false
  ) {
    state.activeXterm.canInput = canInput;
    return;
  }
  disposeActiveXterm();
  container.innerHTML = '';
  // Mount generation: state.activeXterm is null from here until we assign it below, and the font
  // warm-up awaits in between. A rapid session switch can start a newer mount during that gap; this
  // token lets the older (superseded) mount bail before it creates a WebGL context / claims
  // state.activeXterm — otherwise it leaks an xterm + GL context nothing will dispose.
  const _mountGen = ++_consoleMountGen;

  const term = new window.Terminal({
    // This is a real PTY byte stream. Rewriting LF to CRLF changes cursor semantics and is one
    // reason this mirror diverged from Hermes' direct xterm attachment.
    convertEol: false,
    cursorBlink: true,
    fontFamily: '"Cascadia Code", ui-monospace, "Consolas", monospace',
    fontSize: 13,
    theme: terminalThemeFromDashboard(),
    scrollback: 5000,
    // Hermes terminal-setup parity (studied from their dashboard ChatPage + desktop shell):
    //  - allowProposedApi: REQUIRED for the Unicode11 addon we activate below (without it xterm
    //    warns and the width provider silently stays on the core tables → CJK/emoji misalign).
    //  - minimumContrastRatio: xterm's default is 1 (OFF), which paints raw saturated ANSI —
    //    dark-blue-on-black is unreadable. 4.5:1 (WCAG AA) is Hermes' "VS Code secret sauce":
    //    it clamps fg against bg at render time so low-contrast ANSI stays legible.
    //  - selection ergonomics: force native selection under mouse-tracking TUIs and select-word
    //    on right-click, matching their gnome-terminal-parity behavior.
    allowProposedApi: true,
    minimumContrastRatio: 4.5,
    macOptionClickForcesSelection: true,
    rightClickSelectsWord: true,
  });
  let fitAddon = null;
  if (window.FitAddon && window.FitAddon.FitAddon) {
    fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
  }
  // Guarded fit (Hermes parity). Never run fit() on a detached or zero-sized host: fit()
  // triggers a WebGL texture-atlas rebuild, and doing that while a sibling pane is mid-transition
  // at 0px crashes the GL renderer (their comment, learned the hard way). Guard on connected +
  // measurable box so a hidden/collapsing pane simply skips the fit and re-fits when visible.
  const safeFit = () => {
    if (!fitAddon || !container.isConnected) return;
    if (container.clientWidth <= 0 || container.clientHeight <= 0) return;
    try { fitAddon.fit(); } catch {}
  };
  // Match Hermes dashboard's terminal fidelity: Unicode 11 supplies current wide-character
  // cell widths (important for Ink/TUI cursor alignment) and web-links makes rendered URLs
  // clickable without changing the underlying PTY bytes.
  if (window.Unicode11Addon && window.Unicode11Addon.Unicode11Addon) {
    try {
      term.loadAddon(new window.Unicode11Addon.Unicode11Addon());
      term.unicode.activeVersion = '11';
    } catch { /* core Unicode provider remains active */ }
  }
  if (window.WebLinksAddon && window.WebLinksAddon.WebLinksAddon) {
    try { term.loadAddon(new window.WebLinksAddon.WebLinksAddon()); } catch {}
  }
  // Font warm-up before first open/fit (Hermes parity). fit() converts the pane's pixel box into
  // cols/rows using the FONT's cell metrics. If the terminal font hasn't loaded yet, fit measures
  // FALLBACK metrics → wrong row count → the shell boots at the wrong size → an extra SIGWINCH and
  // a stretch of stale/blank rows. Worse, the WebGL renderer would bake the fallback face into its
  // glyph atlas. Wait for the weights we render (regular/bold/italic) before opening. allSettled +
  // a font the host lacks (Cascadia Code absent → Consolas fallback) simply resolves empty — no-op.
  if (document.fonts && typeof document.fonts.load === 'function') {
    try {
      await Promise.allSettled([
        document.fonts.load('13px "Cascadia Code"'),
        document.fonts.load('bold 13px "Cascadia Code"'),
        document.fonts.load('italic 13px "Cascadia Code"'),
      ]);
    } catch { /* fonts API hiccup: proceed; fit re-runs on the ResizeObserver anyway */ }
  }
  // Superseded during the font await (or the pane was detached)? Drop THIS term before opening it /
  // creating a GL context / claiming state.activeXterm, so a newer mount is the only live console.
  if (_mountGen !== _consoleMountGen || !container.isConnected) {
    try { term.dispose(); } catch {}
    return;
  }
  term.open(container);
  // WebGL renderer (WS-D) — big perf win under heavy TUI output; fall back to the DOM
  // renderer if the GL context is lost or the addon throws. Kept referenceable so a live theme
  // change can clear its glyph-color texture atlas (refreshActiveTerminalTheme).
  let webglAddon = null;
  if (window.WebglAddon && window.WebglAddon.WebglAddon) {
    try {
      const webgl = new window.WebglAddon.WebglAddon();
      webgl.onContextLoss(() => { try { webgl.dispose(); } catch {} webglAddon = null; });
      term.loadAddon(webgl);
      webglAddon = webgl;
    } catch { /* DOM renderer remains active */ }
  }
  safeFit();

  // Keystroke forwarding back to the bridge PTY via /terminals/<id>/input.
  // Service request shape (TerminalControlRequest in api_v2.py): {body, requestedBy}.
  // Hermes uses one ordered WebSocket. We still cross the service API, so serialize requests:
  // parallel fetches can otherwise deliver consecutive keystroke chunks out of order.
  const postTerminalInput = createTerminalInputPoster({
    api,
    terminalId,
    onError: (err) => {
      term.write(`\r\n\x1b[31m[input post failed: ${String(err?.message || err).replace(/\x1b/g, '')}]\x1b[0m\r\n`);
    },
  });
  term.onData(createTerminalInputHandler({
    canInput: () => !(state.activeXterm && state.activeXterm.canInput === false),
    onBlocked: () => {
      const now = Date.now();
      if (now - consoleInputBlockedToastAt > 4000) {
        consoleInputBlockedToastAt = now;
        toast('This console is not accepting input right now (session not live).', 'warn');
      }
    },
    postInput: postTerminalInput,
  }));
  // Emit-resize-only-on-change (hermes parity): xterm fires onResize on every fit even when the
  // grid dims didn't actually change — debounce AND dedupe so we don't spam the PTY with no-ops.
  let resizeTimer = 0;
  let lastCols = 0;
  let lastRows = 0;
  term.onResize(({ cols, rows }) => {
    const c = Math.max(20, cols);
    const r = Math.max(5, rows);
    if (c === lastCols && r === lastRows) return;
    lastCols = c; lastRows = r;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      api(`/terminals/${encodeURIComponent(terminalId)}/resize`, {
        method: 'POST',
        body: JSON.stringify({ cols: c, rows: r, requestedBy: 'dashboard' }),
      }).catch(() => {});
    }, 120);
  });

  // OSC-52 clipboard (hermes parity): honor programs inside the PTY that emit the OSC 52 "set
  // clipboard" sequence (vim "+y, tmux, etc.) by copying to the browser clipboard — works on the
  // http loopback origin via the execCommand fallback in copyText().
  try {
    term.parser.registerOscHandler(52, (data) => {
      const payload = String(data || '');
      const b64 = payload.slice(payload.indexOf(';') + 1);
      if (!b64 || b64 === '?') return true;
      try { copyText(atob(b64)); } catch { /* malformed base64 — ignore */ }
      return true;
    });
  } catch { /* older xterm without parser.registerOscHandler */ }

  // Copy/paste key handler for the http LAN origin (navigator.clipboard is undefined there):
  // Ctrl+Shift+C copies the selection, Ctrl+Shift+V / Ctrl+V pastes via the clipboard API when
  // available (loopback secure context) and otherwise leaves the keystroke to flow to the PTY.
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== 'keydown') return true;
    if (e.ctrlKey && e.shiftKey && (e.key === 'C' || e.key === 'c')) {
      if (term.hasSelection()) { copyText(term.getSelection()); return false; }
    }
    if ((e.ctrlKey && e.shiftKey && (e.key === 'V' || e.key === 'v')) || (e.ctrlKey && !e.shiftKey && (e.key === 'V' || e.key === 'v'))) {
      if (navigator.clipboard?.readText) {
        navigator.clipboard.readText().then((txt) => { if (txt) term.paste(txt); }).catch(() => {});
        return false;
      }
    }
    return true;
  });

  // Wheel → arrow keys when a full-screen TUI owns the alternate screen buffer (claude/hermes Ink
  // UIs): a raw wheel does nothing inside the alt-screen, so translate it to cursor up/down so the
  // operator can scroll the agent's UI with the mouse like the old dashboard allowed.
  //
  // TWO FIXES, 2026-07-27, from an operator report of "I try to write and delete stuff in the
  // dashboard terminal but I can't" — a composer full of scrambled escape-sequence fragments.
  //
  // 1. It POSTED DIRECTLY, bypassing `postTerminalInput`. The comment 70 lines above this one
  //    explains exactly why that is wrong — "serialize requests: parallel fetches can otherwise
  //    deliver consecutive keystroke chunks out of order" — and then this handler opened a second,
  //    UNORDERED writer to the same PTY. A wheel gesture emits a burst of events, each firing its
  //    own fetch, so wheel arrows and real keystrokes interleaved arbitrarily. Now routed through
  //    the same serialized queue, so there is ONE ordered writer per console.
  //
  // 2. It fired on HOVER. `wheel` does not require focus, so merely scrolling the page with the
  //    pointer over a console injected up to 5 synthetic arrow keypresses PER EVENT into that
  //    agent's live PTY. Inside a composer, arrows move the cursor — so an operator scrolling to
  //    read scattered their own subsequent typing across the draft, which is precisely the reported
  //    symptom. Keystroke injection now requires the terminal to actually HAVE FOCUS, which is the
  //    honest signal for "I intend to type here". Hover-scroll is navigation, not input.
  //
  // Deliberately NOT filtering what xterm emits from real keys/mouse — that is the raw-passthrough
  // contract (`server.js`: "Raw passthrough: callers own newline semantics"). This only stops the
  // dashboard SYNTHESISING input the operator never typed.
  const onWheel = (ev) => {
    try {
      // Focus gate: `document.activeElement` is xterm's hidden textarea when the terminal is
      // focused. Without it, a wheel over an unfocused pane types into someone's draft.
      const seq = wheelInputSequence({
        bufferType: term.buffer?.active?.type,
        canInput: state.activeXterm?.canInput !== false,
        focused: !!(term.textarea && document.activeElement === term.textarea),
        deltaY: ev.deltaY,
      });
      if (!seq) return; // let the page scroll; do not inject keystrokes
      postTerminalInput(seq);
      ev.preventDefault();
    } catch { /* leave native behavior */ }
  };
  try { container.addEventListener('wheel', onWheel, { passive: false }); } catch {}

  // Re-fit on container/window resize so the terminal tracks the pane size.
  let resizeObserver = null;
  if (window.ResizeObserver && fitAddon) {
    let resyncTimer = null;
    // Coalesce observer bursts to a SINGLE rAF (Hermes parity). A ResizeObserver fires many times
    // during a layout transition; running fit() synchronously on each — especially through a 0px
    // frame — is what crashes the WebGL atlas. One rAF per burst also lets the box settle before
    // we measure. `roFrame` guards against stacking frames; safeFit() no-ops on a 0/detached box.
    let roFrame = 0;
    resizeObserver = new ResizeObserver(() => {
      if (roFrame) return;
      roFrame = requestAnimationFrame(() => {
        roFrame = 0;
        const entry = state.activeXterm;
        // Stale-observer guard: this frame may have been scheduled just before dispose. If the
        // active console is no longer THIS container's, bail — otherwise a disposed terminal's
        // observer would mutate the new entry (spurious resync/flicker).
        if (!entry || entry.container !== container) return;
        // Wide mirror (resident terminal wider than the pane): fit() would shrink the xterm back
        // to the pane and re-wrap the source lines. Instead recompute the pane width WITHOUT
        // applying it; only if it changed materially do we resync (which re-fits + re-widens).
        if (entry && entry.widened) {
          let paneCols = entry.fitCols || 0;
          try { const d = fitAddon.proposeDimensions && fitAddon.proposeDimensions(); if (d && d.cols) paneCols = d.cols; } catch {}
          if (paneCols && Math.abs(paneCols - (entry.fitCols || 0)) >= 2) {
            entry.fitCols = paneCols;
            clearTimeout(resyncTimer);
            resyncTimer = setTimeout(() => { resyncActiveConsole(); }, 220);
          }
          return;
        }
        safeFit();
        // The snapshot was server-rendered at a fixed column count. If a late layout settle (page
        // switch / flex-fill) changes the column count after that, the rendered snapshot is now the
        // wrong width ("narrow and bugged"). Re-fetch + repaint at the new size, debounced, so the
        // console self-heals instead of staying stuck at the mount-time width.
        if (entry && entry.term && entry.term.cols !== entry.renderedCols) {
          entry.renderedCols = entry.term.cols;
          entry.fitCols = entry.term.cols;
          clearTimeout(resyncTimer);
          resyncTimer = setTimeout(() => { resyncActiveConsole(); }, 220);
        }
      });
    });
    try { resizeObserver.observe(container); } catch {}
  }

  state.activeXterm = { terminalId, agentId, term, fitAddon, container, resizeObserver, wheelHandler: onWheel, lastSeq: -1, canInput, webgl: webglAddon, _themeAccent: terminalAccentColor() };

  // Replay existing buffered output so the operator sees history when they open the Console
  // pane mid-session (instead of waiting for the next byte to arrive).
  // Fit FIRST (next frame, after layout settles + with min-width:0 ancestors so fit() measures
  // the VISIBLE pane, not an overflowing one), THEN fetch the snapshot at the settled cols/rows.
  // Fetching before the fit settled rendered the snapshot too wide ("tries to compensate").
  // Double rAF: one frame to apply layout, a second so the flex-fill width is final before fit()
  // measures cols (a single frame can still read a transient narrow width on a fresh page switch).
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  safeFit();
  try {
    // Pass our (settled) grid size so the server renders a CLEAN current-screen snapshot (via the
    // headless VT emulator) instead of the raw byte log — replaying the raw log scrambles
    // full-screen TUIs. Prefer `snapshot`; fall back to raw `output` (e.g. pyte absent).
    const cols = Math.max(20, term.cols || 80), rows = Math.max(5, term.rows || 24);
    if (state.activeXterm) { state.activeXterm.renderedCols = term.cols; state.activeXterm.fitCols = term.cols; }
    const data = await api(`/terminals/${encodeURIComponent(terminalId)}?cols=${cols}&rows=${rows}`);
    // We OWN a managed PTY: fit it to the pane instead of stretching the pane to it. Only a
    // RESIDENT console is a mirror of a terminal we must not resize.
    //
    // Own the PTY ONLY when POSITIVELY managed (2026-07-19). Unknown / missing-agent / empty-mode
    // must fall through to false → we do NOT resize (a resident console mirrors the operator's real
    // terminal; SIGWINCHing it is the exact harm this guard prevents). The old `!== 'resident'`
    // failed OPEN: a not-yet-populated state.agents made an unknown mode read as owned. Fall back to
    // the session row's own mode so an absent agent object can't flip a resident console to "owned".
    const _tid = String(terminalId || '');
    const _sess = (state.sessions || []).find(
      (x) => String(x?.terminalId || x?.terminal?.id || x?.terminal_id || '') === _tid);
    const _mode = String(
      agentForTerminal(terminalId)?.sessionMode || _sess?.sessionMode || _sess?.session_mode || ''
    ).toLowerCase();
    const ownsPty = _mode === 'managed';
    applyRenderedWidth(state.activeXterm, term, container, data, ownsPty);
    if (state.activeXterm) state.activeXterm.ownsPty = ownsPty;

    // Force one real width transition on a PTY we own — do not wait for xterm's onResize.
    //
    // This is what actually un-garbles a console, and it took a browser to see it. These TUIs
    // paint by ABSOLUTE cursor position and never scroll (measured: zero newlines, 1160 CUP moves
    // per 64KB), and they repaint only what CHANGED. So a screen we started tracking mid-stream
    // keeps its wrong rows FOREVER — the operator's "gibberish", with two lines woven together
    // character by character. Nothing we do server-side can fix it, because the app will never
    // redraw those rows on its own.
    //
    // A genuine RESIZE does force a full repaint (verified live: the app emitted 23 chunks and
    // the screen came back clean). But `term.onResize` only fires when xterm's own size CHANGES,
    // and Linux sends no SIGWINCH for a same-size resize. Nudge one column and restore it before
    // pulling the freshly-repainted snapshot.
    if (ownsPty) {
      const c = Math.max(20, term.cols), r2 = Math.max(5, term.rows);
      try {
        await forceTerminalRepaint({
          cols: c,
          rows: r2,
          resize: (nextCols, nextRows) => api(`/terminals/${encodeURIComponent(terminalId)}/resize`, {
            method: 'POST',
            body: JSON.stringify({ cols: nextCols, rows: nextRows, requestedBy: 'dashboard-attach' }),
          }),
          waitForSize: (nextCols, nextRows) => awaitTerminalSize(terminalId, nextCols, nextRows),
        });
        await new Promise((res) => setTimeout(res, 700));   // let the app repaint
        const fresh = await api(`/terminals/${encodeURIComponent(terminalId)}?cols=${c}&rows=${r2}`);
        if (fresh?.terminal?.snapshot) data.terminal = fresh.terminal;
      } catch { /* best-effort: fall back to the snapshot we already have */ }
    }
    const snapshot = data?.terminal?.snapshot;
    const output = data?.terminal?.output;
    // reset() BEFORE seeding, exactly as the Refresh path does. The snapshot's own prefix only
    // clears the visible screen (ESC[2J) — it does not reset scrollback, charset, scroll region
    // or alt-screen state, so writing it into a REUSED xterm can leave stale rows and a stuck
    // line-drawing charset underneath ("____ everywhere"). A full reset makes the seed
    // self-contained no matter what the pane was showing before.
    try { term.reset(); } catch { /* xterm always has reset(); never block the seed */ }
    if (snapshot) term.write(String(snapshot));
    else if (output) term.write(String(output));
    // GET /terminals/{id} returns the buffer sequence as `outputSeq` (only the WS frame uses `seq`).
    // Reading `seq` here left lastSeq=-1, disabling dedup so the first live frames re-painted history.
    if (state.activeXterm) state.activeXterm.lastSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? state.activeXterm.lastSeq);
  } catch (err) {
    term.write(`\r\n\x1b[2m[history fetch failed: ${String(err?.message || err).replace(/\x1b/g, '')}]\x1b[0m\r\n`);
  }
  term.focus();
}


// Which agent owns this terminal? (Used to decide whether the PTY is OURS to resize.)
function agentForTerminal(terminalId) {
  const tid = String(terminalId || '');
  const sess = (state.sessions || []).find((x) => String(x?.terminalId || x?.terminal?.id || x?.terminal_id || '') === tid);
  if (sess) return agentForSession(sess);
  return (state.agents || []).find((a) => String(a?.runtimeState?.terminalId || a?.terminalId || '') === tid) || null;
}

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
function consoleAwaitingInputHint(text) {
  const tail = String(text || '').slice(-400).toLowerCase();
  if (!tail.trim()) return false;
  return /\((y\/n|yes\/no)\)|press enter|are you sure|continue\?|\[y\/n\]|overwrite\?|proceed\?/.test(tail);
}

function updateAwaitPill() {
  const pill = byId('console-await-pill');
  if (!pill) return;
  // Server-derived `blocked` (a real prompt paused the agent's spinner) is the
  // authoritative signal; the tail regex only catches generic y/n prompts the
  // status engine doesn't classify (e.g. plain-bash consoles).
  const agent = state.agents.find((a) => a.id === state.activeXterm?.agentId);
  const blocked = String(agent?.status || '').startsWith('blocked');
  pill.hidden = !blocked && !consoleAwaitingInputHint(state.activeXterm?.recentText || '');
}

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
function renderModeSwitchChip(agent) {
  if (!agent || typeof agent !== 'object') return '';
  const current = String(agent.sessionMode || '').toLowerCase();
  if (current !== 'resident' && current !== 'managed') return '';
  const target = current === 'resident' ? 'managed' : 'resident';
  return `<button class="ghost mode-switch-chip" data-mode-switch="${esc(agent.id)}" data-target-mode="${target}" title="Flip ${esc(agent.id)} to ${target} mode">Switch to ${target}</button>`;
}

// Optional inline label so operators can see the current sessionMode at a
// glance in the session header subtitle. Informational only.
function renderSessionModeLabel(agent) {
  const mode = String(agent?.sessionMode || '').toLowerCase();
  if (mode !== 'resident' && mode !== 'managed') return '';
  return ` · ${esc(mode)}`;
}

async function switchAgentSessionMode(agentId, targetMode, { force = false } = {}) {
  if (!agentId || !targetMode) return null;
  const url = `${apiBase}/agents/${encodeURIComponent(agentId)}/session-mode`;
  let res;
  try {
    res = await fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: targetMode, force, requestedBy: 'dashboard' }),
    });
  } catch (err) {
    inspect('Mode switch error', { agentId, targetMode, error: String(err?.message || err) });
    return null;
  }
  let body = null;
  try { body = await res.json(); } catch {}
  if (!res.ok) {
    // I10: an active run blocks the switch (409). Offer to force it, matching the old dashboard.
    if (res.status === 409 && !force) {
      const detail = body?.detail || body?.error || 'An active run is blocking the switch.';
      if (await uiConfirm(`${detail}\n\nForce the switch to ${targetMode} anyway?`)) {
        return switchAgentSessionMode(agentId, targetMode, { force: true });
      }
      return null;
    }
    toast(`Mode switch failed: ${body?.detail || body?.error || res.status}`, 'error');
    inspect('Mode switch failed', { agentId, targetMode, status: res.status, body });
    return null;
  }
  const updatedMode = String(body?.mode || targetMode);
  const existingAgent = state.agents.find((agent) => String(agent.id || '') === String(agentId));
  if (existingAgent && body?.agent) Object.assign(existingAgent, body.agent);
  else if (existingAgent) existingAgent.sessionMode = updatedMode;
  state.sessions.forEach((session) => {
    if (sessionAgentId(session) === String(agentId)) session.sessionMode = updatedMode;
  });
  renderSessionRail();
  renderSessionWorkspace();
  chatController.render();
  toast(`Switched ${agentId} to ${updatedMode}`, 'ok');
  refreshSoon();
  return body;
}

// Renders an agent's live console (PTY xterm / hermes iframe / codex synth / start-console
// offer) into `targetEl`. Defaults to the Sessions page summary pane, but the Chat page passes
// its own host so the same terminal widget is reachable inline from a conversation.
function renderSessionConsole(session, targetEl, opts = {}) {
  const host = targetEl || byId('session-console-summary');
  if (!host) return;
  // Dual-host xterm guard (2026-06-19 review): this runs for BOTH the Sessions summary host
  // AND the Chat inline console host, and renderSessionWorkspace() calls it on EVERY poll
  // regardless of the active page. state.activeXterm is a single global, so a HIDDEN host
  // re-rendering would dispose+re-mount the live xterm out from under the VISIBLE host → the
  // other pane goes black ("visible for a sec then black", now reachable cross-host since
  // auto-attach mounts terminals for far more sessions). A hidden host (its page/tab inactive →
  // display:none → offsetParent null) must be a no-op; only the visible host owns the mount.
  // setPage() re-renders on switch, so the console appears immediately when its page is shown.
  if (host.offsetParent === null) { host.__consoleWasHidden = true; return; }
  const id = sessionId(session);
  const status = String(session?.status || '').toLowerCase();
  const canStop = !['stopped', 'failed', 'lost', 'ended', 'completed', 'cancelled'].includes(status);
  const agent = agentForSession(session);
  const runtimeConfig = agent?.runtimeConfig || {};
  const runtime = String(agent?.runtime || '').toLowerCase();
  const hermesGatewayHttp = runtime === 'hermes'
    ? hermesGatewayUrlToHttp(runtimeConfig.gatewayUrl)
    : '';
  const codexAppServerUrl = runtime === 'codex' ? String(runtimeConfig.appServerUrl || '').trim() : '';
  const codexThreadId = runtime === 'codex'
    ? String(agent?.sessionHandle || runtimeConfig.threadId || agent?.runtimeState?.threadId || '').trim()
    : '';
  const codexIsLoopback = codexAppServerUrl && (() => {
    try { return ['127.0.0.1', 'localhost', '::1'].includes(new URL(codexAppServerUrl).hostname); }
    catch { return false; }
  })();
  const codexAttachable = codexAppServerUrl && codexIsLoopback;
  const agentIdForCodex = sessionAgentId(session) || '';
  const normalizedSessionMode = String(agent?.sessionMode || session?.sessionMode || session?.session_mode || '').toLowerCase();
  const isResident = normalizedSessionMode === 'resident';
  // When this console is embedded in the Chat conversation pane, the lifecycle actions
  // (Restart/Reset/Stop/Switch/Message-in-Chat) already live in the chat header + Details
  // drawer — so we don't duplicate them here (audit finding C5). Sessions page keeps the full set.
  const isChatSource = opts.source === 'chat';
  const connectActions = `${hermesGatewayHttp ? `<button class="ghost" data-action="open-hermes-tab" data-url="${esc(hermesGatewayHttp)}" title="Open the upstream Hermes browser UI in a separate tab">Open Hermes UI</button>` : ''}`
    + `${codexAttachable ? `<button class="ghost" data-action="codex-console-connect" data-agent-id="${esc(agentIdForCodex)}" data-app-server-url="${esc(codexAppServerUrl)}" data-thread-id="${esc(codexThreadId)}">Connect live console</button>` : ''}`;
  const _drawerAgentId = agent?.id || agentIdForCodex || '';
  const lifecycleActions = `${_drawerAgentId ? `<button class="ghost" data-agent-details="${esc(_drawerAgentId)}" title="Open the full lifecycle drawer (edit, handle, env, history, remove…)">Details</button>` : ''}`
    + `${agentIdForCodex ? `<button class="ghost" data-open-chat="${esc(agentIdForCodex)}" title="Message this agent in Chat">Message in Chat</button>` : ''}`
    + `${renderModeSwitchChip(agent)}`
    + `<button class="ghost" data-session-control="restart" data-session-id="${esc(id)}">Restart</button>`
    + `<button class="ghost" data-session-control="recreate" data-session-id="${esc(id)}" title="Restart with a FRESH context (discards native session)">Reset</button>`
    + `${canStop ? `<button class="ghost danger" data-session-control="stop" data-session-id="${esc(id)}">Stop</button>` : ''}`;
  const headerActions = isChatSource ? connectActions : (lifecycleActions + connectActions);
  // Lean action/meta bar — the agent name, status chip, and workspace already render in the panel
  // header (session-title / session-status / session-subtitle), so repeating them here was the
  // duplicated "doubled header". Keep only the runtime/env/mode meta line + the lifecycle actions.
  const headerCard = `
    <div class="session-actions-bar" data-kind="session" data-id="${esc(id)}">
      <small class="session-meta-line">${esc(sessionRuntime(session))} · ${esc(sessionEnvironmentId(session))}${hermesGatewayHttp ? ' · live tui_gateway' : ''}${codexAttachable ? ' · live app-server' : ''}${renderSessionModeLabel(agent)}</small>
      ${headerActions ? `<div class="contract-actions">${headerActions}</div>` : ''}
    </div>`;

  // For hermes resident agents with a live tui_gateway, embed the upstream
  // hermes web dashboard chat surface as an iframe. The dashboard runs at
  // http://127.0.0.1:<port>/ on the operator's machine; the operator's
  // browser is also on that machine, so loopback access works. This is
  // the real Ink Chat UI — interactive, typing-supported, full fidelity —
  // the same WS session the bridge attaches to via /api/ws. (See
  // ui-tui/src/gatewayClient.ts:resolveGatewayAttachUrl + the hermes
  // dashboard's embedded chat tab gated on HERMES_DASHBOARD_TUI=1.)
  // Widget choice is delegated to chooseSessionConsoleWidget (pure helper,
  // unit-tested in app.test.mjs). It caches the most-recent terminalId per
  // session so the widget doesn't oscillate when the server temporarily
  // clears runtime_state.virtualTerminalId — fixing the operator-reported
  // 2026-05-24 Bug #3 (iframe ↔ xterm flip mid-conversation triggered by
  // _stop_virtual_terminals_for_superseded_bridges running on every
  // list-sessions refresh).
  const widgetChoice = chooseSessionConsoleWidget({
    agent,
    sessionId: id,
    sessionMode: agent?.sessionMode || session?.sessionMode || session?.session_mode,
    sessionStatus: status,
    terminalStatus: session?.terminalStatus || session?.terminal_status || session?.terminal?.status,
    runtime,
    runtimeConfig,
    cache: state.sessionTerminals,
    hermesGatewayHttp,
    codexAppServerUrl,
    codexThreadId,
    codexAttachable,
    // Auto-attach: the session row itself may carry the live terminal id (terminal binding
    // recorded server-side on register/dispatch), so a running console mounts without the
    // operator pressing Start. chooseSessionConsoleWidget treats this as the lowest-priority
    // source (after runtime_state) so it never overrides the true owner's PTY.
    sessionTerminalId: session?.terminalId || session?.terminal?.id || session?.terminal_id || '',
  });
  const terminalId = widgetChoice.terminalId;
  const hasTerminal = widgetChoice.kind === 'xterm';
  // Console input is gated on whether a PTY xterm is actually MOUNTED (the chooser only mounts one
  // for a terminal that can represent the current owner) — NOT on session.status. The old gate
  // (canStop, from the narrow LIVE_SESSION_STATUSES) wrongly rejected input for a live-but-idle
  // `available` agent whose PTY exists, with a misleading "console is not live" toast — while the
  // backend /terminals/{id}/input accepts the keystroke anyway. (2026-06-29 fix.)
  const canConsoleInput = hasTerminal;
  const isVirtualTerminal = Boolean(agent?.runtimeState?.virtualTerminal);
  const ptyContainerId = hasTerminal ? `xterm-${terminalId}` : '';

  const ptyEmbed = hasTerminal
    ? `<div class="console-embed" data-kind="pty-xterm">
         <div class="console-embed-label">
           <span>${isVirtualTerminal ? 'Synth terminal' : 'Live PTY'} — <code>${esc(agent?.runtime || 'runtime')}</code> · terminal <code>${esc(terminalId)}</code>${isVirtualTerminal ? '' : ' · keystrokes flow back to the wrapper'}</span>
           <span class="console-toolbar">
             <span class="console-await-pill" id="console-await-pill" hidden>⌛ awaiting input</span>
             <button class="ghost" data-console-action="copy" title="Copy selection (or whole buffer) — Ctrl+Shift+C">Copy</button>
             <button class="ghost" data-console-action="refresh" title="Re-fetch the authoritative buffer and repaint">Refresh</button>
             ${canStop ? `<button class="ghost danger" data-console-action="stop" data-terminal-id="${esc(terminalId)}" title="Stop this terminal and return the agent to messenger ownership">Stop console</button>` : ''}
           </span>
         </div>
         <div id="${esc(ptyContainerId)}" class="xterm-host"></div>
       </div>`
    : '';

  // No live terminal yet (widgetChoice 'none') and the session is a MANAGED PTY-capable one:
  // offer to start a console. Resident agents are excluded — starting a managed console for a
  // resident identity would spawn a second process alongside the operator's own terminal
  // (audit finding C3); for those we show a switch-to-managed note instead. "Start fresh" is
  // only meaningful for pi without a saved handle (audit findings C1/C2), so we show a single
  // button otherwise — the truly-fresh path is the Reset (recreate) lifecycle action.
  // `!hermesGatewayHttp` was in this gate, so a hermes agent could NEVER be offered a console —
  // it got an iframe of the hermes web page instead. Hermes gets a real PTY console like any
  // other runtime (cms-tech-lead came up with 19KB of PTY output), so it may be started here too.
  const canStartConsole = widgetChoice.kind === 'none' && canStop && runtime && !isResident && !codexAttachable;
  // Runtime-agnostic: with no saved native handle there's nothing to resume, so starting IS a
  // fresh start (and sending freshContext lets handle-required runtimes start without a 409).
  // With a handle, a plain start resumes it; the truly-discard-and-restart path is Reset.
  const noSavedHandle = !String(agent?.sessionHandle || runtimeConfig.handle || runtimeConfig.threadId || '').trim();
  // A DEAD managed session previously showed NOTHING here (canStop false → no start offer,
  // and before the chooser's sessionDead guard it showed a stale dead-terminal xterm instead)
  // — the operator had no way to start the agent from the console view. Offer the session
  // RESTART (teardown + fresh spawn) as an explicit "Start agent" (operator ask 2026-07-02).
  const canStartDeadSession = widgetChoice.kind === 'none' && !canStop && runtime && !isResident;
  const startConsoleEmbed = canStartConsole
    ? `<div class="console-embed" data-kind="console-start">
         <div class="console-embed-label"><span>No live console for this session.</span></div>
         <div class="console-start-actions">
           ${noSavedHandle
             ? `<button class="primary" data-console-action="start-fresh" data-session-id="${esc(id)}" title="No saved native session — start a fresh console">Start fresh console</button>`
             : `<button class="primary" data-console-action="start" data-session-id="${esc(id)}" title="Resume this session's console">Start console</button>`}
         </div>
       </div>`
    : canStartDeadSession
    ? `<div class="console-embed" data-kind="console-start">
         <div class="console-embed-label"><span>This session is ${esc(status || 'stopped')} — no live console. The agent stays <em>available</em>: a message wakes it, or start it now.</span></div>
         <div class="console-start-actions">
           <button class="primary" data-session-control="restart" data-session-id="${esc(id)}" title="Spawn a fresh worker for this agent (resumes its saved session when one exists)">Start agent</button>
         </div>
       </div>`
    : '';

  // Resident agent with no embeddable widget: do NOT offer to start a managed console (that
  // would conflict with the operator's own CLI). Point them at the mode switch instead.
  const residentConsoleNote = (widgetChoice.kind === 'none' && isResident)
    ? `<div class="console-embed" data-kind="console-resident">
         <div class="console-embed-label"><span>${esc(agentIdForCodex || 'This agent')} is <strong>resident</strong> — its terminal is the CLI you launched (${esc(agent?.runtime || 'runtime')}-aify), not a dashboard-owned console.</span></div>
         <div class="console-start-actions">${renderModeSwitchChip(agent) || '<span class="em">Switch it to managed to get a dashboard console.</span>'}</div>
       </div>`
    : '';

  // The hermes gateway is NEVER embedded in the Console tab (operator, 2026-07-14: "it should
  // never show hermes local webpage.. cmon. we have button for that"). It hijacked the tab — you
  // opened Console to read the agent's console and got a web page — and because the iframe counted
  // as a live widget it suppressed the Start-console button too, so there was no way to reach the
  // console you came for. The gateway keeps its explicit "Open in new tab" action in the session
  // header (see connectActions). The chooser can no longer return `hermes-iframe`.
  const hermesIframe = '';

  // Codex doesn't have an upstream web UI to iframe, so we render the
  // JSON-RPC event stream ourselves. Operator clicks "Connect live
  // console" → browser WS direct to codex app-server (loopback only,
  // same security argument as the hermes iframe) → subscribes to the
  // agent's threadId → renders deltas + lifecycle markers + accepts
  // turn/start frames from the local input box. Falls back behind the
  // PTY render if the bridge owns a real terminal for this agent.
  const codexConsole = (widgetChoice.kind === 'codex-synth')
    ? `<div class="console-embed" data-kind="codex-app-server" data-codex-console="${esc(agentIdForCodex)}">
         <div class="console-embed-label">
           Codex live thread — attaches direct WS to <code>${esc(codexAppServerUrl)}</code>${codexThreadId ? ` · thread <code>${esc(codexThreadId)}</code>` : ''} (resident; switch to dashboard-spawned managed for true PTY render)
         </div>
         <div class="codex-console-stream" aria-live="polite"></div>
         <form class="codex-console-input" data-action="codex-console-send" data-agent-id="${esc(agentIdForCodex)}">
           <input type="text" placeholder="${codexThreadId ? 'Type to send turn/start into this thread...' : 'No threadId — read-only.'}" ${codexThreadId ? '' : 'disabled'}>
           <button type="submit" class="primary" ${codexThreadId ? '' : 'disabled'}>Send</button>
           <button type="button" class="ghost" data-action="codex-console-disconnect" data-agent-id="${esc(agentIdForCodex)}">Disconnect</button>
         </form>
       </div>`
    : '';

  // Re-render guard (2026-06-19): renderSessionConsole runs on EVERY poll-driven render.
  // Rewriting host.innerHTML destroys the live xterm DOM node, so the mounted PTY was
  // re-created every poll → "visible for a sec, then black" (operator-reported, hermes AND
  // claude). Skip the rewrite when nothing that changes the rendered widget changed AND the
  // xterm is still mounted to this host — the live terminal then persists across polls.
  // Live status/meta that must stay fresh lives in the panel header (renderSessionWorkspace),
  // not in this console host, so this guard does not stale anything visible.
  const consoleKey = JSON.stringify([id, widgetChoice.kind, terminalId, hermesGatewayHttp, codexAppServerUrl, codexThreadId, canStop, isChatSource, isVirtualTerminal]);
  const xtermStillMounted = hasTerminal && state.activeXterm
    && state.activeXterm.terminalId === terminalId
    && host.contains(state.activeXterm.container);
  if (host.dataset.consoleKey === consoleKey && (!hasTerminal || xtermStillMounted)) {
    if (hasTerminal && state.activeXterm) state.activeXterm.canInput = canConsoleInput;
    // Re-show resync (bughunt 2026-07-03): while this host was hidden (page switched
    // away) terminal_output frames hit the offsetParent early-return before lastSeq
    // advanced, so they were dropped — and an idle agent emits no new frame to trip the
    // seq-gap resync. On return, repaint the authoritative buffer once (mirrors the
    // WS-reconnect resync). Guard on the mounted xterm; clear the flag so it's one-shot.
    if (host.__consoleWasHidden) {
      host.__consoleWasHidden = false;
      if (hasTerminal && state.activeXterm && state.activeXterm.term) resyncActiveConsole().catch(() => {});
    }
    return;
  }
  host.dataset.consoleKey = consoleKey;
  host.__consoleWasHidden = false;

  // Close any live codex console WS before we rewrite innerHTML (bughunt 2026-07-03):
  // the rewrite detaches the codex container from the DOM but left the WebSocket open
  // with a stale map entry — one leaked socket per re-render / widget-kind change.
  if (agentIdForCodex) { try { codexConsoleClose(agentIdForCodex); } catch {} }

  host.innerHTML = `${headerCard}${ptyEmbed}${startConsoleEmbed}${residentConsoleNote}${hermesIframe}${codexConsole}`;

  // Mount xterm.js into the terminal container we just rendered. If a
  // different terminal was previously mounted, dispose its xterm first.
  // Query within `host` (not by global id) so a Chat-embedded console and the
  // Sessions console can't fight over a duplicate element id.
  if (hasTerminal) {
    const container = host.querySelector('.xterm-host');
    if (container) mountXtermForTerminal(terminalId, agentIdForCodex, container, { canInput: canConsoleInput }).catch(() => {});
  } else {
    disposeActiveXterm();
  }
}

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
const runTo = (r) => String(r.targetAgentId || r.target_agent || r.to || '');
const runRuntime = (r) => String(r.runtime || r.requestedRuntime || r.requested_runtime || '');

// Populate a filter <select> with distinct values, preserving the current selection.
function syncRunFilterOptions(id, values, current) {
  const sel = byId(id);
  if (!sel) return;
  const opts = ['', ...[...new Set(values.filter(Boolean))].sort()];
  const sig = opts.join('|');
  if (sel.dataset.optsSig === sig) { sel.value = current || ''; return; }
  sel.dataset.optsSig = sig;
  sel.innerHTML = opts.map((v) => `<option value="${esc(v)}"${v === (current || '') ? ' selected' : ''}>${v ? esc(v) : 'Any'}</option>`).join('');
}

function renderRuns() {
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
    note.textContent = `Showing ${runs.length} most recent matching ${status}run${runs.length === 1 ? '' : 's'}.`;
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
    </article>`).join('') || '<div class="empty-state"><span class="empty-icon">📨</span><strong>No dispatch runs</strong><p>Runs appear here when an agent sends or receives work. Adjust the filters above if you expected to see some.</p></div>';
  renderDiagnosticsBulkToolbar();
}

// (Phase 0.2 dead-code removal, 2026-06-16) renderAnalytics was never called and targeted
// analytics-grid / run-status-mix (absent from index.html). The analytics surface returns as
// a tab on the Control Room slice (Phase 1) consuming GET /analytics + GET /analytics/agent/{id}.

async function loadRunDetails(runId) {
  const result = await api(`/dispatch/runs/${encodeURIComponent(runId)}`);
  return result.run || result;
}

async function loadRunEvents(runId, { before = '', order = state.inspector.eventOrder || 'desc', limit = RUN_INSPECTOR_EVENT_LIMIT } = {}) {
  const params = new URLSearchParams();
  params.set('limit', String(Math.min(limit, RUN_INSPECTOR_EVENT_LIMIT)));
  params.set('order', order === 'asc' ? 'asc' : 'desc');
  if (before) params.set('before', before);
  return api(`/dispatch/runs/${encodeURIComponent(runId)}/events?${params.toString()}`);
}

// runStatusContext moved to ./status.js in v0.5.4.

// runInspectorCapabilities moved to ./run-inspector-controls.mjs in v0.5.4.

// runPendingControlCount moved to ./record-fields.mjs in v0.5.4.

// renderEventBody moved to ./run-event.mjs in v0.5.4.

// renderRunEvent moved to ./run-event.mjs in v0.5.4.

// renderRunInspectorControls moved to ./run-inspector-controls.mjs in v0.5.4.

function renderRunInspector() {
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

async function openRunInspector({ runId, source = 'programmatic', sourceMessageId = '' } = {}) {
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
async function openCompactionHistory(agentId) {
  byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Loading…</p></div>`;
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  state.inspector = { ...state.inspector, kind: 'history', runId: '', agentId };
  let rows = [];
  try {
    const res = await api('/spawn-requests');
    const reqs = res.spawnRequests || res.requests || res || [];
    rows = (Array.isArray(reqs) ? reqs : []).filter((r) => {
      const m = r.metadata || {};
      return m.continuedFromAgentId === agentId || r.agentId === agentId || r.agent_id === agentId;
    }).sort((a, b) => String(b.createdAt || b.created_at || '').localeCompare(String(a.createdAt || a.created_at || '')));
  } catch (err) {
    byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Could not load spawn records: ${esc(String(err?.message || err))}</p></div>`;
    return;
  }
  const body = rows.length ? rows.map((r) => {
    const m = r.metadata || {};
    const mode = m.splitIdentity ? 'Continue-as' : m.compactMode === 'handoff' ? 'Compact' : 'Spawn';
    return `<div class="history-row">
      <div class="history-head"><strong>${esc(mode)}</strong>${renderStatusChip(r.status || 'queued', { label: esc(r.status || 'queued'), why: `Spawn request ${r.status || 'queued'}.` })}</div>
      <dl class="agent-drawer-kv">
        <dt>When</dt><dd>${esc(relTime(r.createdAt || r.created_at))} ago</dd>
        <dt>New agent</dt><dd>${esc(r.agentId || r.agent_id || '—')}</dd>
        ${m.continuedFromAgentId ? `<dt>From agent</dt><dd>${esc(m.continuedFromAgentId)}</dd>` : ''}
        ${m.continuedFromSessionId ? `<dt>From session</dt><dd class="clip">${esc(m.continuedFromSessionId)}</dd>` : ''}
        ${r.subject ? `<dt>Subject</dt><dd class="clip">${esc(r.subject)}</dd>` : ''}
      </dl></div>`;
  }).join('') : '<div class="empty-state"><span class="empty-icon">🕮</span><strong>No history</strong><p>No compaction or continuation records found for this agent.</p></div>';
  byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Compact/continue lineage from spawn records.</p>${body}</div>`;
}

// I3 — edit agent identity: rename, description, native session handle.
// openAgentEditForm moved to ./inspector-forms.mjs in v0.5.4.

async function submitAgentEdit(agentId) {
  const agent = state.agents.find((a) => a.id === agentId) || {};
  const newId = byId('edit-agent-id')?.value.trim() || agentId;
  const desc = byId('edit-agent-desc')?.value ?? '';
  const handle = byId('edit-agent-handle')?.value.trim() ?? '';
  const willRename = !!(newId && newId !== agentId);
  // Confirm the rename UP FRONT — confirming after the other edits already fired left those
  // writes applied with no toast/refresh when the operator cancelled the rename prompt.
  if (willRename && !await uiConfirm(`Rename "${agentId}" → "${newId}"? Chats/sessions/records move to the new id.`)) return;
  try {
    if (desc !== (agent.description || '')) {
      await api(`/agents/${encodeURIComponent(agentId)}/description`, { method: 'PATCH', body: JSON.stringify({ description: desc }) });
    }
    if (handle !== String(agent.sessionHandle || agent.session_handle || '')) {
      await api(`/agents/${encodeURIComponent(agentId)}/session-handle`, { method: 'PATCH', body: JSON.stringify({ sessionHandle: handle }) });
    }
    const envId = byId('edit-agent-env')?.value.trim() || '';
    if (envId) {
      const runtime = byId('edit-agent-runtime')?.value.trim() || '';
      const workspace = byId('edit-agent-workspace')?.value.trim() || '';
      const body = { environmentId: envId };
      if (runtime) body.runtime = runtime;
      if (workspace) body.workspace = workspace;
      await api(`/agents/${encodeURIComponent(agentId)}/environment`, { method: 'POST', body: JSON.stringify(body) });
    }
    if (willRename) {
      await api(`/agents/${encodeURIComponent(agentId)}/rename`, { method: 'POST', body: JSON.stringify({ newAgentId: newId }) });
    }
    toast('Agent updated', 'ok');
    closeInspector();
    await refresh();
  } catch (err) { toast(`Edit failed: ${err?.message || err}`, 'error'); }
}

// Sticky session identity (governance): resolve a `session-changed` agent by
// confirming the new (pending) id or keeping the pinned handle. Both endpoints
// clear the pending id and exit the session-changed state.
async function resolveAgentSession(agentId, mode) {
  const path = mode === 'confirm' ? 'session/confirm' : 'session/keep';
  const label = mode === 'confirm' ? 'Confirm new session id' : 'Keep pinned handle';
  try {
    const res = await api(`/agents/${encodeURIComponent(agentId)}/${path}`, { method: 'POST', body: JSON.stringify({ requestedBy: 'dashboard' }) });
    // `keep` surfaces the runtime resume command so the operator can re-attach the
    // agent onto the pinned id. Show it in a prompt-style dialog for easy copy.
    if (mode === 'keep' && res && res.resumeCommand) {
      await uiPrompt('Re-attach the agent to its pinned session with this command:', { defaultValue: res.resumeCommand, confirmLabel: 'Done' });
    } else {
      toast(`${label}: done`, 'ok');
    }
    await refresh();
    openAgentDrawer(agentId);
  } catch (err) { toast(`${label} failed: ${err?.message || err}`, 'error'); }
}

// F8 — message detail surface in the inspector.
// openMessageDetail moved to ./inspector-forms.mjs in v0.5.4.

// F1 — Compact / Continue-as (handoff packet UX). Build a packet from recent messages and
// render an editable continuation form into the inspector; submit creates a managed-warm
// spawn-request seeded with the packet (POST /spawn-requests), same mechanism as 8800.
// buildHandoffPacket moved to ./inspector-forms.mjs in v0.5.4.

// openContinueForm moved to ./inspector-forms.mjs in v0.5.4.

async function submitContinue(sid, splitIdentity) {
  const target = state.sessions.find((s) => String(sessionId(s)) === String(sid));
  if (!target) { toast('Session not found', 'error'); return; }
  const v = (id) => byId(id)?.value?.trim() || '';
  const sourceAgent = sessionAgentId(target) || '';
  const newAgentId = splitIdentity ? v('cont-agent-id') : (v('cont-agent-id') || sourceAgent);
  if (!newAgentId) { toast('Agent ID is required', 'error'); return; }
  try {
    await api('/spawn-requests', {
      method: 'POST',
      body: JSON.stringify({
        createdBy: 'dashboard', environmentId: v('cont-env') || sessionEnvironmentId(target),
        agentId: newAgentId, role: v('cont-role') || 'coder', runtime: v('cont-runtime') || sessionRuntime(target),
        workspace: v('cont-workspace') || target.workspace || target.cwd, initialMessage: v('cont-packet'),
        subject: splitIdentity ? `Continue as from ${sourceAgent}` : `Handoff compact from ${sourceAgent}`,
        mode: 'managed-warm', resumePolicy: 'fresh_context',
        metadata: { continuedFromSessionId: sid, continuedFromAgentId: sourceAgent, compactMode: 'handoff', sameAgentId: newAgentId === sourceAgent, splitIdentity },
      }),
    });
    toast(splitIdentity ? `Continue-as queued for ${newAgentId}` : `Compact queued for ${newAgentId}`, 'ok');
    closeInspector();
    refreshSoon();
    setPage('environments');
  } catch (err) { toast(`Continue failed: ${err?.message || err}`, 'error'); }
}

async function removeAgent(agentId) {
  if (!agentId) return;
  if (!await uiConfirm(`Remove agent "${agentId}"? This tombstones the identity.`)) return;
  try {
    await api(`/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
    toast(`Removed ${agentId}`, 'ok');
    closeInspector();
    refreshSoon();
  } catch (err) { toast(`Remove failed: ${err?.message || err}`, 'error'); }
}

// Kill a live worker from the details drawer, keyed on the AGENT rather than a session row
// (2026-07-26). /agents/{id}/stop-worker is the authoritative teardown: it ends the live
// agent_sessions rows, terminal bindings, virtual-terminal pointer and turn_busy pulse, and the
// agent reports `available`. Identity, history and the resume handle survive, so this is "stop",
// not "remove" — the agent can be started again from the same drawer.
// Confirmed because it kills real running work.
async function stopAgentWorker(agentId) {
  if (!agentId) return;
  if (!await uiConfirm(
    `Stop ${agentId}'s live worker?\n\n`
    + 'Any turn it is running is lost. Its identity, history and resume handle are kept, '
    + 'so you can start it again.',
  )) return;
  try {
    await api(`/agents/${encodeURIComponent(agentId)}/stop-worker`, {
      method: 'POST',
      body: JSON.stringify({ requestedBy: 'dashboard' }),
    });
    toast(`Stopped ${agentId}'s worker`, 'ok');
    // AWAIT the refresh before re-rendering (review 2026-07-26). Rendering straight after the POST
    // painted the drawer from the PRE-stop `state.agents`, so it still showed the old status and a
    // live "Stop worker" button for a worker that was already gone. Pull fresh state first, then
    // re-render, so the drawer reflects the real post-stop status.
    try { await refresh(); } catch { /* keep the drawer usable even if that poll failed */ }
    openAgentDrawer(agentId);
  } catch (err) { toast(`Stop failed: ${err?.message || err}`, 'error'); }
}

async function deleteSessionById(sid) {
  if (!sid) return;
  if (!await uiConfirm('Delete this session record?')) return;
  try {
    await api(`/sessions/${encodeURIComponent(sid)}`, { method: 'DELETE' });
    toast('Session deleted', 'ok');
    closeInspector();
    refreshSoon();
  } catch (err) { toast(`Delete failed: ${err?.message || err}`, 'error'); }
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

function setNavCollapsed(collapsed) {
  const shell = byId('app-shell');
  shell?.classList.toggle('nav-collapsed', Boolean(collapsed));
  byId('toggle-nav')?.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
  byId('toggle-nav')?.setAttribute('title', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  localStorage.setItem('aify.next.navCollapsed', collapsed ? '1' : '0');
}

function preferredNavCollapsed() {
  const stored = localStorage.getItem('aify.next.navCollapsed');
  if (stored) return stored === '1';
  return window.matchMedia('(max-width: 760px)').matches;
}

async function requestRunControl(runId) {
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

async function requestSessionControl(sessionId, action, confirmAction = true, refreshAfter = true) {
  const labels = {
    stop: 'stop this session',
    restart: 'restart this session using its saved backing',
    recreate: 'RESET this session with a fresh context (the current native session is discarded)',
  };
  if (!sessionId || !action) return;
  if (confirmAction && !await uiConfirm(`Really ${labels[action] || action}?`)) return;
  try {
    await api(`/sessions/${encodeURIComponent(sessionId)}/control`, {
      method: 'POST',
      body: JSON.stringify({
        action,
        from_agent: 'dashboard',
        body: `Session ${action} requested from Dashboard Next.`,
      }),
    });
    if (refreshAfter) await refresh();
  } catch (err) { toast(`Session ${action} failed: ${err?.message || err}`, 'error'); }
}

async function requestBulkSessionControl(action) {
  const ids = selectedSessionIds();
  if (!ids.length || !action) return;
  if (!await uiConfirm(`Really ${action} ${ids.length} selected session${ids.length === 1 ? '' : 's'}?`)) return;
  for (const id of ids) {
    if (action === 'delete') {
      try { await api(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch (err) { toast(`Delete ${id} failed: ${err?.message || err}`, 'error'); }
    } else {
      // Isolate per-item failures so one bad session doesn't abort the rest of the batch
      // (and skip the trailing clear()/refresh()).
      try { await requestSessionControl(id, action, false, false); } catch (err) { toast(`${action} ${id} failed: ${err?.message || err}`, 'error'); }
    }
  }
  state.selectedSessionIds.clear();
  await refresh();
}

async function patchRun(runId, payload) {
  return api(`/dispatch/runs/${encodeURIComponent(runId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

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

// Deep-link to the Chat page for a given agent (used by "Message in Chat" + message threads).
function openAgentChat(agentId) {
  if (!agentId || agentId === 'dashboard') { setPage('chat'); return; }
  setPage('chat');
  // "Message in Chat" must land on the messenger, not follow a stale open analytics panel.
  state.chat.analytics = { agent: '', data: null };
  chatController.open(`dm:${agentId}`);
  if (!state.chat.peek) markConversationRead(agentId, { quiet: true }); // respect Peek mode on deep-link opens too
  byId('chat-composer-body')?.focus();
}

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

async function sendRunFollowup(run, { retry = false, body = '' } = {}) {
  const target = runTargetAgent(run);
  if (!target) return;
  const text = body || run.body || run.summary || run.subject || `Follow-up for ${run.id}`;
  await sendMessageWithTimeout({
    from_agent: 'dashboard',
    to: target,
    type: run.type || 'request',
    priority: run.priority || 'normal',
    subject: retry ? `Retry: ${run.subject || run.id}` : `Queue after ${run.id}`,
    body: text,
    trigger: true,
    queueIfBusy: true,
    requireReply: true,
    inReplyTo: run.messageId || run.message_id || '',
  });
}

async function handleRunInspectorControl(action) {
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

async function loadMoreRunEvents() {
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

async function toggleRunEventOrder() {
  if (!state.inspector.runId) return;
  state.inspector.eventOrder = state.inspector.eventOrder === 'desc' ? 'asc' : 'desc';
  state.inspector.events = [];
  const page = await loadRunEvents(state.inspector.runId, { order: state.inspector.eventOrder, limit: RUN_INSPECTOR_EVENT_LIMIT });
  state.inspector.events = page.events || [];
  state.inspector.hasMore = Boolean(page.hasMore);
  renderRunInspector();
}

async function sendMessageWithTimeout(payload, timeoutMs = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await api('/messages/send', {
      method: 'POST',
      signal: controller.signal,
      body: JSON.stringify(payload),
    });
  } finally {
    clearTimeout(timer);
  }
}

function pastedImageName(blob) {
  const ext = String(blob?.type || 'image/png').split('/')[1]?.replace(/[^a-z0-9]/gi, '') || 'png';
  return `img-${Date.now()}.${ext}`;
}

async function uploadPastedImage(blob, targetEl) {
  if (!blob || !targetEl) return;
  const name = pastedImageName(blob);
  const form = new FormData();
  form.append('from_agent', 'dashboard');
  form.append('name', name);
  form.append('description', 'Pasted image from Dashboard Next');
  form.append('file', blob, name);
  const response = await fetch(`${apiBase}/shared`, { method: 'POST', body: form });
  const result = await response.json().catch(() => ({}));
  if (!response.ok || result.ok === false) throw new Error(result.detail || result.error || 'Image upload failed');
  const link = `${apiBase}/shared/${encodeURIComponent(name)}`;
  const ref = `[image: ${name}] ${link}`;
  const current = targetEl.value || '';
  targetEl.value = current ? `${current}${current.endsWith('\n') ? '' : '\n'}${ref}` : ref;
  targetEl.dispatchEvent(new Event('input', { bubbles: true }));
  targetEl.focus();
}

function lookup(kind, id) {
  const maps = {
    agent: state.agents,
    contract: state.contracts,
    message: state.messages,
    run: state.runs,
    session: state.sessions,
    environment: state.environments,
  };
  return (maps[kind] || []).find((item) => String(item.id || item.messageId) === String(id));
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

function updateStaticLinks() {
  const legacy = byId('legacy-dashboard-link');
  if (legacy) legacy.href = `${apiOrigin}/api/v1/dashboard`;
}

document.addEventListener('click', (event) => {
  const settingsTab = event.target.closest('[data-settings-tab]');
  if (settingsTab) {
    state.settingsTab = settingsTab.dataset.settingsTab;
    try { localStorage.setItem('aifySettingsTab', state.settingsTab); } catch { /* ignore */ }
    renderSettings();
    return;
  }
  const themeChoice = event.target.closest('[data-theme-choice]');
  if (themeChoice) {
    const key = themeChoice.dataset.themeChoice;
    const sel = byId('set-dashboard_theme');
    if (sel) sel.value = key;
    // Selecting a preset resets the custom color pickers to that preset's palette.
    const preset = THEMES[key] || THEMES.default;
    const setColor = (k, v) => { const el = byId(`set-${k}`); if (el) el.value = v; };
    setColor('dashboard_primary_color', preset.accent);
    setColor('dashboard_secondary_color', preset.secondary);
    setColor('dashboard_tertiary_color', preset.tertiary);
    document.querySelectorAll('#theme-preview-grid .theme-preview').forEach((tile) => {
      tile.classList.toggle('active', tile.dataset.themeChoice === key);
    });
    previewAppearance();
    return;
  }
  const favToggle = event.target.closest('[data-fav-toggle]');
  if (favToggle) {
    event.stopPropagation();
    toggleFavorite(favToggle.dataset.favToggle);
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
    const msg = state.messages.find((m) => messageId(m) === chatReply.dataset.chatReply);
    if (msg) {
      state.chat.replyTo = { id: messageId(msg), from: msg.from || 'unknown', subject: msg.subject || '', preview: msg.body || msg.preview || '', conversationKey: state.chat.selected };
      chatController.renderConversation();
      byId('chat-composer-body')?.focus();
    }
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
    const key = chatOpen.dataset.chatOpen;
    // Click-again gesture: re-clicking the already-open conversation closes it back to the
    // chat overview (fleet stats + most-active). Per-agent analytics stays reachable via the
    // explicit "Analytics" action button. (Operator: re-click open chat → close + show stats.)
    if (key === state.chat.selected && !state.chat.analytics.agent) {
      chatController.close();
    } else {
      chatController.open(key);
      // Opening a DM marks its messages read — UNLESS Peek mode is on (watch without marking).
      if (!state.chat.peek && key.startsWith('dm:')) markConversationRead(key.slice('dm:'.length), { quiet: true });
    }
    return;
  }
  const pulseWindow = event.target.closest('[data-pulse-window]');
  if (pulseWindow) {
    const mins = Number(pulseWindow.dataset.pulseWindow) || 60;
    if (mins !== state.chat.pulse.window) {
      state.chat.pulse.window = mins;
      chatController.refreshPulse(true);
    }
    return;
  }
  const chatView = event.target.closest('[data-chat-view]');
  if (chatView) {
    const next = chatView.dataset.chatView === 'console' ? 'console' : 'messenger';
    if (next !== state.chat.view) {
      state.chat.view = next;
      if (next === 'messenger') disposeActiveXterm(); // free the inline terminal when leaving Console
      chatController.renderConversation();
    }
    return;
  }
  // MUST stay scoped to button[...]: the grid section itself carries data-work-view as a
  // CSS state attribute, so a bare [data-work-view] closest() matches EVERY click inside
  // Work and swallows Inspect/Remind/Close (live regression 2026-07-02).
  const workView = event.target.closest('button[data-work-view]');
  if (workView) {
    const v = workView.dataset.workView;
    const grid = document.querySelector('.diagnostics-grid');
    if (grid) grid.setAttribute('data-work-view', v);
    document.querySelectorAll('button[data-work-view]').forEach((b) => { const on = b.dataset.workView === v; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
    try { localStorage.setItem('aifyWorkView', v); } catch { /* private mode */ }
    return;
  }
  // Work Loop List ⇄ Board layout toggle. Scoped to button[data-contract-view] for
  // the same reason as work-view above (avoid swallowing card actions).
  const contractView = event.target.closest('button[data-contract-view]');
  if (contractView) {
    const v = contractView.dataset.contractView === 'board' ? 'board' : 'list';
    state.contractView = v;
    try { localStorage.setItem('aifyContractView', v); } catch { /* private mode */ }
    renderContracts();
    return;
  }
  const diagJump = event.target.closest('[data-diag-jump]');
  if (diagJump) {
    const v = diagJump.dataset.diagJump || '';
    if (v.startsWith('run:')) {
      const sel = byId('run-status-filter'); if (sel) { sel.value = v.slice(4); sel.dispatchEvent(new Event('change', { bubbles: true })); }
    } else {
      const sel = byId('contract-state'); if (sel) { sel.value = v; sel.dispatchEvent(new Event('change', { bubbles: true })); }
    }
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
    requestSessionControl(agentControl.dataset.session, agentControl.dataset.agentControl)
      .catch((err) => toast(`Action failed: ${err?.message || err}`, 'error'));
    return;
  }
  const agentMode = event.target.closest('[data-agent-mode]');
  if (agentMode) {
    switchAgentSessionMode(agentMode.dataset.agent, agentMode.dataset.agentMode)
      .catch((err) => toast(`Mode switch failed: ${err?.message || err}`, 'error'));
    return;
  }
  const agentOpenSessions = event.target.closest('[data-agent-open-sessions]');
  if (agentOpenSessions) {
    const sid = agentOpenSessions.dataset.agentOpenSessions;
    if (sid) { state.selectedSessionId = sid; renderSessionWorkspace(); }
    setPage('sessions');
    closeInspector();
    return;
  }
  const toggleSuperseded = event.target.closest('[data-toggle-superseded]');
  if (toggleSuperseded) {
    state.showSupersededSessions = !state.showSupersededSessions;
    renderSessionRail();
    return;
  }
  const sessionStatusPreset = event.target.closest('[data-session-status-preset]');
  if (sessionStatusPreset) {
    const which = sessionStatusPreset.dataset.sessionStatusPreset;
    state.sessionStatusFilter = new Set(which === 'all' ? SESSION_FILTER_KINDS : which === 'live' ? SESSION_LIVE_KINDS : []);
    persistSessionStatusFilter();
    renderSessionWorkspace();
    return;
  }
  const sessionStatusFilter = event.target.closest('[data-session-status-filter]');
  if (sessionStatusFilter) {
    const k = sessionStatusFilter.dataset.sessionStatusFilter;
    if (state.sessionStatusFilter.has(k)) state.sessionStatusFilter.delete(k);
    else state.sessionStatusFilter.add(k);
    persistSessionStatusFilter();
    renderSessionWorkspace();
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
    chatChannelAction(chanAction.dataset.chatChannelAction, chanAction.dataset.channel)
      .catch((err) => toast(`Channel action failed: ${err?.message || err}`, 'error'));
    return;
  }
  const chanAddMember = event.target.closest('[data-channel-add-member]');
  if (chanAddMember) { addChannelMember(chanAddMember.dataset.channelAddMember); return; }
  const chanRemoveMember = event.target.closest('[data-channel-remove-member]');
  if (chanRemoveMember) { removeChannelMember(chanRemoveMember.dataset.channelRemoveMember, chanRemoveMember.dataset.member); return; }
  const fileDelete = event.target.closest('[data-file-delete]');
  if (fileDelete) {
    deleteSharedFile(fileDelete.dataset.fileDelete)
      .catch((err) => toast(`Delete failed: ${err?.message || err}`, 'error'));
    return;
  }
  const openHermesTab = event.target.closest('[data-action="open-hermes-tab"]');
  if (openHermesTab) {
    const url = openHermesTab.dataset.url;
    if (url) window.open(url, '_blank', 'noopener,noreferrer');
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
    const action = consoleAction.dataset.consoleAction;
    if (action === 'copy') copyActiveConsole();
    else if (action === 'refresh') resyncActiveConsole({ forceRepaint: true }).then(() => toast('Console refreshed', 'ok')).catch(() => {});
    else if (action === 'stop') stopConsoleTerminal(consoleAction.dataset.terminalId);
    else if (action === 'start') startConsoleForSession(consoleAction.dataset.sessionId, false);
    else if (action === 'start-fresh') startConsoleForSession(consoleAction.dataset.sessionId, true);
    return;
  }
  // Start a managed agent that has NO session at all (the cold-agent case — there was no way to
  // do this from the dashboard before). Spawns a worker through the same path a send uses, so a
  // saved session handle is RESUMED, not discarded.
  const agentAction = event.target.closest('[data-agent-action="start"]');
  if (agentAction) {
    const id = agentAction.dataset.agentId;
    agentAction.disabled = true;
    agentAction.textContent = 'Starting…';
    api(`/agents/${encodeURIComponent(id)}/control`, { method: 'POST', body: JSON.stringify({ action: 'start', from_agent: 'dashboard' }) })
      .then((r) => {
        toast(r?.alreadyRunning ? `${id} is already running` : `Starting ${id} — the console appears once its worker is up`, 'ok');
        refreshSoon();
      })
      .catch((err) => {
        toast(`Start agent failed: ${err?.message || err}`, 'error');
        agentAction.disabled = false;
        agentAction.textContent = 'Start agent';
      });
    return;
  }
  const analyticsRange = event.target.closest('[data-analytics-range]');
  if (analyticsRange) {
    state.analytics.range = rangeDef(analyticsRange.dataset.analyticsRange).key;
    loadAnalytics(true);
    return;
  }
  const page = event.target.closest('[data-page], [data-page-jump]')?.dataset.page || event.target.closest('[data-page-jump]')?.dataset.pageJump;
  if (page) {
    setPage(page);
    // Lazy-load the analytics page the first time it's opened (and refresh on re-open).
    if (page === 'analytics') loadAnalytics(true);
    return;
  }
  const diagnosticSelect = event.target.closest('[data-diagnostic-select]');
  if (diagnosticSelect) {
    const key = diagnosticKey(diagnosticSelect.dataset.diagnosticKind || 'run', diagnosticSelect.dataset.diagnosticSelect);
    if (diagnosticSelect.checked) state.selectedDiagnosticIds.add(key);
    else state.selectedDiagnosticIds.delete(key);
    renderDiagnosticsBulkToolbar();
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
    setPage('environments');
    renderEnvironmentSpawnOptions(envSpawn.dataset.envSpawn);
    byId('env-spawn-agent-id')?.focus();
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
    const id = sessionCheckbox.dataset.sessionCheckbox;
    if (sessionCheckbox.checked) state.selectedSessionIds.add(id);
    else state.selectedSessionIds.delete(id);
    renderSessionWorkspace();
    return;
  }
  // Mode-switch chips can live inside selectable session rows. Handle them
  // before row selection so the click reaches PATCH /agents/{id}/session-mode.
  const modeSwitchButton = event.target.closest('[data-mode-switch]');
  if (modeSwitchButton) {
    event.preventDefault();
    event.stopPropagation();
    const agentId = modeSwitchButton.dataset.modeSwitch;
    const targetMode = modeSwitchButton.dataset.targetMode;
    switchAgentSessionMode(agentId, targetMode);
    return;
  }
  const sessionSelect = event.target.closest('[data-session-select]');
  if (sessionSelect) {
    state.selectedSessionId = sessionSelect.dataset.sessionSelect;
    const session = selectedSession();
    state.selectedConversation = session ? sessionAgentId(session) || 'dashboard' : 'dashboard';
    renderSessionWorkspace();
    return;
  }
  const sessionTab = event.target.closest('[data-session-tab]');
  if (sessionTab) {
    state.selectedSessionTab = sessionTab.dataset.sessionTab || 'console';
    renderSessionWorkspace();
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
  if (event.key === 'Escape') {
    closeStatusWhy();
    // Escape also dismisses the inspector/agent drawer when it's open and focus isn't in a field.
    if (byId('inspector')?.classList.contains('open') && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) closeInspector();
  }
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-status-why]')) {
    event.preventDefault();
    openStatusWhy(event.target);
  }
  // Keyboard-operable favorite star (role=button span) — WS-L a11y.
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-fav-toggle]')) {
    event.preventDefault();
    toggleFavorite(event.target.dataset.favToggle);
  }
  // Ctrl+Shift+C copies the console when it has a selection (xterm swallows plain Ctrl+C as
  // SIGINT into the PTY, so the copy shortcut is shifted — parity with the old dashboard).
  if (event.ctrlKey && event.shiftKey && (event.key === 'C' || event.key === 'c') && state.activeXterm?.term) {
    event.preventDefault();
    copyActiveConsole();
  }
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
function persistChatPrefs() {
  try {
    localStorage.setItem('aify.next.chatPrefs', JSON.stringify({
      liveOnly: state.chat.liveOnly, openOnly: state.chat.openOnly,
      workingUp: state.chat.workingUp, unreadOnly: state.chat.unreadOnly,
      scope: state.chat.scope, statusFilter: [...(state.chat.statusFilter || [])],
      sortMode: state.chat.sortMode, compact: state.chat.compact, peek: state.chat.peek,
    }));
  } catch { /* ignore */ }
}
// Reflect filter state into the always-visible chip bar (chips are static markup; only their
// active class tracks state, so the rail re-render never has to rebuild them).
function syncChatChips() {
  // Mirror the visual .active state into aria-pressed so the toggle state isn't conveyed by
  // colour alone (matters for the status dots, which have no text).
  const press = (el, on) => { el.classList.toggle('active', on); el.setAttribute('aria-pressed', on ? 'true' : 'false'); };
  document.querySelectorAll('[data-chat-scope]').forEach((el) => press(el, el.dataset.chatScope === (state.chat.scope || 'all')));
  document.querySelectorAll('[data-chat-toggle]').forEach((el) => press(el, !!state.chat[el.dataset.chatToggle]));
  document.querySelectorAll('[data-chat-compact-toggle]').forEach((el) => press(el, !!state.chat.compact));
  document.querySelectorAll('[data-chat-peek-toggle]').forEach((el) => press(el, !!state.chat.peek));
  document.querySelector('.chat-shell')?.classList.toggle('compact', !!state.chat.compact);
  const sf = state.chat.statusFilter instanceof Set ? state.chat.statusFilter : new Set();
  document.querySelectorAll('[data-chat-status]').forEach((el) => press(el, sf.has(el.dataset.chatStatus)));
}
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
    state.chat.compact = !state.chat.compact;
    persistChatPrefs(); syncChatChips(); // syncChatChips toggles the .chat-shell.compact class
    return;
  }
  const peekBtn = event.target.closest('[data-chat-peek-toggle]');
  if (peekBtn) {
    // Peek mode: watch conversations without auto-marking their messages read on open.
    state.chat.peek = !state.chat.peek;
    persistChatPrefs(); syncChatChips();
    toast(state.chat.peek ? 'Peek mode on — opening a chat won’t mark it read' : 'Peek mode off', 'ok');
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
function persistChatDrafts() {
  try {
    const d = state.chat.drafts || {};
    const pruned = {};
    for (const k of Object.keys(d)) { if (String(d[k] || '').trim()) pruned[k] = d[k]; }
    localStorage.setItem('aifyChatDrafts', JSON.stringify(pruned));
  } catch { /* ignore quota/serialization */ }
}
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
async function loadVersionBadge() {
  const badge = byId('version-badge');
  if (!badge) return;
  try {
    const res = await fetch(`${apiOrigin}/version`);
    if (!res.ok) throw new Error(String(res.status));
    const v = await res.json();
    const behind = Number(v?.update?.behind_by || 0);
    const short = esc(v.sha_short || v.sha || '?');
    const branch = esc(v.branch || '');
    badge.textContent = behind > 0 ? `${short} · ${behind} behind` : short;
    badge.classList.toggle('behind', behind > 0);
    badge.title = behind > 0
      ? `Running ${short} (${branch}) — ${behind} commit${behind === 1 ? '' : 's'} behind origin. git pull && rebuild to update.`
      : `Running ${short} (${branch}) — up to date with origin.`;
  } catch (_) {
    badge.textContent = '';
    badge.title = 'Build version unavailable';
  }
}

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
