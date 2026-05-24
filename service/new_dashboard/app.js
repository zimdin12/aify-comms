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
  agents: [],
  contracts: [],
  messages: [],
  runs: [],
  sessions: [],
  environments: [],
  stats: {},
  terminalOwners: new Map(),
  realtimeConnected: false,
  selectedConversation: 'dashboard',
  selectedSessionId: '',
  selectedSessionTab: 'chat',
  selectedSessionIds: new Set(),
  selectedDiagnosticIds: new Set(),
  inspector: { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' },
  filter: '',
  runStatusFilter: '',
};

const STATUS_KINDS = {
  active: { label: 'active', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  online: { label: 'online', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  working: { label: 'working', dotKind: 'working', tone: 'warn', inputEnabled: false },
  blocked: { label: 'blocked', dotKind: 'blocked', tone: 'bad', inputEnabled: false },
  queued: { label: 'queued', dotKind: 'queued', tone: 'muted', inputEnabled: false },
  claimed: { label: 'claimed', dotKind: 'working', tone: 'warn', inputEnabled: false },
  running: { label: 'running', dotKind: 'working', tone: 'warn', inputEnabled: false },
  completed: { label: 'completed', dotKind: 'ok', tone: 'ok', inputEnabled: true },
  failed: { label: 'failed', dotKind: 'bad', tone: 'bad', inputEnabled: true },
  cancelled: { label: 'cancelled', dotKind: 'bad', tone: 'bad', inputEnabled: true },
  offline: { label: 'offline', dotKind: 'bad', tone: 'bad', inputEnabled: false },
  unknown: { label: 'unknown', dotKind: 'unknown', tone: 'muted', inputEnabled: false },
};

const flowAssertions = {
  foundations: () => Boolean(STATUS_KINDS.unknown && state.terminalOwners && typeof connectRealtimeSocket === 'function'),
  sessions: () => Boolean(byId('session-rail') && byId('session-chat-thread') && typeof renderSessionWorkspace === 'function'),
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
  sessions: ['Sessions', 'Environment-backed sessions with chat and console in one workspace.'],
  environments: ['Environments', 'Connected bridges, runtimes, roots, and capacity.'],
  diagnostics: ['Diagnostics', 'Runs and Work Loop evidence stay secondary to the session workspace.'],
  settings: ['Settings', 'Configuration stays on the classic dashboard until this flow reaches parity.'],
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

const byId = (id) => document.getElementById(id);
let refreshTimer = null;
let dashboardSocket = null;

function resolveStatus(rawStatus, context = {}) {
  const raw = String(rawStatus || '').trim().toLowerCase();
  const base = STATUS_KINDS[raw] || STATUS_KINDS.unknown;
  const label = context.label || base.label;
  const badges = Array.isArray(context.badges) ? context.badges.filter(Boolean) : [];
  return { ...base, kind: STATUS_KINDS[raw] ? raw : 'unknown', label, badges };
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

function renderStatusChip(rawStatus, context = {}) {
  const status = resolveStatus(rawStatus, context);
  const badges = status.badges.length ? ` <small>${esc(status.badges.join(' · '))}</small>` : '';
  const why = context.why || `${status.label} status`;
  return `<span class="status-chip ${esc(status.tone)} status-why-trigger" role="button" tabindex="0" title="${esc(why)}" data-status-why="${esc(why)}" data-tone="${esc(status.tone)}" data-status-kind="${esc(status.kind)}"><span class="status-dot ${esc(status.dotKind)}"></span>${esc(status.label)}${badges}</span>`;
}

function renderStatusDot(rawStatus) {
  const status = resolveStatus(rawStatus);
  return `<span class="status-dot dot ${esc(status.dotKind)}" data-status-kind="${esc(status.kind)}"></span>`;
}

function evaluateFlowGates() {
  Object.values(flowGates).forEach((gate) => {
    gate.enabled = Boolean(gate.assertion());
  });
  return flowGates;
}

const relTime = (iso) => {
  if (!iso) return '';
  let ms = Number(iso);
  if (!Number.isFinite(ms) || String(iso).includes('-')) ms = Date.parse(iso);
  if (Number.isFinite(ms) && ms > 0 && ms < 1000000000000) ms *= 1000;
  if (!Number.isFinite(ms)) return '';
  const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.round(minutes / 6) / 10;
  if (hours < 48) return `${hours}h`;
  return `${Math.round(hours / 24)}d`;
};

async function api(path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.error || data.detail || response.statusText);
  return data;
}

function refreshSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refresh, 250);
}

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
    state.realtimeConnected = true;
    evaluateFlowGates();
  };
  dashboardSocket.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data || '{}');
      applyRealtimeEvent(payload.event, payload.data || {});
    } catch {}
  };
  dashboardSocket.onclose = () => {
    state.realtimeConnected = false;
    setTimeout(connectRealtimeSocket, 2500);
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
    refreshSoon();
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
    'agent_status',
  ].includes(event)) {
    refreshSoon();
  }
}

