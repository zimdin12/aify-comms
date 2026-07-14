// Dashboard Next SPA entry. ES module (DASHBOARD_REBUILD_PLAN §0.1): pure cores live in
// sibling modules and are imported here; app.js remains the orchestrator (render + actions +
// the single delegated event handler + init) until later Phase-0 slices split those too.
import { esc, relTime, tsMs } from './util.js';
import { STATUS_KINDS, resolveStatus, renderStatusChip } from './status.js';
import { hermesGatewayUrlToHttp, chooseSessionConsoleWidget } from './console-chooser.js';
import { toast, uiConfirm, uiPrompt, installRejectionToast } from './ui.js';
import { createChatController } from './chat.js';
import { THEMES, applyTheme, applyCachedTheme, previewTheme, paletteFromSettings } from './theme.js';
import { trafficChartHtml, statCardsHtml, healthGridHtml, runStatusMixHtml, rangeSelectorHtml, rangeDef, opsKpisHtml, dispatchOutcomesHtml, agentLeaderboardHtml, busiestChannelsHtml, failureReasonsHtml } from './analytics.js';

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
const RUN_INSPECTOR_EVENT_LIMIT = 50;

const state = {
  loaded: false, // false until the first successful refresh — lets the chat rail show
                 // "Loading…" instead of "No agents." on a cold load.
  agents: [],
  contracts: [],
  // Work Loop layout: 'list' (flat) or 'board' (status columns). Persisted; the flat
  // list stays the default so nothing changes for anyone who doesn't opt in.
  contractView: (() => { try { return localStorage.getItem('aifyContractView') === 'board' ? 'board' : 'list'; } catch { return 'list'; } })(),
  messages: [],
  runs: [],
  sessions: [],
  environments: [],
  spawnRequests: [], // GET /spawn-requests — queued/claimed/failed/done spawns, surfaced on Environments.
  stats: {},
  files: [],
  // Plan 6 C3/C4/C5/C6: server settings snapshot (GET /api/v1/settings).
  // Mode-switch chips (Plan 6) and any other settings-gated UI consult
  // state.settings here. Empty object until first refresh completes.
  settings: {},
  terminalOwners: new Map(),
  activeXterm: null, // { terminalId, agentId, term, fitAddon, container } — xterm.js mounted into Session Console
  sessionTerminals: new Map(), // sessionId → most-recent terminalId seen for this session (cache prevents widget oscillation when the server clears runtime_state.virtualTerminalId mid-conversation per Bug #3 root cause)
  realtimeConnected: false,
  // Chat-first landing (Phase 1): conversation rail + timeline + composer state.
  chat: { identity: 'dashboard', selected: '', view: 'messenger', filter: '', liveOnly: false, openOnly: false, workingUp: false, unreadOnly: false, scope: 'all', statusFilter: new Set(), sortMode: 'activity', channels: [], channelMessages: {}, analytics: { agent: '', data: null }, pulse: { window: 60, data: null, loading: false, lastMs: 0 }, drafts: {}, replyTo: null, msgFilter: '', compact: false },
  selectedConversation: 'dashboard',
  selectedSessionId: '',
  selectedSessionTab: 'console', // Sessions = terminal-first (Console default); Activity is the read-only log
  selectedSessionIds: new Set(),
  selectedDiagnosticIds: new Set(),
  inspector: { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' },
  filter: '',
  runStatusFilter: '',
  runFromFilter: '', runToFilter: '', runRuntimeFilter: '', runSearch: '', // WS-H runs filters
  sessionStatusFilter: new Set(), // WS-F: status multiselect for the Sessions rail (empty = all)
  settingsTab: '', // active settings tab (empty → first group)
  // Global analytics page (WS-C). Lazily loaded when the page is first opened, then on refresh
  // while it stays active, and on range change. data === null until first load completes.
  analytics: { range: 'hour', data: null, loading: false, usage: null, consumption: null, usageStale: false, lastMs: 0 },
};

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
  settings: ['Settings', 'Curated service + dashboard configuration. Save writes to the live settings; rare knobs stay on classic.'],
};

const byId = (id) => document.getElementById(id);
let refreshTimer = null;
// In-flight guard: refresh() fires a ~10-request bundle; refreshSoon() can be triggered by
// every WS event. Without this, under poll load (slow single-worker service) bundles pile up
// faster than they drain and saturate the browser's ~6-connection-per-origin limit — which
// starves lazily-loaded pages (e.g. Analytics) of their own fetches. Coalesce: at most one
// bundle in flight; if more arrive while it runs, run exactly one more afterwards.
let _refreshInFlight = false;
let _refreshQueued = false;
let dashboardSocket = null;

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

