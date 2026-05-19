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

const state = {
  agents: [],
  contracts: [],
  messages: [],
  runs: [],
  sessions: [],
  environments: [],
  stats: {},
  selectedConversation: 'dashboard',
  filter: '',
};

const pages = {
  control: ['Control', 'Who is available, what is moving, and what needs attention.'],
  chat: ['Chat', 'Direct messages first, with run and handoff state nearby.'],
  'work-loop': ['Work Loop', 'Open contracts, reminders, and handoff hygiene.'],
  sessions: ['Sessions', 'Runtime backings grouped by agent and environment.'],
  environments: ['Environments', 'Connected bridges, runtimes, roots, and capacity.'],
  runs: ['Runs', 'Execution audit without making operators read raw logs first.'],
  analytics: ['Analytics', 'Recent volume, health, and capacity signals.'],
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

const byId = (id) => document.getElementById(id);
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
    const [agents, contracts, messages, runs, sessions, environments, stats] = await Promise.all([
      api('/agents'),
      api('/contracts?limit=80'),
      api('/messages/inbox/dashboard?filter=all&peek=true&limit=80'),
      api('/dispatch/runs?limit=80'),
      api('/sessions?limit=80'),
      api('/environments'),
      api('/stats'),
    ]);
    state.agents = asAgentArray(agents);
    state.contracts = contracts.contracts || [];
    state.messages = messages.messages || [];
    state.runs = runs.runs || [];
    state.sessions = asArray(sessions, 'sessions');
    state.environments = asArray(environments, 'environments');
    state.stats = stats || {};
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
  renderAgents();
  renderMessages();
  renderConversations();
  renderContracts();
  renderRuntime();
  renderRuns();
  renderAnalytics();
}

function metric(label, value, tone = '') {
  return `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span>${tone ? `<small>${esc(tone)}</small>` : ''}</div>`;
}

function renderMetrics() {
  const working = state.agents.filter((a) => a.status === 'working').length;
  const blocked = state.agents.filter((a) => a.status === 'blocked').length;
  const active = state.agents.filter((a) => ['active', 'online', 'working', 'blocked'].includes(a.status)).length;
  const overdue = state.contracts.filter((c) => c.overdue).length;
  const queued = state.contracts.filter((c) => c.state === 'queued').length;
  byId('metrics').innerHTML = [
    metric('Active agents', active),
    metric('Working now', working),
    metric('Blocked agents', blocked),
    metric('Overdue work', overdue),
    metric('Queued contracts', queued),
  ].join('');
}

function contractCard(contract) {
  const tone = contract.overdue ? 'bad' : contract.state === 'working' ? 'warn' : 'muted';
  return `
    <article class="contract" data-kind="contract" data-id="${esc(contract.id)}">
      <div>
        <div class="item-title">
          <strong class="clip">${esc(contract.subject || contract.id)}</strong>
          <span class="status-chip ${tone}">${esc(contract.state || contract.status)}</span>
        </div>
        <p class="preview">${esc(contract.preview || '')}</p>
        <div class="contract-meta">
          ${esc(contract.from)} → ${esc(contract.targetAgentId)} · ${esc(contract.type)} · ${relTime(contract.requestedAt)} old · ${contract.lastReminderAt ? `last reminded ${relTime(contract.lastReminderAt)} ago` : 'not reminded'}
        </div>
      </div>
      <button class="ghost" data-inspect="contract" data-id="${esc(contract.id)}">Inspect</button>
    </article>`;
}

function renderAttention() {
  const items = filtered(state.contracts, ['subject', 'preview', 'from', 'targetAgentId'])
    .filter((c) => c.overdue || c.state === 'working' || c.state === 'queued')
    .slice(0, 8);
  byId('attention-list').innerHTML = items.length
    ? items.map(contractCard).join('')
    : '<div class="item"><strong>No open attention items</strong><p class="preview">The current Work Loop is clear.</p></div>';
}

function renderAgents() {
  const agents = filtered(state.agents, ['id', 'name', 'role', 'runtime', 'status']).slice(0, 18);
  byId('agent-list').innerHTML = agents.map((agent) => `
    <article class="agent" data-kind="agent" data-id="${esc(agent.id)}">
      <span class="dot ${esc(agent.status || '')}"></span>
      <div>
        <strong>${esc(agent.id)}</strong>
        <p class="preview">${esc(agent.runtime || 'runtime')} · ${esc(agent.sessionMode || '')} · ${esc(agent.machineId || '')}</p>
      </div>
      <span class="status-chip ${agent.status === 'working' ? 'warn' : agent.status === 'offline' ? 'bad' : 'ok'}">${esc(agent.status || 'unknown')}</span>
    </article>`).join('');
}