function runQueryPath(status = state.runStatusFilter) {
  const params = new URLSearchParams({ limit: '80' });
  if (status) params.set('status', status);
  return `/dispatch/runs?${params.toString()}`;
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
  byId('api-status').textContent = 'refreshing';
  byId('api-status').className = 'status-chip muted';
  try {
    const [agents, contracts, inboxMessages, recentMessages, runs, sessions, environments, stats] = await Promise.all([
      api('/agents'),
      api('/contracts?limit=80'),
      api('/messages/inbox/dashboard?filter=all&peek=true&limit=80'),
      api('/messages/recent?limit=80'),
      api(runQueryPath()),
      api('/sessions?limit=80'),
      api('/environments'),
      api('/stats'),
    ]);
    state.agents = asAgentArray(agents);
    state.contracts = contracts.contracts || [];
    state.messages = recentMessages.messages || inboxMessages.messages || [];
    state.runs = runs.runs || [];
    state.sessions = asArray(sessions, 'sessions');
    state.environments = asArray(environments, 'environments');
    state.stats = stats || {};
    state.sessions.forEach((session) => {
      const terminalId = session.terminalId || session.terminal?.id;
      const agentId = session.agentId || session.agent_id;
      if (terminalId && agentId) state.terminalOwners.set(String(terminalId), String(agentId));
    });
    evaluateFlowGates();
    renderAll();
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
  } catch (error) {
    byId('api-status').textContent = 'API error';
    byId('api-status').className = 'status-chip bad';
    inspect('API error', { message: error.message });
  }
}

function filtered(items, fields) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) return items;
  return items.filter((item) => fields.some((field) => String(item[field] || '').toLowerCase().includes(needle)));
}

function renderAll() {
  renderMetrics();
  renderAttention();
  renderSessionWorkspace();
  renderActivityFeed();
  renderDiagnosticsSummary();
  renderDiagnosticsBulkToolbar();
  renderContracts();
  renderEnvironmentSummary();
  renderEnvironmentSpawnOptions();
  renderRuntime();
  renderRuns();
}