function messageIdOf(m) { return String(m?.id || m?.messageId || m?.message_id || ''); }

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
function fileSizeLabel(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(1)} MB`;
}
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

function statusWhyContext(kind, item = {}, rawStatus = item.status || 'unknown', context = {}) {
  const base = resolveStatus(rawStatus, context);
  const parts = [];
  if (kind === 'session') {
    parts.push(`Session ${sessionAgentId(item) || sessionId(item) || 'unknown'} is ${base.label}.`);
    if (sessionEnvironmentId(item)) parts.push(`Environment: ${sessionEnvironmentId(item)}.`);
    if (sessionRuntime(item)) parts.push(`Runtime: ${sessionRuntime(item)}.`);
    if (item.workspace || item.cwd) parts.push(`Workspace: ${item.workspace || item.cwd}.`);
  } else if (kind === 'run') {
    parts.push(`Run ${item.id || 'unknown'} is ${base.label}.`);
    if (runTargetAgent(item)) parts.push(`Target: ${runTargetAgent(item)}.`);
    if (item.requestedAt) parts.push(`Requested ${relTime(item.requestedAt)} ago.`);
    if (item.startedAt) parts.push(`Started ${relTime(item.startedAt)} ago.`);
    if (item.error || item.blockedByActiveRun) parts.push(`Reason: ${item.error || item.blockedByActiveRun}.`);
  } else if (kind === 'contract') {
    parts.push(`Work Loop item ${item.subject || item.id || 'unknown'} is ${base.label}.`);
    if (item.targetAgentId) parts.push(`Target: ${item.targetAgentId}.`);
    if (item.lastReminderAt) parts.push(`Last reminder ${relTime(item.lastReminderAt)} ago.`);
    if (item.overdue) parts.push('It is overdue.');
  } else if (kind === 'agent') {
    parts.push(`Agent ${item.id || 'unknown'} is ${base.label}.`);
    if (item.runtime) parts.push(`Runtime: ${item.runtime}.`);
    if (item.statusNote || item.status_note) parts.push(`Note: ${item.statusNote || item.status_note}.`);
    if (item.lastSeen || item.last_seen) parts.push(`Last seen ${relTime(item.lastSeen || item.last_seen)} ago.`);
  } else if (kind === 'environment') {
    parts.push(`Environment ${item.label || item.id || 'unknown'} is ${base.label}.`);
    if (item.bridgeId || item.bridge_id) parts.push(`Bridge: ${item.bridgeId || item.bridge_id}.`);
    if (item.lastSeen || item.last_seen) parts.push(`Last heartbeat ${relTime(item.lastSeen || item.last_seen)} ago.`);
  } else {
    parts.push(`${kind || 'Item'} is ${base.label}.`);
  }
  return { ...context, label: context.label || base.label, why: parts.filter(Boolean).join(' ') };
}

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

function refreshSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

let _wsReconnectAttempts = 0;
function connectRealtimeSocket() {
  if (dashboardSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(dashboardSocket.readyState)) return;
  const wsOrigin = apiOrigin.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
  try {
    dashboardSocket = new WebSocket(`${wsOrigin}/ws`);
  } catch {
    state.realtimeConnected = false;
    return;
  }
  dashboardSocket.onopen = () => {
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
  dashboardSocket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data || '{}');
      applyRealtimeEvent(payload.event, payload.data || {});
    } catch {}
  };
  dashboardSocket.onclose = () => {
    state.realtimeConnected = false;
    // Exponential backoff (capped) instead of hammering /ws every 2.5s. The single-worker
    // service restarts on every deploy; a flat retry from every open tab piles load on exactly
    // when it's weakest. Reset to fast on a successful open (see onopen below).
    _wsReconnectAttempts = Math.min(_wsReconnectAttempts + 1, 6);
    const delay = Math.min(30000, 1500 * 2 ** _wsReconnectAttempts);
    setTimeout(connectRealtimeSocket, delay);
  };
}

function applyRealtimeEvent(event, data = {}) {
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

function asAgentArray(payload) {
  if (Array.isArray(payload.agents)) return payload.agents;
  return Object.entries(payload.agents || {}).map(([id, value]) => ({ id, ...value }));
}

function asArray(payload, key) {
  const value = payload?.[key];
  if (Array.isArray(value)) return value;
  if (value && typeof value === 'object') return Object.entries(value).map(([id, item]) => ({ id, ...item }));
  return [];
}

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

function filtered(items, fields) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => fields.some((field) => String(item[field] || '').toLowerCase().includes(needle)));
}

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
const EFFORT_OPTS = ['low', 'medium', 'high', 'xhigh'];
// Pi accepts an empty effort meaning "OMP default" — preserve that as a selectable option.
const PI_EFFORT_OPTS = ['', 'low', 'medium', 'high', 'xhigh'];
const SETTINGS_SCHEMA = [
  { group: 'Appearance', appearance: true, items: [
    { key: 'dashboard_theme', label: 'Color scheme', type: 'theme' },
    { key: 'dashboard_primary_color', label: 'Primary color', type: 'color', hint: 'Actions, brand, focus.' },
    { key: 'dashboard_secondary_color', label: 'Secondary color', type: 'color', hint: 'Selection, links.' },
    { key: 'dashboard_tertiary_color', label: 'Tertiary color', type: 'color', hint: 'Depth, charts.' },
    { key: 'dashboard_title', label: 'Dashboard title', type: 'text' },
  ] },
  { group: 'Status & lifecycle', items: [
    { key: 'resident_lease_seconds', label: 'Resident bridge lease (s)', type: 'number', min: 30, max: 3600 },
    { key: 'environment_offline_seconds', label: 'Environment offline after (s)', type: 'number', min: 30, max: 3600 },
    { key: 'agent_liveness_seconds', label: 'Agent offline after no heartbeat (s)', type: 'number', min: 30, max: 600 },
    { key: 'worker_idle_close_enabled', label: 'Auto-close idle managed workers', type: 'toggle' },
    { key: 'worker_idle_close_minutes', label: 'Idle close after (min)', type: 'number', min: 0, max: 1440 },
    { key: 'auto_confirm_session_id', label: 'Auto-confirm new session IDs', type: 'toggle' },
    { key: 'manual_session_mode', label: 'Show resident↔managed switch chips', type: 'toggle' },
  ] },
  { group: 'Reply contracts', items: [
    { key: 'reply_contracts_enabled', label: 'Reply contracts enabled', type: 'toggle' },
    { key: 'reply_reminder_minutes', label: 'First reminder after (min)', type: 'number', min: 1, max: 240 },
    { key: 'reply_reminder_repeat_minutes', label: 'Reminder repeat (min)', type: 'number', min: 1, max: 1440 },
    { key: 'reply_reminder_max_count', label: 'Max reminders (0 = unlimited)', type: 'number', min: 0, max: 20 },
    { key: 'reply_reminder_full_every', label: 'Full reminder every Nth (0 = always full)', type: 'number', min: 0, max: 20 },
    { key: 'contract_stale_hours', label: 'Contract history window (h)', type: 'number', min: 1, max: 720 },
  ] },
  { group: 'Managed runtimes', items: [
    { key: 'managed_terminal_backing_enabled', label: 'Terminal-backed managed sessions', type: 'toggle' },
    { key: 'insert_messages_via_console', label: 'Legacy PTY-input delivery', type: 'toggle', hint: 'Default off — scrambles concurrent typing. Channel delivery is preferred.' },
    { key: 'managed_pty_eager_spawn', label: 'Eager-spawn managed PTY', type: 'toggle' },
    { key: 'console_auto_confirm_claude_dev_channels', label: 'Auto-confirm claude dev-channels prompt', type: 'toggle' },
    { key: 'console_auto_confirm_claude_compaction', label: 'Auto-confirm claude compaction prompt', type: 'toggle' },
    { key: 'managed_via_wrapper', label: 'Wrapper-backed managed runtimes', type: 'csv', hint: 'Comma-separated, e.g. codex, hermes.' },
    { key: 'managed_claude_model', label: 'Managed claude model', type: 'text' },
    { key: 'managed_claude_effort', label: 'Managed claude effort', type: 'select', options: EFFORT_OPTS },
    { key: 'managed_codex_model', label: 'Managed codex model', type: 'text' },
    { key: 'managed_codex_effort', label: 'Managed codex effort', type: 'select', options: EFFORT_OPTS },
    { key: 'managed_pi_model', label: 'Managed pi model', type: 'text' },
    { key: 'managed_pi_effort', label: 'Managed pi effort', type: 'select', options: PI_EFFORT_OPTS, optionLabels: { '': 'OMP default' } },
  ] },
  { group: 'Retention & rotation', items: [
    { key: 'rotation_enabled', label: 'Rotation enabled', type: 'toggle' },
    { key: 'retention_days', label: 'Retention (days)', type: 'number', min: 1, max: 3650 },
    { key: 'max_messages_per_agent', label: 'Max messages / agent', type: 'number', min: 10, max: 100000 },
    { key: 'max_shared_size_mb', label: 'Max shared file size (MB)', type: 'number', min: 10, max: 100000 },
    { key: 'active_run_stale_minutes', label: 'Terminal run stale cleanup (min)', type: 'number', min: 5, max: 240 },
    { key: 'active_managed_run_stale_minutes', label: 'Managed run stale cleanup (min)', type: 'number', min: 1, max: 120 },
  ] },
  { group: 'Dashboard', items: [
    { key: 'dashboard_refresh_seconds', label: 'Poll fallback (s)', type: 'number', min: 5, max: 300, hint: 'A safety net only — live updates arrive over WebSocket.' },
  ] },
];

function themePreviewTilesHtml(selectedKey) {
  const selected = THEMES[selectedKey] ? selectedKey : 'default';
  return `<div class="theme-preview-grid" id="theme-preview-grid">${Object.entries(THEMES).map(([key, t]) => `
    <button type="button" class="theme-preview${key === selected ? ' active' : ''}" data-theme-choice="${esc(key)}" title="Use ${esc(t.label)} color scheme">
      <b>${esc(t.label)}</b>
      <span class="theme-preview-swatches"><span style="background:${esc(t.accent)}"></span><span style="background:${esc(t.secondary)}"></span><span style="background:${esc(t.tertiary)}"></span></span>
    </button>`).join('')}</div>`;
}

// Short tab labels for the settings tab bar (the full group names are long).
const SETTINGS_TAB_LABELS = {
  'Appearance': 'Appearance', 'Status & lifecycle': 'Status', 'Reply contracts': 'Contracts',
  'Managed runtimes': 'Runtimes', 'Retention & rotation': 'Retention', 'Dashboard': 'Dashboard',
};
const SETTINGS_TAB_DESC = {
  'Appearance': 'Theme, accent colors, and the dashboard title.',
  'Status & lifecycle': 'How liveness is derived and when agents are marked idle/offline.',
  'Reply contracts': 'Reply-reminder cadence and how long contracts stay tracked.',
  'Managed runtimes': 'Defaults applied to dashboard-spawned managed agents.',
  'Retention & rotation': 'Message/file retention and stale-record cleanup windows.',
  'Dashboard': 'Dashboard-only preferences.',
};
const HELP_TAB = 'Help';

// One aligned field row: label (+hint) on the left, control on the right. Toggles render a real
// switch. The theme picker spans the full width (select + preview tiles). Same input ids +
// data-setting-* attrs as before so saveSettings/previewAppearance/theme tiles keep working.
function settingsFieldHtml(item, value, settings = {}) {
  const id = `set-${item.key}`;
  const hint = item.hint ? `<span class="field-hint">${esc(item.hint)}</span>` : '';
  // Associate the label with its input (for/id) so screen readers announce the field name.
  const labelBlock = `<label class="field-label" for="${id}">${esc(item.label)}${hint}</label>`;
  const bounds = `${item.min != null ? ` min="${item.min}"` : ''}${item.max != null ? ` max="${item.max}"` : ''}`;

  if (item.type === 'toggle') {
    return `<div class="settings-field"><label class="field-label" for="${id}">${esc(item.label)}${hint}</label>`
      + `<div class="field-control"><label class="switch"><input type="checkbox" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="toggle"${value === true ? ' checked' : ''}><span class="switch-slider"></span></label></div></div>`;
  }
  if (item.type === 'theme') {
    const key = THEMES[value] ? value : 'default';
    const opts = Object.entries(THEMES).map(([k, t]) => `<option value="${esc(k)}"${k === key ? ' selected' : ''}>${esc(t.label)}</option>`).join('');
    return `<div class="settings-field settings-field-wide">${labelBlock}`
      + `<div class="field-control"><select id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="theme">${opts}</select></div>`
      + `<div class="settings-field-extra">${themePreviewTilesHtml(key)}</div></div>`;
  }
  if (item.type === 'color') {
    const preset = paletteFromSettings(settings, settings.dashboard_theme);
    const fallback = item.key === 'dashboard_secondary_color' ? preset.secondary : item.key === 'dashboard_tertiary_color' ? preset.tertiary : preset.accent;
    const hex = /^#[0-9a-fA-F]{6}$/.test(String(value || '')) ? value : fallback;
    return `<div class="settings-field">${labelBlock}<div class="field-control field-control-color"><input type="color" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="color" value="${esc(hex)}"><code class="field-color-hex">${esc(hex)}</code></div></div>`;
  }
  let control;
  if (item.type === 'select') {
    const opts = (item.options || []).map((o) => {
      const label = (item.optionLabels && item.optionLabels[o] != null) ? item.optionLabels[o] : (o === '' ? '(default)' : o);
      return `<option value="${esc(o)}"${String(value ?? '') === String(o) ? ' selected' : ''}>${esc(label)}</option>`;
    }).join('');
    control = `<select id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="select">${opts}</select>`;
  } else if (item.type === 'number') {
    control = `<input type="number" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="number" value="${esc(value ?? '')}"${bounds}>`;
  } else if (item.type === 'csv') {
    const text = Array.isArray(value) ? value.join(', ') : (value ?? '');
    control = `<input type="text" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="csv" value="${esc(text)}">`;
  } else {
    control = `<input type="text" id="${id}" data-setting-key="${esc(item.key)}" data-setting-type="text" value="${esc(value ?? '')}">`;
  }
  return `<div class="settings-field">${labelBlock}<div class="field-control">${control}</div></div>`;
}

function activeSettingsTab() {
  const tabs = [...SETTINGS_SCHEMA.map((g) => g.group), HELP_TAB];
  return tabs.includes(state.settingsTab) ? state.settingsTab : SETTINGS_SCHEMA[0].group;
}

// Tabbed settings: one panel visible at a time (short page), but ALL schema panels stay in the
// DOM so Save collects every field regardless of the active tab. Help is its own tab and toggles
// the static help-band.
function renderSettings() {
  const host = byId('settings-form');
  if (!host) return;
  // Don't rebuild while the operator is editing a FIELD — the 15s poll re-renders settings and
  // would otherwise wipe an in-progress edit (deep-audit C1). Scope strictly to editable inputs:
  // the tab buttons live inside this same host, so guarding on any focused descendant also blocked
  // tab switches (a real click focuses the tab → early return → panel never switched). 2026-06-29 fix.
  const _ae = document.activeElement;
  if (_ae && host.contains(_ae) && _ae.matches && _ae.matches('input, select, textarea')) return;
  const s = state.settings || {};
  const active = activeSettingsTab();
  const tabBar = `<div class="settings-tabs" role="group" aria-label="Settings sections">`
    + SETTINGS_SCHEMA.map((g) => `<button type="button" class="settings-tab${g.group === active ? ' active' : ''}" data-settings-tab="${esc(g.group)}">${esc(SETTINGS_TAB_LABELS[g.group] || g.group)}</button>`).join('')
    + `<button type="button" class="settings-tab${active === HELP_TAB ? ' active' : ''}" data-settings-tab="${HELP_TAB}">${HELP_TAB}</button>`
    + `</div>`;
  const panels = SETTINGS_SCHEMA.map((grp) => `
    <section class="settings-panel${grp.group === active ? ' active' : ''}${grp.appearance ? ' settings-appearance' : ''}" data-settings-panel="${esc(grp.group)}">
      ${SETTINGS_TAB_DESC[grp.group] ? `<p class="settings-panel-desc">${esc(SETTINGS_TAB_DESC[grp.group])}</p>` : ''}
      ${grp.items.map((item) => settingsFieldHtml(item, s[item.key], s)).join('')}
    </section>`).join('');
  host.innerHTML = tabBar + panels;
  // Help tab shows the static help-band; schema tabs hide it. Save/Classic buttons hide on Help.
  const helpBand = byId('help-band');
  if (helpBand) helpBand.hidden = active !== HELP_TAB;
  const saveBtn = byId('settings-save');
  if (saveBtn) saveBtn.style.display = active === HELP_TAB ? 'none' : '';
}

// Read the (possibly unsaved) Appearance editor controls into a partial settings object.
function readAppearanceInputs() {
  const val = (k) => byId(`set-${k}`)?.value;
  return {
    dashboard_theme: val('dashboard_theme'),
    dashboard_primary_color: val('dashboard_primary_color'),
    dashboard_secondary_color: val('dashboard_secondary_color'),
    dashboard_tertiary_color: val('dashboard_tertiary_color'),
    dashboard_title: val('dashboard_title'),
  };
}

// Live-preview the Appearance editor without saving (theme tile, select, or color picker).
function previewAppearance() {
  const a = readAppearanceInputs();
  previewTheme({ theme: a.dashboard_theme, primary: a.dashboard_primary_color, secondary: a.dashboard_secondary_color, tertiary: a.dashboard_tertiary_color });
  const title = String(a.dashboard_title || 'AIFY Comms').trim() || 'AIFY Comms';
  document.title = title;
  const brand = document.querySelector('.brand-copy strong');
  if (brand) brand.textContent = title;
  // Keep the hex labels next to the color pickers in sync.
  document.querySelectorAll('.field-control-color').forEach((wrap) => {
    const input = wrap.querySelector('input[type="color"]');
    const code = wrap.querySelector('.field-color-hex');
    if (input && code) code.textContent = input.value;
  });
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

function usageResetLabel(iso) {
  try {
    const ms = new Date(iso) - new Date();
    if (!(ms > 0)) return 'resets soon';
    const h = Math.floor(ms / 3600000), m = Math.floor((ms % 3600000) / 60000);
    return h > 0 ? `resets in ${h}h ${m}m` : `resets in ${m}m`;
  } catch { return ''; }
}
function usageFmtTokens(n) {
  n = Number(n || 0);
  if (n >= 1e9) return (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}
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
    const reset = w.resets_at ? usageResetLabel(w.resets_at) : '';
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
      : `<div class="usage-pool-meta">5h ${fleft} left${reset ? ' · ' + esc(reset) : ''}</div>`;
    return `<div class="usage-pool-card ${sev}"><div class="usage-pool-name"><span>${esc(name)}</span><span>${tags}</span></div>`
      + `<div class="usage-pool-weekly">${left}<span class="usage-pool-sub"> weekly left</span></div>`
      + `<div class="usage-pool-bar"><span style="width:${used}%"></span></div>`
      + meta + `</div>`;
  }).join('');
}
function renderUsageConsumption() {
  const host = byId('usage-consumption');
  if (!host) return;
  const s = state.analytics.consumption;
  const byAgent = (s && s.by_agent) || {};
  const agents = Object.keys(byAgent);
  if (!agents.length) { host.innerHTML = '<p class="em">No per-agent token data yet (collector warming up).</p>'; return; }
  agents.sort((a, b) => (byAgent[b].output_tokens || 0) - (byAgent[a].output_tokens || 0));
  const rows = agents.map((a) => {
    const c = byAgent[a];
    return `<tr><td>${esc(a)}</td><td>${usageFmtTokens(c.input_tokens)}</td><td>${usageFmtTokens(c.output_tokens)}</td><td>${usageFmtTokens(c.cache_tokens)}</td></tr>`;
  }).join('');
  const t = (s && s.totals) || {};
  host.innerHTML = '<div class="table-wrap"><table class="usage-consumption-table"><thead><tr><th>Agent</th><th>In</th><th>Out</th><th>Cache</th></tr></thead>'
    + `<tbody>${rows}</tbody>`
    + `<tfoot><tr><td>Total</td><td>${usageFmtTokens(t.input_tokens)}</td><td>${usageFmtTokens(t.output_tokens)}</td><td>${usageFmtTokens(t.cache_tokens)}</td></tr></tfoot></table></div>`;
}

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

function metric(label, value, tone = 'neutral', attrs = '') {
  // attrs is caller-provided raw HTML attributes (e.g. a data-* jump target), never user input.
  return `<div class="metric${attrs ? ' metric-clickable' : ''}" data-tone="${esc(tone)}"${attrs}><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
}