function renderMessages() {
  const messages = filtered(state.messages, ['from', 'subject', 'preview', 'body']).slice(0, 10);
  byId('message-list').innerHTML = messages.map((message) => `
    <article class="item" data-kind="message" data-id="${esc(message.id)}">
      <div class="item-title">
        <strong class="clip">${esc(message.subject || '(no subject)')}</strong>
        <span class="status-chip ${message.read ? 'muted' : 'warn'}">${message.read ? 'read' : 'unread'}</span>
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
    ...agents.map((agent) => `<button class="nav-item ${state.selectedConversation === agent.id ? 'active' : ''}" data-conversation="${esc(agent.id)}">${esc(agent.id)} <small>${esc(agent.status || '')}</small></button>`),
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
}

function renderRuntime() {
  byId('environment-list').innerHTML = state.environments.map((env) => `
    <article class="runtime-card" data-kind="environment" data-id="${esc(env.id)}">
      <div class="item-title"><strong>${esc(env.label || env.id)}</strong><span class="status-chip ${env.status === 'online' ? 'ok' : 'bad'}">${esc(env.status)}</span></div>
      <p class="preview">${esc(env.kind || env.os || '')} · ${esc(env.machineId || env.machine_id || '')}</p>
      <small>${esc((env.runtimes || env.runtimeCapabilities || []).map((r) => r.runtime || r).join(', '))}</small>
    </article>`).join('') || '<div class="item">No environments loaded.</div>';
  byId('session-groups').innerHTML = state.sessions.map((session) => `
    <article class="runtime-card" data-kind="session" data-id="${esc(session.id)}">
      <div class="item-title"><strong>${esc(session.agentId || session.agent_id || session.id)}</strong><span class="status-chip muted">${esc(session.status || '')}</span></div>
      <p class="preview">${esc(session.runtime || '')} · ${esc(session.environmentId || session.environment_id || '')}</p>
      <small>${esc(session.workspace || session.cwd || '')}</small>
    </article>`).join('') || '<div class="item">No sessions loaded.</div>';
}

function renderRuns() {
  const runs = filtered(state.runs, ['id', 'subject', 'targetAgentId', 'from', 'summary']).slice(0, 80);
  byId('run-list').innerHTML = runs.map((run) => `
    <article class="run-row" data-kind="run" data-id="${esc(run.id)}">
      <span class="status-chip ${['failed', 'cancelled'].includes(run.status) ? 'bad' : run.status === 'completed' ? 'ok' : 'warn'}">${esc(run.status)}</span>
      <span>${esc(run.targetAgentId || run.target_agent || '')}</span>
      <div><strong class="clip">${esc(run.subject || run.id)}</strong><p class="preview">${esc(run.summary || run.error || '')}</p></div>
      <button class="ghost" data-inspect="run" data-id="${esc(run.id)}">Inspect</button>
    </article>`).join('') || '<div class="item">No runs loaded.</div>';
}

function renderAnalytics() {
  byId('analytics-grid').innerHTML = [
    metric('Messages today', state.stats.messages_today || 0),
    metric('Completed runs 24h', state.stats.completed_runs_24h || 0),
    metric('Run failures 24h', state.stats.run_failures_24h || 0),
    metric('Unread messages', state.stats.unread_messages || 0),
    metric('Online environments', state.environments.filter((e) => e.status === 'online').length),
  ].join('');
  const counts = state.runs.reduce((acc, run) => {
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

function inspect(kind, payload) {
  const data = typeof payload === 'string'
    ? lookup(kind, payload)
    : payload;
  byId('inspector-content').innerHTML = `<pre>${esc(JSON.stringify(data || {}, null, 2))}</pre>`;
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
  const [title, subtitle] = pages[page] || pages.control;
  byId('page-title').textContent = title;
  byId('page-subtitle').textContent = subtitle;
  document.querySelectorAll('.page').forEach((el) => el.classList.toggle('active', el.id === `page-${page}`));
  document.querySelectorAll('.nav-item[data-page]').forEach((el) => el.classList.toggle('active', el.dataset.page === page));
}

function updateStaticLinks() {
  const legacy = byId('legacy-dashboard-link');
  if (legacy) legacy.href = `${apiOrigin}/api/v1/dashboard`;
}

document.addEventListener('click', (event) => {
  const page = event.target.closest('[data-page], [data-page-jump]')?.dataset.page || event.target.closest('[data-page-jump]')?.dataset.pageJump;
  if (page) setPage(page);
  const conversation = event.target.closest('[data-conversation]')?.dataset.conversation;
  if (conversation) {
    state.selectedConversation = conversation;
    renderConversations();
  }
  const inspectButton = event.target.closest('[data-inspect]');
  if (inspectButton) inspect(inspectButton.dataset.inspect, inspectButton.dataset.id);
  const inspectItem = event.target.closest('[data-kind]');
  if (inspectItem && !inspectButton) inspect(inspectItem.dataset.kind, inspectItem.dataset.id);
});

byId('refresh').addEventListener('click', refresh);
byId('global-filter').addEventListener('input', (event) => {
  state.filter = event.target.value;
  renderAll();
});
byId('contract-state').addEventListener('change', renderContracts);
byId('send-reminders').addEventListener('click', async () => {
  const result = await api('/contracts/reminders/run', { method: 'POST' });
  inspect('reminders', result);
  await refresh();
});
byId('composer').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = byId('composer-body').value.trim();
  const to = state.selectedConversation;
  if (!body || to === 'dashboard') return;
  await api('/messages/send', {
    method: 'POST',
    body: JSON.stringify({
      from_agent: 'dashboard',
      to,
      type: 'info',
      subject: body.slice(0, 80),
      body,
      trigger: true,
    }),
  });
  byId('composer-body').value = '';
  await refresh();
});
byId('mark-read').addEventListener('click', () => inspect('mark-read', { note: 'Mark-read is planned for the next slice.' }));
byId('close-inspector').addEventListener('click', () => {
  byId('inspector-content').textContent = 'Select an item to inspect details.';
});

updateStaticLinks();
refresh();
setInterval(refresh, 15000);