function metric(label, value, tone = 'neutral') {
  return `<div class="metric" data-tone="${esc(tone)}"><b>${esc(value)}</b><span>${esc(label)}</span></div>`;
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
          ${esc(contract.from)} → ${esc(contract.targetAgentId)} · ${esc(contract.type)} · ${relTime(contract.requestedAt)} old · ${contract.lastReminderAt ? `last reminded ${relTime(contract.lastReminderAt)} ago` : 'not reminded'}
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
  byId('attention-list').innerHTML = items.length
    ? items.map((contract) => contractCard(contract, { selectable: false })).join('')
    : '<div class="item"><strong>No open attention items</strong><p class="preview">The current Work Loop is clear.</p></div>';
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
  const openWork = state.contracts.filter((contract) => ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state)).length;
  const overdue = state.contracts.filter((contract) => contract.overdue).length;
  const activeRuns = state.runs.filter((run) => ['claimed', 'running'].includes(resolveStatus(run.status).kind)).length;
  const failedRuns = state.runs.filter((run) => resolveStatus(run.status).kind === 'failed').length;
  target.innerHTML = [
    metric('Open work', openWork, openWork ? 'warn' : 'neutral'),
    metric('Overdue', overdue, overdue ? 'bad' : 'neutral'),
    metric('Active runs', activeRuns, activeRuns ? 'working' : 'neutral'),
    metric('Failed recent', failedRuns, failedRuns ? 'bad' : 'neutral'),
  ].join('');
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
    at: Date.parse(run.startedAt || run.requestedAt || '') || 0,
    source: run,
  }));
  const messageItems = state.messages.slice(0, 8).map((message) => ({
    kind: 'message',
    id: messageId(message),
    title: message.subject || message.body || '(no subject)',
    meta: `${message.from || 'unknown'} → ${message.to || message.targetAgentId || 'dashboard'} · ${relTime(message.createdAt || message.timestamp || message.time)} ago`,
    status: message.read ? 'completed' : 'queued',
    at: Date.parse(message.createdAt || message.timestamp || message.time || '') || 0,
    source: message,
  }));
  const contractItems = state.contracts.slice(0, 8).map((contract) => ({
    kind: 'contract',
    id: contract.id,
    title: contract.subject || contract.id,
    meta: `${contract.targetAgentId || 'unknown'} · ${relTime(contract.requestedAt)} old`,
    status: contract.overdue ? 'failed' : contract.state || contract.status || 'unknown',
    at: Date.parse(contract.lastReminderAt || contract.requestedAt || '') || 0,
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

function openStatusWhy(trigger) {
  const popover = byId('status-why-popover');
  if (!popover || !trigger) return;
  const reason = trigger.dataset.statusWhy || trigger.title || 'No status reason loaded.';
  const kind = trigger.dataset.statusKind || 'unknown';
  popover.hidden = false;
  popover.innerHTML = `
    <div class="item-title">
      <strong>Status: ${esc(kind)}</strong>
      <button class="ghost" data-close-status-why>Close</button>
    </div>
    <p>${esc(reason)}</p>`;
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
  state.sessions.forEach((session) => {
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

function messageId(message) {
  return String(message?.id || message?.messageId || message?.message_id || '');
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
       <button class="ghost" data-bulk-session-action="recover">Recover</button>
       <button class="ghost" data-bulk-session-action="restart">Restart</button>
       <button class="ghost danger" data-bulk-session-action="stop">Stop</button>`
    : '';
}

function renderSessionRail() {
  const groups = groupedSessionsByEnvironment();
  renderSessionBulkToolbar();
  byId('session-rail').innerHTML = groups.length ? groups.map((group) => `
    <section class="session-env-group">
      <div class="session-env-title">${esc(group.label)} <span>${group.sessions.length}</span></div>
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
                ${renderStatusChip(status, statusWhyContext('session', session, status))}
              </div>
              <p class="preview">${esc(session.workspace || session.cwd || '')}</p>
              <span class="session-runtime-badge" data-runtime="${esc(sessionRuntime(session))}">${esc(sessionRuntime(session))}</span>
            </div>
          </article>`;
      }).join('')}
    </section>`).join('') : '<div class="item">No sessions loaded.</div>';
}

function renderSessionChat(session) {
  const messages = messagesForSession(session);
  byId('session-chat-thread').innerHTML = messages.length ? messages.map((message) => {
    const runId = messageRunId(message);
    const id = messageId(message);
    return `
    <article class="message" data-kind="message" data-id="${esc(id)}" id="message-${esc(id)}">
      <div class="item-title">
        <strong>${esc(message.from || 'unknown')}</strong>
        <span class="button-row">
          ${runId ? `<button class="run-chip" data-run-chip="${esc(runId)}" data-run-source="chat" data-message-id="${esc(id)}">Run ${esc(runId.slice(0, 10))}</button>` : ''}
          ${renderStatusChip(message.read ? 'completed' : 'queued', { label: esc(message.type || (message.read ? 'read' : 'unread')), why: `Message is ${message.read ? 'read' : 'unread'}; type ${message.type || 'unknown'}.` })}
        </span>
      </div>
      <h3>${esc(message.subject || '(no subject)')}</h3>
      <p class="preview">${esc(message.body || message.preview || '')}</p>
    </article>`;
  }).join('') : '<div class="message">No loaded messages for this session yet.</div>';
}

// Convert a hermes tui_gateway WS URL into its sibling HTTP root URL.
// Input:  ws://127.0.0.1:1234/api/ws?token=abc
// Output: http://127.0.0.1:1234/?token=abc
// Returns "" if the input isn't a recognizable loopback ws:// URL.
function hermesGatewayUrlToHttp(wsUrl) {
  const raw = String(wsUrl || '').trim();
  if (!/^wss?:\/\//i.test(raw)) return '';
  try {
    const u = new URL(raw);
    // Only embed loopback hermes dashboards — public hosts would expose tokens
    // through the iframe URL and the dashboard would need explicit allowlisting.
    if (!['127.0.0.1', 'localhost', '::1'].includes(u.hostname)) return '';
    const scheme = u.protocol === 'wss:' ? 'https' : 'http';
    const token = u.searchParams.get('token') || '';
    const query = token ? `?token=${encodeURIComponent(token)}` : '';
    return `${scheme}://${u.hostname}:${u.port || (scheme === 'https' ? '443' : '80')}/${query}`;
  } catch (_) {
    return '';
  }
}

// --- Codex live-console widget --------------------------------------
// Connects directly to a codex app-server WS (browser → ws://127.0.0.1:<port>),
// subscribes to events on the agent's threadId via initialize + thread/resume,
// and renders agent message deltas + turn lifecycle markers into a div.
// Symmetric in intent with the hermes iframe embed, but built custom because
// codex has no upstream web UI to embed — we render the JSON-RPC event stream
// ourselves. Send a turn/start when the operator types in the input box.

const codexConsoleConnections = new Map(); // agentId → { ws, threadId, container }

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

  const container = document.querySelector(`[data-codex-console="${agentId}"] .codex-console-stream`);
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

function renderSessionConsole(session) {
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

  const headerCard = `
    <article class="runtime-card" data-kind="session" data-id="${esc(id)}">
      <div class="item-title"><strong>${esc(sessionAgentId(session) || id || 'No session selected')}</strong>${renderStatusChip(session?.status || 'unknown', statusWhyContext('session', session || {}, session?.status || 'unknown'))}</div>
      <p class="preview">${esc(session?.workspace || session?.cwd || '')}</p>
      <small>${esc(sessionRuntime(session))} · ${esc(sessionEnvironmentId(session))}${hermesGatewayHttp ? ' · live tui_gateway' : ''}${codexAttachable ? ' · live app-server' : ''}</small>
      <div class="contract-actions">
        <button class="ghost" data-session-control="restart" data-session-id="${esc(id)}">Restart</button>
        <button class="ghost" data-session-control="recover" data-session-id="${esc(id)}">Recover</button>
        ${canStop ? `<button class="ghost danger" data-session-control="stop" data-session-id="${esc(id)}">Stop</button>` : ''}
        ${hermesGatewayHttp ? `<button class="ghost" data-action="open-hermes-tab" data-url="${esc(hermesGatewayHttp)}">Open in new tab</button>` : ''}
        ${codexAttachable ? `<button class="ghost" data-action="codex-console-connect" data-agent-id="${esc(agentIdForCodex)}" data-app-server-url="${esc(codexAppServerUrl)}" data-thread-id="${esc(codexThreadId)}">Connect live console</button>` : ''}
      </div>
    </article>`;

  // For hermes resident agents with a live tui_gateway, embed the upstream
  // hermes web dashboard chat surface as an iframe. The dashboard runs at
  // http://127.0.0.1:<port>/ on the operator's machine; the operator's
  // browser is also on that machine, so loopback access works. This is
  // the real Ink Chat UI — interactive, typing-supported, full fidelity —
  // the same WS session the bridge attaches to via /api/ws. (See
  // ui-tui/src/gatewayClient.ts:resolveGatewayAttachUrl + the hermes
  // dashboard's embedded chat tab gated on HERMES_DASHBOARD_TUI=1.)
  const hermesIframe = hermesGatewayHttp
    ? `<div class="console-embed" data-kind="hermes-gateway">
         <div class="console-embed-label">Hermes live chat — embedded from <code>${esc(hermesGatewayHttp.split('?')[0])}</code></div>
         <iframe src="${esc(hermesGatewayHttp)}" title="Hermes live chat" allow="clipboard-read; clipboard-write"></iframe>
       </div>`
    : '';

  // Codex doesn't have an upstream web UI to iframe, so we render the
  // JSON-RPC event stream ourselves. Operator clicks "Connect live
  // console" → browser WS direct to codex app-server (loopback only,
  // same security argument as the hermes iframe) → subscribes to the
  // agent's threadId → renders deltas + lifecycle markers + accepts
  // turn/start frames from the local input box.
  const codexConsole = codexAttachable
    ? `<div class="console-embed" data-kind="codex-app-server" data-codex-console="${esc(agentIdForCodex)}">
         <div class="console-embed-label">
           Codex live thread — attaches direct WS to <code>${esc(codexAppServerUrl)}</code>${codexThreadId ? ` · thread <code>${esc(codexThreadId)}</code>` : ''}
         </div>
         <div class="codex-console-stream" aria-live="polite"></div>
         <form class="codex-console-input" data-action="codex-console-send" data-agent-id="${esc(agentIdForCodex)}">
           <input type="text" placeholder="${codexThreadId ? 'Type to send turn/start into this thread...' : 'No threadId — read-only.'}" ${codexThreadId ? '' : 'disabled'}>
           <button type="submit" class="primary" ${codexThreadId ? '' : 'disabled'}>Send</button>
           <button type="button" class="ghost" data-action="codex-console-disconnect" data-agent-id="${esc(agentIdForCodex)}">Disconnect</button>
         </form>
       </div>`
    : '';

  byId('session-console-summary').innerHTML = `${headerCard}${hermesIframe}${codexConsole}`;
}

function renderSessionWorkspace() {
  const session = ensureSelectedSession();
  renderSessionRail();
  document.querySelectorAll('[data-session-tab]').forEach((button) => {
    button.classList.toggle('active', button.dataset.sessionTab === state.selectedSessionTab);
  });
  byId('session-chat-panel').classList.toggle('active', state.selectedSessionTab === 'chat');
  byId('session-console-panel').classList.toggle('active', state.selectedSessionTab === 'console');
  if (!session) {
    byId('session-title').textContent = 'No sessions loaded';
    byId('session-subtitle').textContent = 'Spawn or connect an agent to start a session workspace.';
    byId('session-status').innerHTML = renderStatusChip('unknown', statusWhyContext('session', {}, 'unknown'));
    byId('session-chat-thread').innerHTML = '<div class="message">No session selected.</div>';
    byId('session-console-summary').innerHTML = '<div class="item">No session selected.</div>';
    byId('composer-body').placeholder = 'Select a session to send a message';
    return;
  }
  const agentId = sessionAgentId(session);
  byId('session-title').textContent = agentId || sessionId(session);
  byId('session-subtitle').textContent = session.workspace || session.cwd || 'Chat and console are bound to this session.';
  byId('session-status').innerHTML = renderStatusChip(session.status || agentForSession(session).status || 'unknown', statusWhyContext('session', session, session.status || agentForSession(session).status || 'unknown'));
  byId('composer-body').placeholder = agentId ? `Send to ${agentId}` : 'Select a session to send a message';
  renderSessionChat(session);
  renderSessionConsole(session);
}

function renderAgents() {
  const agents = filtered(state.agents, ['id', 'name', 'role', 'runtime', 'status']).slice(0, 18);
  byId('agent-list').innerHTML = agents.map((agent) => `
    <article class="agent" data-kind="agent" data-id="${esc(agent.id)}">
      ${renderStatusDot(agent.status)}
      <div>
        <strong>${esc(agent.id)}</strong>
        <p class="preview">${esc(agent.runtime || 'runtime')} · ${esc(agent.sessionMode || '')} · ${esc(agent.machineId || '')}</p>
      </div>
      ${renderStatusChip(agent.status, statusWhyContext('agent', agent, agent.status))}
    </article>`).join('');
}

function renderMessages() {
  const messages = filtered(state.messages, ['from', 'subject', 'preview', 'body']).slice(0, 10);
  byId('message-list').innerHTML = messages.map((message) => `
    <article class="item" data-kind="message" data-id="${esc(message.id)}">
      <div class="item-title">
        <strong class="clip">${esc(message.subject || '(no subject)')}</strong>
        ${renderStatusChip(message.read ? 'completed' : 'queued', { label: message.read ? 'read' : 'unread', why: `Message is ${message.read ? 'read' : 'unread'} from ${message.from || 'unknown'}.` })}
      </div>
      <p class="preview">${esc(message.preview || message.body || '')}</p>
      <small>${esc(message.from)} · ${relTime(message.createdAt || message.timestamp || message.time)} ago</small>
    </article>`).join('');
}

function renderConversations() {
  const agents = state.agents.filter((a) => a.id !== 'dashboard').slice(0, 50);
  byId('conversation-count').textContent = `${agents.length} agents`;
  byId('conversation-list').innerHTML = [
    `<button class="nav-item ${state.selectedConversation === 'dashboard' ? 'active' : ''}" data-conversation="dashboard">Dashboard Inbox</button>`,
    ...agents.map((agent) => `<button class="nav-item ${state.selectedConversation === agent.id ? 'active' : ''}" data-conversation="${esc(agent.id)}">${esc(agent.id)} <small>${esc(resolveStatus(agent.status).label)}</small></button>`),
  ].join('');
  const title = state.selectedConversation === 'dashboard' ? 'Dashboard Inbox' : state.selectedConversation;
  byId('conversation-title').textContent = title;
  const messages = state.selectedConversation === 'dashboard'
    ? state.messages
    : state.messages.filter((message) => message.from === state.selectedConversation);
  byId('conversation-messages').innerHTML = messages.slice(0, 30).map((message) => `
    <article class="message" data-kind="message" data-id="${esc(message.id)}">
      <div class="item-title"><strong>${esc(message.from)}</strong><span class="status-chip muted">${esc(message.type)}</span></div>
      <h3>${esc(message.subject || '(no subject)')}</h3>
      <p class="preview">${esc(message.body || message.preview || '')}</p>
    </article>`).join('') || '<div class="message">No loaded messages for this conversation.</div>';
}

function renderContracts() {
  const selected = byId('contract-state').value || 'open';
  const contracts = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])
    .filter((contract) => selected === 'open' ? ['overdue', 'working', 'queued', 'sent', 'seen'].includes(contract.state) : contract.state === selected);
  byId('contract-list').innerHTML = contracts.map(contractCard).join('') || '<div class="item">No contracts match this filter.</div>';
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
  const currentEnv = state.environments.some((env) => String(env.id) === selectedEnvId)
    ? selectedEnvId
    : String(state.environments.find((env) => resolveStatus(env.status).kind === 'online')?.id || state.environments[0]?.id || '');
  envSelect.innerHTML = '<option value="">Environment</option>' + state.environments.map((env) => `<option value="${esc(env.id)}"${String(env.id) === currentEnv ? ' selected' : ''}>${esc(env.label || env.id)} (${esc(resolveStatus(env.status).label)})</option>`).join('');
  const env = state.environments.find((item) => String(item.id) === currentEnv) || {};
  const runtimeOptions = environmentRuntimes(env);
  const available = runtimeOptions.filter((runtime) => runtime.available !== false);
  runtimeSelect.innerHTML = '<option value="">Runtime</option>' + runtimeOptions.map((runtime) => {
    const disabled = runtime.available === false ? ' disabled' : '';
    const suffix = runtime.available === false ? ' (unavailable)' : '';
    return `<option value="${esc(runtime.runtime)}"${disabled}>${esc(runtime.runtime)}${suffix}</option>`;
  }).join('');
  runtimeSelect.value = available[0]?.runtime || '';
  const workspace = byId('env-spawn-workspace');
  if (workspace && !workspace.value) workspace.value = environmentRoots(env)[0] || '';
}

function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong>${renderStatusChip(env.status, statusWhyContext('environment', env, env.status))}</div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}</p>
      <div class="env-runtime-list">
        ${environmentRuntimes(env).map((runtime) => `<span class="env-runtime-pill${runtime.available === false ? ' unavailable' : ''}">${esc(runtime.runtime)}${runtime.available === false ? ' off' : ''}</span>`).join('') || '<span class="env-runtime-pill unavailable">no runtimes</span>'}
      </div>
      <div class="env-root-list">
        ${environmentRoots(env).slice(0, 4).map((root) => `<code>${esc(root)}</code>`).join('') || '<span class="subtle">No workspace roots advertised</span>'}
      </div>
      <div class="contract-actions">
        <button class="ghost" data-env-spawn="${esc(env.id)}">Spawn here</button>
      </div>
    </article>`).join('') || '<div class="item">No environments loaded.</div>';
}

function renderRuns() {
  const runs = filtered(state.runs, ['id', 'subject', 'targetAgentId', 'from', 'summary']).slice(0, 80);
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
    </article>`).join('') || '<div class="item">No runs loaded.</div>';
  renderDiagnosticsBulkToolbar();
}

function renderAnalytics() {
  byId('analytics-grid').innerHTML = [
    metric('Messages today', state.stats.messages_today || 0),
    metric('Completed runs 24h', state.stats.completed_runs_24h || 0),
    metric('Run failures 24h', state.stats.run_failures_24h || 0),
    metric('Unread messages', state.stats.unread_messages || 0),
    metric('Online environments', state.environments.filter((e) => resolveStatus(e.status).kind === 'online').length),
  ].join('');
  const counts = state.stats.dispatch_runs_by_status || state.runs.reduce((acc, run) => {
    acc[run.status || 'unknown'] = (acc[run.status || 'unknown'] || 0) + 1;
    return acc;
  }, {});
  const max = Math.max(1, ...Object.values(counts));
  byId('run-status-mix').innerHTML = Object.entries(counts).map(([status, count]) => `
    <div class="bar-row">
      <span>${esc(status)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.round((count / max) * 100)}%"></div></div>
      <b>${count}</b>
    </div>`).join('');
}

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
        ${events.length ? events.map(renderRunEvent).join('') : '<div class="item">No events for this run yet.</div>'}
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

function openInspector(request) {
  if (request && request.kind === 'run' && request.runId && state.inspector.runId !== String(request.runId)) {
    openRunInspector(request);
    return;
  }
  const inspector = byId('inspector');
  inspector?.classList.add('open');
  inspector?.classList.toggle('run-inspector-sheet', state.inspector.kind === 'run' || request?.kind === 'run');
}

function closeInspector() {
  const inspector = byId('inspector');
  inspector?.classList.remove('open');
  inspector?.classList.remove('run-inspector-sheet');
  state.inspector = { kind: '', runId: '', source: '', run: null, events: [], hasMore: false, loadingMore: false, eventOrder: 'desc', sourceMessageId: '' };
  byId('inspector-content').textContent = 'Select an item to inspect details.';
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
  const body = prompt('Steer this active run');
  if (!body) return;
  await api(`/dispatch/runs/${encodeURIComponent(runId)}/control`, {
    method: 'POST',
    body: JSON.stringify({ from_agent: 'dashboard', action: 'steer', body }),
  });
  await openRunInspector({ runId, source: 'runs' });
}

async function requestSessionControl(sessionId, action, confirmAction = true, refreshAfter = true) {
  const labels = {
    stop: 'stop this session',
    restart: 'restart this session using its saved backing',
    recover: 'recover this session using its saved backing',
  };
  if (!sessionId || !action) return;
  if (confirmAction && !confirm(`Really ${labels[action] || action}?`)) return;
  await api(`/sessions/${encodeURIComponent(sessionId)}/control`, {
    method: 'POST',
    body: JSON.stringify({
      action,
      from_agent: 'dashboard',
      body: `Session ${action} requested from Dashboard Next.`,
    }),
  });
  if (refreshAfter) await refresh();
}

async function requestBulkSessionControl(action) {
  const ids = selectedSessionIds();
  if (!ids.length || !action) return;
  if (!confirm(`Really ${action} ${ids.length} selected session${ids.length === 1 ? '' : 's'}?`)) return;
  for (const id of ids) {
    await requestSessionControl(id, action, false, false);
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
  if (confirmAction && !confirm('Close this Work Loop contract as operator-reviewed?')) return;
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
    for (const item of contracts) {
      await remindWorkContract(item.id, false);
    }
    state.selectedDiagnosticIds.clear();
    await refresh();
    return;
  }
  if (action === 'close') {
    if (!confirm(`Close ${selected.length} selected diagnostics item${selected.length === 1 ? '' : 's'} as operator-reviewed?`)) return;
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
    inspect('spawn-error', { message: 'Need environment, runtime, agent ID, and workspace.' });
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

function openMessageThread(messageIdValue) {
  const message = state.messages.find((item) => messageId(item) === String(messageIdValue));
  if (!message) return;
  const agentId = message.from === 'dashboard' ? message.to : message.from;
  const session = sessionForAgent(agentId) || selectedSession();
  if (session) {
    state.selectedSessionId = sessionId(session);
    state.selectedConversation = sessionAgentId(session) || 'dashboard';
  }
  state.selectedSessionTab = 'chat';
  setPage('sessions');
  renderSessionWorkspace();
  setTimeout(() => {
    document.getElementById(`message-${messageId(message)}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, 50);
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
  if (action === 'steer') {
    const body = prompt('Steer this active run');
    if (!body) return;
    await api(`/dispatch/runs/${encodeURIComponent(run.id)}/control`, {
      method: 'POST',
      body: JSON.stringify({ from_agent: 'dashboard', action: 'steer', body }),
    });
  } else if (action === 'interrupt') {
    if (!confirm(`Interrupt this run? This will kill 1 active run + ${runPendingControlCount(run)} pending controls.`)) return;
    await api(`/dispatch/runs/${encodeURIComponent(run.id)}/control`, {
      method: 'POST',
      body: JSON.stringify({ from_agent: 'dashboard', action: 'interrupt', body: 'Interrupted from Dashboard Next run inspector.' }),
    });
  } else if (action === 'queue-after') {
    const body = prompt('Queue a follow-up after this run');
    if (!body) return;
    await sendRunFollowup(run, { body });
  } else if (action === 'retry') {
    if (!confirm(`Retry this run? This will kill 1 active run + ${runPendingControlCount(run)} pending controls.`)) return;
    await sendRunFollowup(run, { retry: true });
  } else if (action === 'close') {
    if (!confirm('Close this run as operator-reviewed?')) return;
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
  document.querySelectorAll('.nav-item[data-page]').forEach((el) => el.classList.toggle('active', el.dataset.page === page));
  document.querySelectorAll('.mobile-tabbar [data-page]').forEach((el) => el.classList.toggle('active', el.dataset.page === page));
}

function updateStaticLinks() {
  const legacy = byId('legacy-dashboard-link');
  if (legacy) legacy.href = `${apiOrigin}/api/v1/dashboard`;
}

document.addEventListener('click', (event) => {
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
  const page = event.target.closest('[data-page], [data-page-jump]')?.dataset.page || event.target.closest('[data-page-jump]')?.dataset.pageJump;
  if (page) setPage(page);
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
  const envSpawn = event.target.closest('[data-env-spawn]');
  if (envSpawn) {
    setPage('environments');
    renderEnvironmentSpawnOptions(envSpawn.dataset.envSpawn);
    byId('env-spawn-agent-id')?.focus();
    return;
  }
  const sessionCheckbox = event.target.closest('[data-session-checkbox]');
  if (sessionCheckbox) {
    const id = sessionCheckbox.dataset.sessionCheckbox;
    if (sessionCheckbox.checked) state.selectedSessionIds.add(id);
    else state.selectedSessionIds.delete(id);
    renderSessionWorkspace();
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
    state.selectedSessionTab = sessionTab.dataset.sessionTab || 'chat';
    renderSessionWorkspace();
    return;
  }
  const bulkSessionButton = event.target.closest('[data-bulk-session-action]');
  if (bulkSessionButton) {
    requestBulkSessionControl(bulkSessionButton.dataset.bulkSessionAction);
    return;
  }
  const conversation = event.target.closest('[data-conversation]')?.dataset.conversation;
  if (conversation) {
    state.selectedConversation = conversation;
    renderConversations();
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
    navigator.clipboard?.writeText(copyRunButton.dataset.copyRunId || '');
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
  const inspectButton = event.target.closest('[data-inspect]');
  if (inspectButton) inspect(inspectButton.dataset.inspect, inspectButton.dataset.id);
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
  const inspectItem = event.target.closest('[data-kind]');
  if (inspectItem && !inspectButton) inspect(inspectItem.dataset.kind, inspectItem.dataset.id);
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeStatusWhy();
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-status-why]')) {
    event.preventDefault();
    openStatusWhy(event.target);
  }
});

byId('refresh').addEventListener('click', refresh);
byId('global-filter').addEventListener('input', (event) => {
  state.filter = event.target.value;
  renderAll();
});
byId('contract-state').addEventListener('change', renderContracts);
byId('run-status-filter').addEventListener('change', async (event) => {
  byId('api-status').textContent = 'filtering';
  byId('api-status').className = 'status-chip muted';
  try {
    await loadRunsForStatus(event.target.value);
    byId('api-status').textContent = 'live';
    byId('api-status').className = 'status-chip ok';
  } catch (error) {
    byId('api-status').textContent = 'API error';
    byId('api-status').className = 'status-chip bad';
    inspect('API error', { message: error.message });
  }
});
byId('env-spawn-environment')?.addEventListener('change', (event) => {
  byId('env-spawn-workspace').value = '';
  renderEnvironmentSpawnOptions(event.target.value);
});
byId('environment-spawn-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await createSpawnRequest();
  } catch (error) {
    inspect('spawn-error', { message: error.message || 'Spawn request failed' });
  }
});
byId('send-reminders').addEventListener('click', async () => {
  const result = await api('/contracts/reminders/run', { method: 'POST' });
  inspect('reminders', result);
  await refresh();
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

byId('composer').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = byId('composer-body').value.trim();
  const session = selectedSession();
  const to = session ? sessionAgentId(session) : state.selectedConversation;
  if (!body || to === 'dashboard') return;
  const type = byId('composer-type').value || 'info';
  const queueIfBusy = byId('composer-queue').checked;
  try {
    await sendMessageWithTimeout({
      from_agent: 'dashboard',
      to,
      type,
      priority: byId('composer-priority').value,
      subject: body.slice(0, 80),
      body,
      trigger: true,
      queueIfBusy,
      requireReply: ['request', 'review'].includes(type),
    });
    byId('composer-body').value = '';
    await refresh();
    renderSessionWorkspace();
  } catch (error) {
    inspect('send-error', { message: error.message || 'Send failed' });
  }
});
byId('mark-read')?.addEventListener('click', () => inspect('mark-read', { note: 'Mark-read is planned for the next slice.' }));
document.addEventListener('paste', (event) => {
  const target = event.target;
  if (!target || target.id !== 'composer-body') return;
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

updateStaticLinks();
setNavCollapsed(preferredNavCollapsed());
connectRealtimeSocket();
refresh();
setInterval(refresh, 15000);
byId('open-classic-settings')?.addEventListener('click', () => openClassic('settings'));