function renderMetrics() {
  const working = state.agents.filter((a) => resolveStatus(a.status).kind === 'working').length;
  const blocked = state.agents.filter((a) => resolveStatus(a.status).kind === 'blocked').length;
  const active = state.agents.filter((a) => ['active', 'online', 'working', 'blocked'].includes(resolveStatus(a.status).kind)).length;
  const overdue = state.contracts.filter((c) => c.overdue).length;
  const queued = state.contracts.filter((c) => c.state === 'queued').length;
  byId('metrics').innerHTML = [
    metric('Active agents', active, 'ok'),
    metric('Working now', working, working ? 'working' : 'neutral'),
    metric('Blocked agents', blocked, blocked ? 'bad' : 'neutral'),
    metric('Overdue work', overdue, overdue ? 'warn' : 'neutral'),
    metric('Queued contracts', queued, queued ? 'queued' : 'neutral'),
  ].join('');
}

function contractCard(contract, { selectable = true } = {}) {
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

function contractActionable(contract) {
  const target = String(contract?.targetAgentId || '').trim();
  const current = String(contract?.state || '').toLowerCase();
  return Boolean(contract?.id && target && target !== 'dashboard' && !['answered', 'closed'].includes(current));
}

function renderAttention() {
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

function diagnosticKey(kind, id) {
  return `${kind}:${id}`;
}

function selectedDiagnostics() {
  const selected = [];
  const contractById = new Map(state.contracts.map((contract) => [String(contract.id), contract]));
  const runById = new Map(state.runs.map((run) => [String(run.id), run]));
  for (const key of state.selectedDiagnosticIds) {
    const [kind, ...rest] = String(key).split(':');
    const id = rest.join(':');
    if (kind === 'contract' && contractById.has(id)) selected.push({ kind, id, item: contractById.get(id) });
    if (kind === 'run' && runById.has(id)) selected.push({ kind, id, item: runById.get(id) });
  }
  return selected;
}

function pruneDiagnosticSelection() {
  const live = new Set([
    ...state.contracts.map((contract) => diagnosticKey('contract', contract.id)),
    ...state.runs.map((run) => diagnosticKey('run', run.id)),
  ]);
  for (const key of [...state.selectedDiagnosticIds]) {
    if (!live.has(key)) state.selectedDiagnosticIds.delete(key);
  }
}

function renderDiagnosticsSummary() {
  const target = byId('diagnostics-summary');
  if (!target) return;
  // Summary tiles describe the FLEET, not the current Work-Loop/Runs filter. Use the unfiltered
  // open-contracts snapshot (contractsBase) + fleet-wide /stats so changing a filter never moves
  // the headline numbers.
  const baseContracts = state.contractsBase || state.contracts;
  const runsByStatus = state.stats?.dispatch_runs_by_status || {};
  const openWork = baseContracts.filter((contract) => ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state)).length;
  const overdue = baseContracts.filter((contract) => contract.overdue).length;
  const activeRuns = (Number(runsByStatus.claimed) || 0) + (Number(runsByStatus.running) || 0);
  const failedRuns = Number(state.stats?.run_failures_24h) || 0;
  // Tiles are triage shortcuts: clicking jumps to the matching Work-Loop/Runs filter.
  const jump = (t) => ` data-diag-jump="${t}" role="button" tabindex="0" title="Filter to ${esc(t.replace('run:', ''))}"`;
  target.innerHTML = [
    metric('Open work', openWork, openWork ? 'warn' : 'neutral', jump('open')),
    metric('Overdue', overdue, overdue ? 'bad' : 'neutral', jump('overdue')),
    metric('Active runs', activeRuns, activeRuns ? 'working' : 'neutral', jump('run:running')),
    metric('Failed recent', failedRuns, failedRuns ? 'bad' : 'neutral', jump('run:failed')),
  ].join('');
}

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

function activityItems() {
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

function renderActivityFeed() {
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

let _statusWhyReturnFocus = null;
function openStatusWhy(trigger) {
  const popover = byId('status-why-popover');
  if (!popover || !trigger) return;
  _statusWhyReturnFocus = trigger;
  const reason = trigger.dataset.statusWhy || trigger.title || 'No status reason loaded.';
  const kind = trigger.dataset.statusKind || 'unknown';
  popover.hidden = false;
  popover.setAttribute('role', 'dialog');
  popover.innerHTML = `
    <div class="item-title">
      <strong>Status: ${esc(kind)}</strong>
      <button class="ghost" data-close-status-why>Close</button>
    </div>
    <p>${esc(reason)}</p>`;
  setTimeout(() => popover.querySelector('[data-close-status-why]')?.focus(), 20);
  const rect = trigger.getBoundingClientRect();
  const top = Math.min(window.innerHeight - 160, Math.max(12, rect.bottom + 8));
  const left = Math.min(window.innerWidth - 320, Math.max(12, rect.left));
  popover.style.top = `${top}px`;
  popover.style.left = `${left}px`;
}

function closeStatusWhy() {
  const popover = byId('status-why-popover');
  if (!popover) return;
  popover.hidden = true;
  popover.innerHTML = '';
  try { if (_statusWhyReturnFocus && _statusWhyReturnFocus.focus) _statusWhyReturnFocus.focus(); } catch {}
  _statusWhyReturnFocus = null;
}

function sessionId(session) {
  return String(session?.id || session?.sessionId || session?.session_id || '');
}

function sessionAgentId(session) {
  return String(session?.agentId || session?.agent_id || session?.agent || '');
}

function sessionEnvironmentId(session) {
  return String(session?.environmentId || session?.environment_id || session?.envId || session?.env_id || 'unassigned');
}

function sessionRuntime(session) {
  return String(session?.runtime || session?.runtimeKind || session?.kind || 'runtime');
}

function agentForSession(session) {
  const agentId = sessionAgentId(session);
  return state.agents.find((agent) => String(agent.id) === agentId) || {};
}

function groupedSessionsByEnvironment() {
  const groups = new Map();
  const filter = state.sessionStatusFilter;
  const find = state.filter.trim().toLowerCase();
  state.sessions.forEach((session) => {
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

function selectedSessionIds() {
  return [...state.selectedSessionIds].filter((id) => state.sessions.some((session) => sessionId(session) === id));
}

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

function messagesForSession(session) {
  const agentId = sessionAgentId(session);
  if (!agentId) return [];
  return state.messages
    .filter((message) => message.from === agentId || message.to === agentId || message.targetAgentId === agentId || message.target_agent_id === agentId)
    .slice(0, 50);
}

// Single source of truth lives in messageIdOf(); kept as an alias so existing call sites work.
function messageId(message) {
  return messageIdOf(message);
}

function messageRunId(message) {
  return String(message?.dispatchRunId || message?.dispatch_run_id || message?.runId || message?.run_id || message?.contractRunId || message?.contract_run_id || '');
}

function runTargetAgent(run) {
  return String(run?.targetAgentId || run?.target_agent || run?.agentId || run?.agent_id || '');
}

function sessionForAgent(agentId) {
  return state.sessions.find((session) => sessionAgentId(session) === agentId) || null;
}

function sessionForRun(run) {
  return sessionForAgent(runTargetAgent(run));
}

function runSourceMessage(run) {
  const id = String(run?.messageId || run?.message_id || state.inspector.sourceMessageId || '').trim();
  if (!id) return null;
  return state.messages.find((message) => messageId(message) === id) || null;
}

function renderSessionBulkToolbar() {
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

// WS-F: status multiselect filter chips for the Sessions rail.
// Proof-based 6-state model only — `idle`/`stale` were removed in the status rewrite, so they must
// not appear as session filter chips (dead chips that match nothing).
const SESSION_FILTER_KINDS = ['working', 'online', 'available', 'blocked', 'offline', 'stopped'];
const SESSION_LIVE_KINDS = ['working', 'online', 'available', 'blocked'];
function renderSessionStatusFilter() {
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
  host.innerHTML = presets + chips + hiddenNote;
}

function persistSessionStatusFilter() {
  try { localStorage.setItem('aifySessionStatusFilter', JSON.stringify([...state.sessionStatusFilter])); } catch { /* ignore */ }
}

function renderSessionRail() {
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

// Persisted collapse state for session env-groups (WS-J collapsibles).
function sessionGroupCollapsed(envId) {
  try { return (JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []).includes(envId); } catch { return false; }
}
function toggleSessionGroupCollapsed(envId, collapsed) {
  try {
    const set = new Set(JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []);
    if (collapsed) set.add(envId); else set.delete(envId);
    localStorage.setItem('aifyCollapsedSessionGroups', JSON.stringify([...set]));
  } catch { /* ignore */ }
}

// Read-only Activity log for a session (WS-J): recent runs + messages, NO composer (messaging
// lives in Chat). Merges the agent's dispatch runs and message thread, newest first.
function renderSessionActivity(session) {
  const agentId = sessionAgentId(session);
  const host = byId('session-activity');
  if (!host) return;
  const ts = (v) => { const n = Date.parse(String(v || '')); return Number.isFinite(n) ? n : 0; };
  const runItems = state.runs
    .filter((r) => runTargetAgent(r) === agentId || runFrom(r) === agentId)
    .map((r) => ({ kind: 'run', ts: ts(r.updatedAt || r.createdAt || r.created_at), r }));
  const msgItems = messagesForSession(session)
    .map((m) => ({ kind: 'msg', ts: ts(m.timestamp || m.createdAt), m }));
  const items = [...runItems, ...msgItems].sort((a, b) => b.ts - a.ts).slice(0, 60);
  host.innerHTML = items.length ? items.map((it) => {
    if (it.kind === 'run') {
      const r = it.r;
      return `<article class="activity-row" data-kind="run" data-id="${esc(r.id)}">
        <div class="item-title"><span class="button-row">${renderStatusChip(r.status, statusWhyContext('run', r, r.status))}<strong class="clip">${esc(r.subject || r.id)}</strong></span>
          <button class="ghost" data-run-inspector="${esc(r.id)}" data-run-source="activity">Inspect</button></div>
        ${r.summary || r.error ? `<p class="preview">${esc(r.summary || r.error)}</p>` : ''}
      </article>`;
    }
    const m = it.m; const id = messageId(m);
    return `<article class="activity-row" data-kind="message" data-id="${esc(id)}">
      <div class="item-title"><strong>${esc(m.from || 'unknown')}</strong>${renderStatusChip(m.read ? 'completed' : 'queued', { label: esc(m.type || (m.read ? 'read' : 'unread')), why: `Message ${m.read ? 'read' : 'unread'}.` })}</div>
      <p class="preview">${esc(m.subject ? m.subject + ' — ' : '')}${esc(m.body || m.preview || '')}</p>
    </article>`;
  }).join('') : '<div class="empty-state"><span class="empty-icon">📋</span><strong>No activity yet</strong><p>Runs and messages for this session appear here. Use Chat to message the agent.</p></div>';
}

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

  const term = new window.Terminal({
    convertEol: true,
    cursorBlink: true,
    fontFamily: '"Cascadia Code", ui-monospace, "Consolas", monospace',
    fontSize: 13,
    theme: { background: '#0b0e13', foreground: '#cdd6f4', cursor: '#51c5b0' },
    scrollback: 5000,
  });
  let fitAddon = null;
  if (window.FitAddon && window.FitAddon.FitAddon) {
    fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(fitAddon);
  }
  term.open(container);
  // WebGL renderer (WS-D) — big perf win under heavy TUI output; fall back to the DOM
  // renderer if the GL context is lost or the addon throws.
  if (window.WebglAddon && window.WebglAddon.WebglAddon) {
    try {
      const webgl = new window.WebglAddon.WebglAddon();
      webgl.onContextLoss(() => { try { webgl.dispose(); } catch {} });
      term.loadAddon(webgl);
    } catch { /* DOM renderer remains active */ }
  }
  if (fitAddon) {
    try { fitAddon.fit(); } catch {}
  }

  // Keystroke forwarding back to the bridge PTY via /terminals/<id>/input.
  // Service request shape (TerminalControlRequest in api_v2.py): {body, requestedBy}.
  term.onData(async (data) => {
    // Blocked-input guard (WS-D): don't silently POST into a console that can't accept input —
    // warn the operator (debounced) so their keystrokes aren't lost into the void.
    if (state.activeXterm && state.activeXterm.canInput === false) {
      const now = Date.now();
      if (now - consoleInputBlockedToastAt > 4000) {
        consoleInputBlockedToastAt = now;
        toast('This console is not accepting input right now (session not live).', 'warn');
      }
      return;
    }
    try {
      await api(`/terminals/${encodeURIComponent(terminalId)}/input`, {
        method: 'POST',
        body: JSON.stringify({ body: data, requestedBy: 'dashboard' }),
      });
    } catch (err) {
      term.write(`\r\n\x1b[31m[input post failed: ${String(err?.message || err).replace(/\x1b/g, '')}]\x1b[0m\r\n`);
    }
  });
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
  const onWheel = (ev) => {
    try {
      if (term.buffer?.active?.type !== 'alternate') return; // normal buffer scrolls natively
      const lines = Math.min(5, Math.max(1, Math.round(Math.abs(ev.deltaY) / 40)));
      const seq = (ev.deltaY > 0 ? '\x1b[B' : '\x1b[A').repeat(lines);
      if (state.activeXterm?.canInput === false) return;
      api(`/terminals/${encodeURIComponent(terminalId)}/input`, {
        method: 'POST', body: JSON.stringify({ body: seq, requestedBy: 'dashboard' }),
      }).catch(() => {});
      ev.preventDefault();
    } catch { /* leave native behavior */ }
  };
  try { container.addEventListener('wheel', onWheel, { passive: false }); } catch {}

  // Re-fit on container/window resize so the terminal tracks the pane size.
  let resizeObserver = null;
  if (window.ResizeObserver && fitAddon) {
    let resyncTimer = null;
    resizeObserver = new ResizeObserver(() => {
      const entry = state.activeXterm;
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
      try { fitAddon.fit(); } catch {}
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
    try { resizeObserver.observe(container); } catch {}
  }

  state.activeXterm = { terminalId, agentId, term, fitAddon, container, resizeObserver, wheelHandler: onWheel, lastSeq: -1, canInput };

  // Replay existing buffered output so the operator sees history when they open the Console
  // pane mid-session (instead of waiting for the next byte to arrive).
  // Fit FIRST (next frame, after layout settles + with min-width:0 ancestors so fit() measures
  // the VISIBLE pane, not an overflowing one), THEN fetch the snapshot at the settled cols/rows.
  // Fetching before the fit settled rendered the snapshot too wide ("tries to compensate").
  // Double rAF: one frame to apply layout, a second so the flex-fill width is final before fit()
  // measures cols (a single frame can still read a transient narrow width on a fresh page switch).
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  if (fitAddon) { try { fitAddon.fit(); } catch {} }
  try {
    // Pass our (settled) grid size so the server renders a CLEAN current-screen snapshot (via the
    // headless VT emulator) instead of the raw byte log — replaying the raw log scrambles
    // full-screen TUIs. Prefer `snapshot`; fall back to raw `output` (e.g. pyte absent).
    const cols = Math.max(20, term.cols || 80), rows = Math.max(5, term.rows || 24);
    if (state.activeXterm) { state.activeXterm.renderedCols = term.cols; state.activeXterm.fitCols = term.cols; }
    const data = await api(`/terminals/${encodeURIComponent(terminalId)}?cols=${cols}&rows=${rows}`);
    // Widen the xterm to the server's rendered width (resident mirrors are wider than the pane)
    // BEFORE writing, so the snapshot lands in a grid that matches its render and never re-wraps.
    applyRenderedWidth(state.activeXterm, term, container, data);
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

// Apply the server's rendered width to the xterm. A resident wrapper mirrors the operator's
// real (often wider) terminal; the server renders the snapshot at that source width and reports
// it as `renderedCols`. If that exceeds the pane-fitted cols, widen the xterm to it and let the
// pane scroll horizontally (class `console-wide-mirror`) — otherwise the wide lines re-wrap and
// mangle ("gappy / bugged console"). When renderedCols fits (managed terminals), this is a no-op.
function applyRenderedWidth(entry, term, container, data) {
  // Compare against the pane's FITTED width (entry.fitCols), not the current term.cols —
  // term may already be widened from a prior snapshot, and we must be able to shrink back.
  const base = (entry && entry.fitCols) || term.cols;
  const rc = Number(data?.terminal?.renderedCols) || 0;
  const rr = Number(data?.terminal?.renderedRows) || term.rows;
  if (rc && rc > base) {
    try { term.resize(rc, Math.max(term.rows, rr)); } catch {}
    if (container) container.classList.add('console-wide-mirror');
    if (entry) { entry.widened = true; entry.renderedCols = rc; }
  } else {
    if (container) container.classList.remove('console-wide-mirror');
    try { if (term.cols !== base) term.resize(base, term.rows); } catch {}
    if (entry) { entry.widened = false; entry.renderedCols = base; }
  }
}

// Re-fetch the authoritative buffer and repaint (used by the Refresh button and on a
// detected seq gap, mirroring the old dashboard's resync path).
async function resyncActiveConsole() {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  try {
    // Fetch at the pane's FITTED width (not the possibly-widened current width) so the server
    // can re-infer the source width and hand back the correct renderedCols.
    const fetchCols = Math.max(20, entry.fitCols || entry.term.cols);
    const data = await api(`/terminals/${encodeURIComponent(entry.terminalId)}?cols=${fetchCols}&rows=${entry.term.rows}`);
    // reset() (not clear()) wipes any scrambled scrollback/alt-screen state before we
    // repaint the clean server-rendered snapshot — so Refresh actually un-scrambles.
    entry.term.reset();
    applyRenderedWidth(entry, entry.term, entry.container, data);
    const snapshot = data?.terminal?.snapshot;
    entry.term.write(String(snapshot || data?.terminal?.output || ''));
    entry.lastSeq = Number(data?.terminal?.outputSeq ?? data?.terminal?.seq ?? entry.lastSeq);
  } catch { /* keep current buffer */ }
}

// Clipboard copy that works on the http loopback origin (navigator.clipboard is undefined
// there) — falls back to a hidden textarea + execCommand, ported from the old dashboard.
async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

function copyActiveConsole() {
  const entry = state.activeXterm;
  if (!entry || !entry.term) return;
  let text = '';
  let autoSelected = false;
  try {
    if (entry.term.hasSelection()) { text = entry.term.getSelection(); }
    else { entry.term.selectAll(); autoSelected = true; text = entry.term.getSelection(); }
  } catch {}
  // Don't leave the whole buffer visually selected when we auto-selected to copy-all.
  if (autoSelected) { try { entry.term.clearSelection(); } catch {} }
  copyText(text).then((ok) => toast(ok ? 'Console copied' : 'Copy failed', ok ? 'ok' : 'error'));
}

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

const codexConsoleConnections = new Map(); // agentId → { ws, threadId, container }

// Don't leak codex console sockets across an unload/navigation.
window.addEventListener('beforeunload', () => { codexConsoleConnections.forEach((e) => { try { e.ws?.close(); } catch {} }); });
function codexConsoleClose(agentId) {
  const entry = codexConsoleConnections.get(agentId);
  if (!entry) return;
  try { entry.ws?.close(); } catch {}
  codexConsoleConnections.delete(agentId);
}

function codexConsoleAppendLine(container, line, cls = '') {
  if (!container) return;
  const div = document.createElement('div');
  div.className = `codex-line ${cls}`.trim();
  div.textContent = line;
  container.appendChild(div);
  // Cap scrollback (the xterm path caps at 5000; this DOM stream had no bound → grew forever).
  while (container.childElementCount > 2000) container.removeChild(container.firstChild);
  container.scrollTop = container.scrollHeight;
}

function codexConsoleAppendText(container, text) {
  if (!container) return;
  const lastLine = container.querySelector('.codex-line.delta:last-child');
  if (lastLine) {
    lastLine.textContent += text;
  } else {
    const div = document.createElement('div');
    div.className = 'codex-line delta';
    div.textContent = text;
    container.appendChild(div);
  }
  container.scrollTop = container.scrollHeight;
}

function codexConsoleConnect(agentId, appServerUrl, threadId) {
  const wsUrl = String(appServerUrl || '').trim();
  if (!/^wss?:\/\//i.test(wsUrl)) return;
  codexConsoleClose(agentId);

  const sel = String(agentId).replace(/[\\"]/g, '\\$&'); // safe inside a quoted attribute selector
  const container = document.querySelector(`[data-codex-console="${sel}"] .codex-console-stream`);
  if (!container) return;
  container.innerHTML = '';
  codexConsoleAppendLine(container, `[connecting to ${wsUrl}…]`, 'sys');

  let ws;
  try { ws = new WebSocket(wsUrl); } catch (err) {
    codexConsoleAppendLine(container, `[connect error: ${err?.message || err}]`, 'err');
    return;
  }
  let nextId = 1;
  let activeTurn = null;
  const entry = { ws, threadId, container };
  codexConsoleConnections.set(agentId, entry);

  ws.addEventListener('open', () => {
    codexConsoleAppendLine(container, '[connected]', 'sys');
    ws.send(JSON.stringify({
      jsonrpc: '2.0',
      id: nextId++,
      method: 'initialize',
      params: { clientInfo: { name: 'aify-dashboard', title: 'aify dashboard console', version: '1.0' } },
    }));
    ws.send(JSON.stringify({ jsonrpc: '2.0', method: 'initialized', params: {} }));
    if (threadId) {
      ws.send(JSON.stringify({
        jsonrpc: '2.0',
        id: nextId++,
        method: 'thread/resume',
        params: { threadId, personality: 'friendly' },
      }));
      codexConsoleAppendLine(container, `[subscribed to thread ${threadId}]`, 'sys');
    } else {
      codexConsoleAppendLine(container, '[no threadId — will only see broadcast events]', 'sys');
    }
  });
  ws.addEventListener('message', (ev) => {
    let msg;
    try { msg = JSON.parse(String(ev.data)); } catch { return; }
    const method = String(msg.method || '');
    const params = msg.params || {};
    if (method === 'turn/started' && params.turn?.id) {
      activeTurn = params.turn.id;
      codexConsoleAppendLine(container, `▶ turn started (${params.turn.id})`, 'turn');
    } else if (method === 'turn/completed') {
      const usage = params.turn?.usage || params.usage || {};
      const usageStr = usage.input_tokens || usage.output_tokens
        ? ` (in=${usage.input_tokens || 0} out=${usage.output_tokens || 0})`
        : '';
      codexConsoleAppendLine(container, `■ turn ended [${params.turn?.status || 'completed'}]${usageStr}`, 'turn');
      activeTurn = null;
    } else if (method === 'item/agentMessage/delta') {
      codexConsoleAppendText(container, String(params.delta || ''));
    } else if (method === 'item/started' && params.item?.id) {
      codexConsoleAppendLine(container, `→ ${params.item?.type || 'item'}`, 'tool');
    } else if (method === 'item/completed' && params.item?.id) {
      codexConsoleAppendLine(container, `✓ ${params.item?.type || 'item'}`, 'tool ok');
    } else if (method === 'error' && params.error?.message) {
      codexConsoleAppendLine(container, `✗ ${params.error.message}`, 'err');
    }
  });
  ws.addEventListener('close', (ev) => {
    codexConsoleAppendLine(container, `[disconnected: code=${ev.code}]`, 'sys');
    codexConsoleConnections.delete(agentId);
  });
  ws.addEventListener('error', () => {
    codexConsoleAppendLine(container, '[websocket error]', 'err');
  });
}

function codexConsoleSendTurn(agentId, text) {
  const entry = codexConsoleConnections.get(agentId);
  if (!entry || !entry.ws || entry.ws.readyState !== 1 || !entry.threadId) return;
  const trimmed = String(text || '').trim();
  if (!trimmed) return;
  const id = Math.floor(Math.random() * 1e9);
  entry.ws.send(JSON.stringify({
    jsonrpc: '2.0',
    id,
    method: 'turn/start',
    params: {
      threadId: entry.threadId,
      input: [{ type: 'text', text: trimmed }],
    },
  }));
  codexConsoleAppendLine(entry.container, `> ${trimmed}`, 'user');
}

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
  toast(`Switched ${agentId} to ${body?.mode || targetMode}`, 'ok');
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
  const connectActions = `${hermesGatewayHttp ? `<button class="ghost" data-action="open-hermes-tab" data-url="${esc(hermesGatewayHttp)}">Open in new tab</button>` : ''}`
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

function contractCategory(c) {
  return String(c.category || c.kind || (c.channel ? 'channel' : c.selfWake || c.self_wake ? 'self_wake' : 'direct')).toLowerCase();
}
// Work Loop board columns, ordered needs-attention → done. Each contract lands in
// the FIRST column whose match() is true (so `overdue` — a flag layered on any live
// state — always wins its urgency slot). `always` columns render even when empty so
// the board shape is stable in the default Open filter; terminal columns only appear
// when they actually hold cards (or when the State filter loaded them).
const CONTRACT_BOARD_COLUMNS = [
  { key: 'overdue',  label: 'Overdue',  always: true,  match: (c) => !!c.overdue },
  { key: 'working',  label: 'Working',  always: true,  match: (c) => c.state === 'working' },
  { key: 'queued',   label: 'Queued',   always: true,  match: (c) => c.state === 'queued' },
  { key: 'awaiting', label: 'Awaiting', always: true,  match: (c) => ['sent', 'seen', 'missing_reply'].includes(c.state) },
  { key: 'answered', label: 'Answered', always: false, match: (c) => ['answered', 'closed'].includes(c.state) },
  { key: 'failed',   label: 'Failed',   always: false, match: (c) => c.state === 'failed' },
];

function renderContractBoard(contracts) {
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

function environmentRuntimes(env) {
  const runtimes = env?.runtimes || env?.runtimeCapabilities || [];
  return Array.isArray(runtimes) ? runtimes
    .map((runtime) => typeof runtime === 'string' ? { runtime, available: true } : runtime)
    .filter((runtime) => runtime && runtime.runtime) : [];
}

function environmentRoots(env) {
  const roots = env?.cwdRoots || env?.cwd_roots || env?.roots || env?.workspaceRoots || [];
  return Array.isArray(roots) ? roots.filter(Boolean) : [];
}

function renderEnvironmentSummary() {
  const target = byId('environment-summary');
  if (!target) return;
  const online = state.environments.filter((env) => resolveStatus(env.status).kind === 'online').length;
  const offline = state.environments.filter((env) => resolveStatus(env.status).kind === 'offline').length;
  const runtimeKinds = new Set();
  state.environments.forEach((env) => environmentRuntimes(env).forEach((runtime) => runtimeKinds.add(runtime.runtime)));
  target.innerHTML = [
    metric('Environments', state.environments.length, state.environments.length ? 'ok' : 'neutral'),
    metric('Online bridges', online, online ? 'ok' : 'neutral'),
    metric('Offline', offline, offline ? 'bad' : 'neutral'),
    metric('Runtime types', runtimeKinds.size, runtimeKinds.size ? 'working' : 'neutral'),
  ].join('');
}

function renderEnvironmentSpawnOptions(selectedEnvId = byId('env-spawn-environment')?.value || '') {
  const envSelect = byId('env-spawn-environment');
  const runtimeSelect = byId('env-spawn-runtime');
  if (!envSelect || !runtimeSelect) return;
  // Don't rebuild the spawn dropdowns while the operator is interacting with the form — the 15s
  // poll would otherwise reset an open/selected dropdown (deep-audit C minor).
  if (byId('environment-spawn-form')?.contains(document.activeElement)) return;
  const currentEnv = state.environments.some((env) => String(env.id) === selectedEnvId)
    ? selectedEnvId
    : String(state.environments.find((env) => resolveStatus(env.status).kind === 'online')?.id || state.environments[0]?.id || '');
  envSelect.innerHTML = '<option value="">Environment</option>' + state.environments.map((env) => `<option value="${esc(env.id)}"${String(env.id) === currentEnv ? ' selected' : ''}>${esc(env.label || env.id)} (${esc(resolveStatus(env.status).label)})</option>`).join('');
  const env = state.environments.find((item) => String(item.id) === currentEnv) || {};
  const runtimeOptions = environmentRuntimes(env);
  const available = runtimeOptions.filter((runtime) => runtime.available !== false);
  // Preserve the operator's runtime pick across poll re-renders (review finding #9):
  // the focus guard above only protects while focus is INSIDE the form — pick a runtime,
  // click elsewhere, and the next poll silently reset it to available[0] right before Spawn.
  const priorRuntime = runtimeSelect.value || '';
  runtimeSelect.innerHTML = '<option value="">Runtime</option>' + runtimeOptions.map((runtime) => {
    const disabled = runtime.available === false ? ' disabled' : '';
    const suffix = runtime.available === false ? ' (unavailable)' : '';
    return `<option value="${esc(runtime.runtime)}"${disabled}>${esc(runtime.runtime)}${suffix}</option>`;
  }).join('');
  runtimeSelect.value = available.some((r) => r.runtime === priorRuntime) ? priorRuntime : (available[0]?.runtime || '');
  const workspace = byId('env-spawn-workspace');
  if (workspace && !workspace.value) workspace.value = environmentRoots(env)[0] || '';
}

function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong>${renderStatusChip(env.status, statusWhyContext('environment', env, env.status))}</div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}</p>
      <div class="env-runtime-list">
        ${environmentRuntimes(env).map((runtime) => `<span class="env-runtime-pill${runtime.available === false ? ' unavailable' : ''}">${esc(runtime.runtime)}${runtime.available === false ? ' (unavailable)' : ''}</span>`).join('') || '<span class="env-runtime-pill unavailable">no runtimes</span>'}
      </div>
      <div class="env-root-list">
        ${(() => { const roots = environmentRoots(env); const shown = roots.slice(0, 4).map((root) => `<code>${esc(root)}</code>`).join(''); const more = roots.length > 4 ? `<span class="subtle">+${roots.length - 4} more</span>` : ''; return (shown + more) || '<span class="subtle">No workspace roots advertised</span>'; })()}
      </div>
      <div class="contract-actions">
        ${resolveStatus(env.status).kind === 'offline' ? '' : `<button class="ghost" data-env-spawn="${esc(env.id)}" title="Open the spawn form prefilled for this environment">Spawn here…</button>`}
        <button class="ghost" data-env-roots="${esc(env.id)}" title="Edit the workspace roots agents may be spawned into">Edit roots…</button>
        ${resolveStatus(env.status).kind === 'offline'
          ? `<button class="ghost danger" data-env-control="forget" data-env-id="${esc(env.id)}" title="Hide this offline environment (identities/chats/records remain)">Forget</button>`
          : `<button class="ghost danger" data-env-control="stop" data-env-id="${esc(env.id)}" title="Ask this host bridge process to exit">Stop bridge</button>`}
      </div>
    </article>`).join('') || '<div class="empty-state"><span class="empty-icon">🔌</span><strong>No environments connected</strong><p>Start an aify-comms bridge on a host to see it here.</p></div>';
}

// Spawn-requests queue/history (ported from 8800 renderSpawnRequests): surfaces queued/
// claimed/failed/done spawn requests on the Environments page so failed or stuck spawns have
// somewhere to appear. Reads GET /spawn-requests (loaded into state.spawnRequests on refresh).
// `done` is the one spawn status the canonical resolver doesn't know — alias it to completed.
function renderSpawnRequests() {
  const el = byId('spawn-requests-list');
  if (!el) return;
  const requests = [...state.spawnRequests].sort((a, b) =>
    String(b.createdAt || b.created_at || '').localeCompare(String(a.createdAt || a.created_at || '')));
  if (!requests.length) {
    el.innerHTML = '<div class="empty-state"><span class="empty-icon">🌱</span><strong>No spawn requests</strong><p>Queued, failed, and completed spawns will appear here.</p></div>';
    return;
  }
  const rows = requests.map((req) => {
    const status = String(req.status || 'queued').toLowerCase();
    const chipStatus = status === 'done' ? 'completed' : status;
    const detail = req.error || req.claimedByBridgeId || '';
    const created = req.createdAt || req.created_at || '';
    return `<tr>
      <td>${created ? esc(relTime(created)) + ' ago' : '—'}</td>
      <td><strong>${esc(req.agentId || req.agent_id || '—')}</strong>${req.role ? `<br><span class="subtle">${esc(req.role)}</span>` : ''}</td>
      <td class="clip">${esc(req.environmentId || req.environment_id || '—')}</td>
      <td>${esc(req.runtime || '—')}</td>
      <td>${renderStatusChip(chipStatus, { label: status, why: `Spawn request status: ${status}.` })}</td>
      <td class="clip">${esc(req.workspace || '—')}</td>
      <td class="clip">${esc(detail)}</td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="table-wrap"><table class="spawn-requests-table"><thead><tr>
      <th>Requested</th><th>Agent</th><th>Environment</th><th>Runtime</th><th>Status</th><th>Workspace</th><th>Bridge / error</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

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
function environmentStartCommand(env) {
  const roots = environmentRoots(env).filter(Boolean);
  const firstRoot = roots[0] || '';
  const extras = roots.slice(1);
  const os = String(env.os || env.kind || '').toLowerCase();
  const quote = (v) => /[\s"'`]/.test(v) ? JSON.stringify(v) : v;
  if (os.includes('win')) {
    const cd = firstRoot ? `cd /d ${quote(firstRoot)}` : 'cd /d C:\\Docker';
    const args = extras.map(quote).join(' ');
    return `${cd}\naify-comms.cmd${args ? ' ' + args : ''}`;
  }
  const cd = firstRoot ? `cd ${quote(firstRoot)}` : (os.includes('mac') || os.includes('darwin') ? 'cd "$HOME"' : 'cd /mnt/c/Docker');
  const args = extras.map(quote).join(' ');
  return `${cd}\naify-comms${args ? ' ' + args : ''}`;
}

function openEnvironmentRootsEditor(environmentId) {
  const env = state.environments.find((e) => String(e.id) === String(environmentId)) || { id: environmentId };
  const roots = environmentRoots(env);
  const manualRoots = !!(env.metadata && (env.metadata.manualRoots || env.metadata.manual_roots));
  const overrideBadge = manualRoots
    ? '<span class="mb mb-warn" title="Roots were set from the dashboard and override what the bridge advertises">dashboard override active</span>'
    : '<span class="subtle">using bridge-advertised roots</span>';
  const startCmd = environmentStartCommand(env);
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Workspace roots — ${esc(env.label || environmentId)}</strong></div>
      <div class="env-roots-state">${overrideBadge}</div>
      <label class="settings-label">Roots (one per line)
        <textarea id="env-edit-roots" rows="6" spellcheck="false" placeholder="C:/work&#10;C:/projects">${esc(roots.join('\n'))}</textarea>
      </label>
      <p class="subtle">Agents spawned in this environment must use a cwd under one of these roots. Leave non-empty; use “Reset to bridge roots” to restore the advertised set.</p>
      <label class="settings-label">Start command <span class="subtle">(run on the host to bring this bridge back)</span>
        <textarea id="env-start-cmd" rows="2" spellcheck="false" readonly>${esc(startCmd)}</textarea>
      </label>
      <div class="agent-drawer-actions">
        <button class="primary" data-env-roots-submit="${esc(environmentId)}">Save roots</button>
        <button class="ghost" data-env-roots-reset="${esc(environmentId)}">Reset to bridge roots</button>
        <button class="ghost" data-copy-text="${esc(startCmd)}">Copy start command</button>
      </div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'env-roots', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  setTimeout(() => byId('env-edit-roots')?.focus(), 30);
}

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

const runFrom = (r) => String(r.from || r.fromAgent || r.from_agent || '');
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

function runStatusContext(run) {
  const blockerReason = String(run?.blockedByActiveRun || run?.blockedBy || run?.error || '').trim();
  return {
    label: run?.status || 'unknown',
    blockerReason,
    badges: blockerReason && resolveStatus(run?.status).kind === 'blocked' ? ['blocked'] : [],
  };
}

function runInspectorCapabilities(run, session = sessionForRun(run)) {
  const statusKind = resolveStatus(run?.status).kind;
  const active = ['claimed', 'running'].includes(statusKind);
  const terminal = ['completed', 'failed', 'cancelled'].includes(statusKind);
  const target = runTargetAgent(run);
  return {
    steer: Boolean(active),
    interrupt: Boolean(active),
    queueAfter: Boolean(target),
    retry: Boolean(target),
    close: Boolean(run?.id && !terminal),
    openConsole: Boolean(session),
  };
}

function runPendingControlCount(run) {
  return (run?.controls || []).filter((control) => ['pending', 'claimed'].includes(String(control.status || '').toLowerCase())).length;
}

function renderEventBody(event) {
  const body = String(event?.body || '');
  if (!body) return '<p class="preview">No event body.</p>';
  if (body.length > 160) {
    return `<details><summary>Body</summary><p class="preview">${esc(body)}</p></details>`;
  }
  return `<p class="preview">${esc(body)}</p>`;
}

function renderRunEvent(event) {
  const iso = event.createdAt || event.created_at || '';
  return `
    <article class="run-event">
      <div class="item-title">
        <time title="${esc(iso)}">${esc(relTime(iso) || 'now')}</time>
        <span class="event-chip">${esc(event.eventType || event.type || 'event')}</span>
      </div>
      ${renderEventBody(event)}
    </article>`;
}

function renderRunInspectorControls(run) {
  const session = sessionForRun(run);
  const capabilities = runInspectorCapabilities(run, session);
  const disabled = (enabled) => enabled ? '' : ' disabled';
  return `
    <div id="run-inspector-controls" class="run-inspector-controls">
      <button class="ghost" data-run-control="steer"${disabled(capabilities.steer)} title="Steer">Steer</button>
      <button class="ghost danger" data-run-control="interrupt"${disabled(capabilities.interrupt)} title="Interrupt">Interrupt</button>
      <button class="ghost" data-run-control="queue-after"${disabled(capabilities.queueAfter)} title="Queue-after">Queue-after</button>
      <button class="ghost danger" data-run-control="retry"${disabled(capabilities.retry)} title="Retry">Retry</button>
      <button class="ghost danger" data-run-control="close"${disabled(capabilities.close)} title="Close">Close</button>
      <button class="primary" data-run-control="open-console"${disabled(capabilities.openConsole)} title="Open Console">Open Console</button>
    </div>`;
}

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
function openIdentityDirectory() {
  const agents = [...state.agents].sort((a, b) => String(a.id || '').localeCompare(String(b.id || '')));
  const modeOf = (agent, session) => String(agent.sessionMode || (session && (session.mode || session.session_mode)) || 'resident').toLowerCase();
  const managed = agents.filter((a) => modeOf(a, sessionForAgent(a.id)) === 'managed').length;
  const resident = agents.length - managed;
  const unread = agents.reduce((sum, a) => sum + Number(a.unread || a.unreadCount || 0), 0);
  const rows = agents.map((agent) => {
    const id = String(agent.id || '');
    const session = sessionForAgent(id);
    const mode = modeOf(agent, session);
    const env = session ? (state.environments.find((e) => String(e.id) === String(sessionEnvironmentId(session))) || null) : null;
    const envLabel = (env && (env.label || env.id)) || (session ? sessionEnvironmentId(session) : '') || '';
    const runtime = agent.runtime || (session && sessionRuntime(session)) || '';
    const lastSeen = agent.lastSeen || agent.last_seen || '';
    return `<tr>
      <td><strong>${esc(id)}</strong></td>
      <td>${esc(agent.role || '')}</td>
      <td>${esc(runtime || '—')}</td>
      <td>${esc(mode)}</td>
      <td class="clip">${esc(envLabel === 'unassigned' ? '—' : (envLabel || '—'))}</td>
      <td>${renderStatusChip(agent.status || 'unknown', statusWhyContext('agent', agent, agent.status))}</td>
      <td>${Number(agent.unread || agent.unreadCount || 0) || 0}</td>
      <td>${lastSeen ? esc(relTime(lastSeen)) + ' ago' : '—'}</td>
      <td class="identity-row-actions">
        <button class="ghost" data-agent-details="${esc(id)}" title="Open the agent detail drawer (lifecycle controls)">Details</button>
        <button class="ghost danger" data-agent-remove="${esc(id)}" title="Unregister/forget this identity (tombstones it)">Remove</button>
      </td>
    </tr>`;
  }).join('');
  const table = agents.length
    ? `<div class="table-wrap"><table class="identity-table"><thead><tr>
        <th>ID</th><th>Role</th><th>Runtime</th><th>Mode</th><th>Environment</th><th>Status</th><th>Unread</th><th>Last seen</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table></div>`
    : '<div class="empty-state"><span class="empty-icon">🪪</span><strong>No identities</strong><p>No agents are registered yet.</p></div>';
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer identity-directory">
      <div class="agent-drawer-head"><strong>Identity directory</strong></div>
      <p class="subtle">Identities are the stable mailbox, role, and routing behind chat. Use this directory to audit roles, runtime, session mode, bound environment, and live status — or to forget an offline CLI identity. Runtime control lives on Sessions.</p>
      <dl class="chat-kv agent-drawer-kv identity-directory-stats">
        <dt>Managed</dt><dd>${managed}</dd>
        <dt>Resident / manual</dt><dd>${resident}</dd>
        <dt>Total unread</dt><dd>${unread}</dd>
      </dl>
      ${table}
    </div>`;
  state.inspector = { ...state.inspector, kind: 'identity-directory', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

// Agent-detail drawer (Phase 1.3): ONE drawer (the shared inspector) surfacing an agent's
// session/runtime/status + the key lifecycle actions, reusing the existing control functions
// — no duplicated action surface (the 8800 triplication the plan kills). Reachable from chat.
// Continue-in-CLI: the command to resume this agent's pinned native session in the
// operator's own terminal (mirror of the 8800 dashboard resume-command). Empty when
// there's no saved handle or the runtime has no resident resume (pi/opencode are
// managed-only). Linux/WSL shell form.
function continueCliCommand(agent) {
  const handle = String(agent?.sessionHandle || agent?.session_handle || '').trim();
  if (!handle) return '';
  const runtime = String(agent?.runtime || '').trim().toLowerCase();
  const id = String(agent?.id || '').trim();
  const agentFlag = id ? ` --aify-agent ${id}` : '';
  if (runtime === 'claude-code') return `claude-aify${agentFlag} --dangerously-skip-permissions --resume ${handle}`;
  if (runtime === 'hermes') return `hermes-aify${agentFlag} --resume ${handle}`;
  if (runtime === 'codex') return `AIFY_RUNTIME=codex AIFY_AGENT_ID=${id} AIFY_SESSION_HANDLE=${handle} CODEX_THREAD_ID=${handle} CODEX_HOME="$HOME/.local/state/aify-comms/managed-codex-home" codex --no-alt-screen resume --include-non-interactive ${handle}`;
  return '';
}

function openAgentDrawer(agentId) {
  const id = String(agentId || '').trim();
  if (!id) return;
  const agent = state.agents.find((a) => a.id === id) || { id };
  const session = sessionForAgent(id);
  const env = session ? (state.environments.find((e) => String(e.id) === String(sessionEnvironmentId(session))) || null) : null;
  const sid = session ? sessionId(session) : '';
  const mode = String(agent.sessionMode || (session && session.mode) || 'resident').toLowerCase();
  const otherMode = mode === 'managed' ? 'resident' : 'managed';
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value}</dd>`;
  const actions = [
    sid ? `<button class="ghost" data-agent-control="restart" data-session="${esc(sid)}">Restart</button>` : '',
    sid ? `<button class="ghost" data-agent-control="recreate" data-session="${esc(sid)}" title="Restart with a FRESH context (discards native session)">Reset</button>` : '',
    sid ? `<button class="ghost danger" data-agent-control="stop" data-session="${esc(sid)}">Stop</button>` : '',
    sid ? `<button class="ghost" data-agent-compact="${esc(sid)}">Compact</button>` : '',
    sid ? `<button class="ghost" data-agent-continue="${esc(sid)}">Continue as…</button>` : '',
    `<button class="ghost" data-agent-mode="${esc(otherMode)}" data-agent="${esc(id)}">Switch to ${esc(otherMode)}</button>`,
    `<button class="ghost" data-agent-edit="${esc(id)}">Edit…</button>`,
    `<button class="ghost" data-agent-history="${esc(id)}">History</button>`,
    sid ? `<button class="ghost danger" data-agent-delete-session="${esc(sid)}">Delete session</button>` : '',
    `<button class="ghost danger" data-agent-remove="${esc(id)}">Remove agent</button>`,
    `<button class="ghost" data-agent-open-sessions="${esc(sid)}">Open in Sessions</button>`,
  ].filter(Boolean).join('');
  const cliCmd = continueCliCommand(agent);
  const continueCliBlock = cliCmd ? `
      <div class="agent-drawer-cli">
        <div class="agent-drawer-subhead">Continue in CLI</div>
        <p class="subtle">Resume this session in your own terminal — native ${esc(agent.runtime || 'runtime')} CLI.</p>
        <div class="cli-cmd-row"><code class="cli-cmd">${esc(cliCmd)}</code><button class="ghost" data-copy-cli="${esc(cliCmd)}" title="Copy the resume command">Copy</button></div>
      </div>` : '';
  const sessionChangedBanner = agent.sessionChanged ? `
      <div class="session-changed-banner" role="alert">
        <p>⚠ This agent reported a new session id <code>${esc(agent.pendingSessionId)}</code> that differs from its pinned handle <code>${esc(agent.sessionHandle || '—')}</code>. Delivery still targets the pinned handle until you resolve this.</p>
        <div class="button-row">
          <button class="primary" data-session-confirm="${esc(id)}">Confirm new id</button>
          <button class="ghost" data-session-keep="${esc(id)}">Keep pinned</button>
        </div>
      </div>` : '';
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer">
      <div class="agent-drawer-head"><strong>${esc(id)}</strong>${renderStatusChip(agent.status || 'unknown', statusWhyContext('agent', agent, agent.status))}</div>
      ${sessionChangedBanner}
      <dl class="chat-kv agent-drawer-kv">
        ${row('Runtime', esc(agent.runtime || (session && sessionRuntime(session)) || '—'))}
        ${row('Mode', esc(mode))}
        ${row('Environment', esc((env && (env.label || env.id)) || sessionEnvironmentId(session) || '—'))}
        ${row('Workspace', esc((session && (session.workspace || session.cwd)) || agent.cwd || '—'))}
        ${row('Session', sid ? `${esc(sid)} · ${esc(session.status || 'unknown')}` : '<span class="subtle">no active session</span>')}
        ${row('Machine', esc(agent.machineId || '—'))}
      </dl>
      ${continueCliBlock}
      <div class="agent-drawer-actions">${actions}</div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'agent', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

// I9 — compaction / continuation lineage, derived from spawn records (metadata.continuedFrom*).
async function openCompactionHistory(agentId) {
  byId('inspector-content').innerHTML = `<div class="agent-drawer"><div class="agent-drawer-head"><strong>History · ${esc(agentId)}</strong></div><p class="subtle">Loading…</p></div>`;
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
  state.inspector = { ...state.inspector, kind: 'history', runId: '' };
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
function openAgentEditForm(agentId) {
  const agent = state.agents.find((a) => a.id === agentId) || { id: agentId };
  const currentEnv = String((agent.runtimeState && agent.runtimeState.environmentId) || (agent.runtimeConfig && agent.runtimeConfig.environmentId) || '');
  const currentRuntime = String(agent.runtime || 'generic');
  const onlineEnvs = state.environments.filter((env) => resolveStatus(env.status).kind === 'online');
  const envOptions = ['<option value="">— keep current —</option>']
    .concat(onlineEnvs.map((env) => {
      const id = String(env.id || env.environmentId || '');
      return `<option value="${esc(id)}"${id === currentEnv ? ' selected' : ''}>${esc(env.label || id)}</option>`;
    })).join('');
  const runtimeOptions = ['generic', 'claude-code', 'codex', 'pi', 'opencode']
    .map((rt) => `<option value="${esc(rt)}"${rt === currentRuntime ? ' selected' : ''}>${esc(rt)}</option>`).join('');
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>Edit ${esc(agentId)}</strong></div>
      <label class="settings-label">Agent ID (rename)<input id="edit-agent-id" type="text" value="${esc(agentId)}"></label>
      <label class="settings-label">Description<input id="edit-agent-desc" type="text" value="${esc(agent.description || '')}" placeholder="Short role/description"></label>
      <label class="settings-label">Native session handle<input id="edit-agent-handle" type="text" value="${esc(agent.sessionHandle || agent.session_handle || '')}" placeholder="Claude/Codex/Pi session id — blank clears"></label>
      <fieldset class="agent-edit-env">
        <legend>Re-assign environment</legend>
        <label class="settings-label">Environment<select id="edit-agent-env">${envOptions}</select></label>
        <label class="settings-label">Runtime<select id="edit-agent-runtime">${runtimeOptions}</select></label>
        <label class="settings-label">Workspace (optional)<input id="edit-agent-workspace" type="text" value="${esc(agent.cwd || '')}" placeholder="Leave blank for the environment default root"></label>
        <p class="subtle">Only takes effect when an environment is chosen above. The environment must be online and advertise the selected runtime.</p>
      </fieldset>
      <div class="agent-drawer-actions"><button class="primary" data-agent-edit-submit="${esc(agentId)}">Save changes</button></div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'agent-edit', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

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
function openMessageDetail(msgId) {
  const m = state.messages.find((x) => messageId(x) === String(msgId));
  if (!m) { toast('Message not found in the loaded set', 'warn'); return; }
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value}</dd>`;
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer">
      <div class="agent-drawer-head"><strong>Message ${esc(String(messageId(m)).slice(0, 12))}</strong></div>
      <dl class="chat-kv agent-drawer-kv">
        ${row('From', esc(m.from || 'unknown'))}
        ${row('To', esc(m.to || m.target || '—'))}
        ${row('Type', esc(m.type || 'info'))}
        ${row('Priority', esc(m.priority || 'normal'))}
        ${row('Read', m.read === false ? 'unread' : 'read')}
        ${row('When', esc(relTime(m.timestamp || m.createdAt)) + ' ago')}
        ${messageRunId(m) ? row('Run', esc(messageRunId(m))) : ''}
      </dl>
      ${m.subject ? `<h4 class="an-h">${esc(m.subject)}</h4>` : ''}
      <p class="chat-msg-body">${esc(m.body || m.preview || '')}</p>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'message', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

// F1 — Compact / Continue-as (handoff packet UX). Build a packet from recent messages and
// render an editable continuation form into the inspector; submit creates a managed-warm
// spawn-request seeded with the packet (POST /spawn-requests), same mechanism as 8800.
function buildHandoffPacket(agentId, count = 25) {
  const related = state.messages
    .filter((m) => m.from === agentId || m.to === agentId || m.target === agentId)
    .slice(-count)
    .map((m) => `[${m.from || '?'}→${m.to || m.target || '?'}] ${m.subject ? m.subject + ': ' : ''}${m.body || m.preview || ''}`.trim());
  return `Handoff packet for ${agentId} (last ${related.length} messages):\n\n${related.join('\n')}`;
}

function openContinueForm(sid, splitIdentity) {
  const target = state.sessions.find((s) => String(sessionId(s)) === String(sid));
  if (!target) { toast('Session not found', 'warn'); return; }
  const agentId = sessionAgentId(target) || '';
  const packet = buildHandoffPacket(agentId);
  byId('inspector-content').innerHTML = `
    <div class="agent-drawer continue-form">
      <div class="agent-drawer-head"><strong>${splitIdentity ? 'Continue as new agent' : 'Compact session'}</strong></div>
      <p class="subtle">${splitIdentity ? 'Splits into a new agent identity with an editable handoff packet.' : 'Compacts into a fresh managed backing, keeping the same agent ID.'}</p>
      <label class="settings-label">Agent ID<input id="cont-agent-id" type="text" value="${esc(splitIdentity ? '' : agentId)}" placeholder="${esc(agentId)}"></label>
      <label class="settings-label">Role<input id="cont-role" type="text" value="${esc(target.role || 'coder')}"></label>
      <label class="settings-label">Environment<input id="cont-env" type="text" value="${esc(sessionEnvironmentId(target) || '')}"></label>
      <label class="settings-label">Runtime<input id="cont-runtime" type="text" value="${esc(sessionRuntime(target) || '')}"></label>
      <label class="settings-label">Workspace<input id="cont-workspace" type="text" value="${esc(target.workspace || target.cwd || '')}"></label>
      <label class="settings-label">Handoff packet<textarea id="cont-packet" rows="8">${esc(packet)}</textarea></label>
      <div class="agent-drawer-actions">
        <button class="primary" data-continue-submit="${esc(sid)}" data-split="${splitIdentity ? '1' : '0'}">${splitIdentity ? 'Continue as' : 'Compact'}</button>
      </div>
    </div>`;
  state.inspector = { ...state.inspector, kind: 'continue', runId: '' };
  byId('inspector')?.classList.add('open');
  byId('inspector')?.classList.remove('run-inspector-sheet');
}

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

function openClassic(anchor = '') {
  const suffix = anchor ? `#${encodeURIComponent(anchor)}` : '';
  window.open(`${apiOrigin}/api/v1/dashboard${suffix}`, '_blank', 'noopener');
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
    else if (action === 'refresh') resyncActiveConsole().then(() => toast('Console refreshed', 'ok')).catch(() => {});
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
  chatController.send();
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
byId('open-classic-settings')?.addEventListener('click', () => openClassic('settings'));
byId('settings-save')?.addEventListener('click', () => {
  saveSettings().catch((err) => toast(`Save failed: ${err?.message || err}`, 'error'));
});
byId('settings-reset')?.addEventListener('click', () => {
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur(); // clear the edit-guard
  applyTheme(state.settings); // undo any live appearance preview
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
