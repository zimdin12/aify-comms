// Chat-first landing (DASHBOARD_REBUILD_PLAN §3.1): a conversation rail (DMs + channels)
// with presence dots, unread, favorites, identity switcher, and search; a threaded timeline
// with read/unread + wake-vs-stored badges; and a composer with an "expects reply" toggle,
// queue option, and the delivery-truthfulness toast ladder. Pure rail/timeline builders are
// exported for unit testing; the page wires app state + send via createChatController().
import { esc, relTime } from './util.js';
import { renderStatusChip, resolveStatus } from './status.js';
import { toast } from './ui.js';

// Which of state.messages belong to a DM conversation with `agentId`, scoped to the viewing
// identity. (The server already scopes the dashboard inbox; this is the per-peer filter.)
export function dmMessages(messages, agentId, identity = 'dashboard') {
  const peer = String(agentId || '');
  return (messages || []).filter((m) => {
    const from = String(m.from || '');
    const to = String(m.to || m.targetAgentId || m.target_agent_id || '');
    return from === peer || to === peer;
  });
}

// Build rail items (DMs + channels) from state. Pure → unit-tested. Sort: unread first, then
// favorites, then most-recent activity, then id.
export function chatConversationItems(state) {
  const identity = state.chat?.identity || 'dashboard';
  const filter = String(state.chat?.filter || '').trim().toLowerCase();
  const liveOnly = !!state.chat?.liveOnly;
  const ts = (v) => { const n = Date.parse(String(v || '')); return Number.isFinite(n) ? n : Number(v) || 0; };

  const dms = (state.agents || [])
    .filter((a) => a.id && a.id !== 'dashboard')
    .map((a) => {
      const msgs = dmMessages(state.messages, a.id, identity);
      const last = msgs[msgs.length - 1];
      const unread = msgs.filter((m) => m.read === false).length;
      return {
        kind: 'dm', key: `dm:${a.id}`, id: a.id, status: a.status || 'unknown',
        statusNote: a.statusNote || a.status_note || '',
        preview: last ? (last.subject || last.preview || last.body || '') : '',
        unread, favorited: !!a.favorited, lastTs: ts(last?.timestamp || last?.createdAt),
      };
    });

  const channels = (state.chat?.channels || []).map((c) => ({
    kind: 'channel', key: `channel:${c.name}`, id: c.name, status: 'online',
    preview: c.description || `${c.memberCount ?? (c.members?.length || 0)} members`,
    unread: c.unreadCount || 0, favorited: false, lastTs: ts(c.lastMessageAt || c.createdAt),
  }));

  let items = [...dms, ...channels];
  const live = new Set(['working', 'online', 'idle', 'available', 'blocked']);
  if (liveOnly) items = items.filter((i) => i.kind === 'channel' || live.has(resolveStatus(i.status).kind));
  if (filter) items = items.filter((i) => i.id.toLowerCase().includes(filter) || i.preview.toLowerCase().includes(filter));
  items.sort((a, b) => (
    (b.unread > 0) - (a.unread > 0)
    || (b.favorited - a.favorited)
    || (b.lastTs - a.lastTs)
    || a.id.localeCompare(b.id)
  ));
  return items;
}

function railItemHtml(item, selectedKey) {
  const active = item.key === selectedKey ? ' active' : '';
  const dot = item.kind === 'dm'
    ? `<span class="status-dot ${esc(resolveStatus(item.status).dotKind)}"></span>`
    : '<span class="chat-rail-hash">#</span>';
  const unread = item.unread > 0 ? `<span class="chat-unread">${item.unread}</span>` : '';
  const fav = item.favorited ? '<span class="chat-fav" title="Favorite">★</span>' : '';
  return `<button class="chat-rail-item${active}" data-chat-open="${esc(item.key)}" title="${esc(item.id)}">
    <span class="chat-rail-head">${dot}<span class="chat-rail-name clip">${esc(item.id)}</span>${fav}${unread}</span>
    <span class="chat-rail-preview clip">${esc(item.preview || '')}</span>
  </button>`;
}

// Wake-vs-stored badge: a message that triggered a dispatch run "woke" the agent; otherwise
// it was stored to the inbox. read/unread shown alongside.
function messageHtml(m) {
  const id = String(m.id || m.messageId || '');
  const runId = String(m.dispatchRunId || m.dispatch_run_id || m.runId || m.run_id || '');
  const woke = !!runId || m.dispatchRequested || m.dispatch_requested;
  const badges = [
    `<span class="msg-badge ${m.read === false ? 'unread' : 'read'}">${m.read === false ? 'unread' : 'read'}</span>`,
    woke ? '<span class="msg-badge woke">woke</span>' : '<span class="msg-badge stored">stored</span>',
    m.type ? `<span class="msg-badge type">${esc(m.type)}</span>` : '',
  ].join('');
  return `<article class="chat-msg" data-kind="message" data-id="${esc(id)}" id="chat-msg-${esc(id)}">
    <div class="chat-msg-head"><strong>${esc(m.from || 'unknown')}</strong>
      <span class="chat-msg-badges">${badges}${runId ? `<button class="run-chip" data-run-chip="${esc(runId)}" data-message-id="${esc(id)}">Run ${esc(runId.slice(0, 10))}</button>` : ''}</span>
    </div>
    ${m.subject ? `<h4 class="chat-msg-subject">${esc(m.subject)}</h4>` : ''}
    <p class="chat-msg-body">${esc(m.body || m.preview || '')}</p>
    <small class="chat-msg-time">${esc(relTime(m.timestamp || m.createdAt))} ago</small>
  </article>`;
}

// Map a /messages/send response to a single truthful delivery toast (the plan's "ladder":
// steered / queued-busy / console-delivered / woke / stored-offline).
export function deliveryToastFor(response, to) {
  const run = (response?.runs || [])[0] || {};
  const status = String(run.status || '').toLowerCase();
  const consoleDelivered = (response?.consoleDeliveries || []).length > 0;
  const notStarted = (response?.notStarted || []).length > 0;
  if (response && response.ok === false) return { tone: 'error', text: `Not delivered to ${to}: ${response.error || 'recipient cannot start live work'}` };
  if (run.steered) return { tone: 'ok', text: `Steered into ${to}'s active turn` };
  if (status === 'queued') return { tone: 'info', text: `Queued behind ${to}'s active work` };
  if (consoleDelivered) return { tone: 'ok', text: `Delivered to ${to}'s console` };
  if (status === 'claimed' || status === 'running' || status === 'delivered') return { tone: 'ok', text: `Woke ${to}` };
  if (notStarted) return { tone: 'warn', text: `Stored for ${to} (no live worker to wake)` };
  return { tone: 'ok', text: `Sent to ${to}` };
}

// Build the controller that renders the page and wires send. deps: { state, byId, sendMessage,
// loadChannels, refresh, loadConversation }.
export function createChatController(deps) {
  const { state, byId, sendMessage, loadChannels, refresh, loadConversation } = deps;

  function renderRail() {
    const host = byId('chat-rail-list');
    if (!host) return;
    const items = chatConversationItems(state);
    const dmItems = items.filter((i) => i.kind === 'dm');
    const chItems = items.filter((i) => i.kind === 'channel');
    host.innerHTML = (
      `<div class="chat-rail-section">Direct messages</div>`
      + (dmItems.length ? dmItems.map((i) => railItemHtml(i, state.chat.selected)).join('') : '<p class="subtle chat-rail-empty">No agents.</p>')
      + `<div class="chat-rail-section">Channels</div>`
      + (chItems.length ? chItems.map((i) => railItemHtml(i, state.chat.selected)).join('') : '<p class="subtle chat-rail-empty">No channels.</p>')
    );
  }

  function renderConversation() {
    const titleEl = byId('chat-conv-title');
    const timeline = byId('chat-timeline');
    if (!timeline) return;
    const key = state.chat.selected;
    if (!key) {
      if (titleEl) titleEl.textContent = 'Select a conversation';
      timeline.innerHTML = '<div class="chat-empty">Pick a direct message or channel from the left to start.</div>';
      const composer = byId('chat-composer');
      if (composer) composer.hidden = true;
      return;
    }
    const isChannel = key.startsWith('channel:');
    const id = key.slice(key.indexOf(':') + 1);
    if (titleEl) titleEl.textContent = isChannel ? `#${id}` : id;
    // Channel management actions (join/leave/read) reflect membership for the viewing identity.
    const actions = byId('chat-conv-actions');
    if (actions) {
      if (isChannel) {
        const chan = (state.chat.channels || []).find((c) => c.name === id) || {};
        const members = chan.members || [];
        const isMember = members.includes(state.chat.identity);
        const count = chan.memberCount ?? members.length;
        actions.innerHTML = `<span class="chat-members" title="${esc(members.join(', '))}">${count} member${count === 1 ? '' : 's'}</span>`
          + (isMember
            ? `<button class="ghost" data-chat-channel-action="leave" data-channel="${esc(id)}">Leave</button>`
            : `<button class="ghost" data-chat-channel-action="join" data-channel="${esc(id)}">Join</button>`)
          + `<button class="ghost" data-chat-channel-action="read" data-channel="${esc(id)}">Mark read</button>`;
      } else {
        actions.innerHTML = '';
      }
    }
    const msgs = isChannel
      ? (state.chat.channelMessages?.[id] || [])
      : dmMessages(state.messages, id, state.chat.identity);
    timeline.innerHTML = msgs.length
      ? msgs.map(messageHtml).join('')
      : '<div class="chat-empty">No messages yet in this conversation.</div>';
    timeline.scrollTop = timeline.scrollHeight;
    const composer = byId('chat-composer');
    if (composer) composer.hidden = false;
    const placeholder = byId('chat-composer-body');
    if (placeholder) placeholder.placeholder = isChannel ? `Message #${id}` : `Message ${id}`;
  }

  function renderIdentityOptions() {
    const sel = byId('chat-identity');
    if (!sel) return;
    const ids = ['dashboard', ...(state.agents || []).map((a) => a.id).filter((id) => id && id !== 'dashboard').sort()];
    const html = ids.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
    if (sel.dataset.optionSig !== html) { sel.innerHTML = html; sel.dataset.optionSig = html; }
    if (sel.value !== state.chat.identity) sel.value = state.chat.identity;
  }

  function render() {
    renderIdentityOptions();
    renderRail();
    renderConversation();
  }

  async function open(key) {
    state.chat.selected = key;
    if (key.startsWith('channel:')) {
      const name = key.slice('channel:'.length);
      try { await loadConversation(name); } catch (_) { /* toast handled upstream */ }
    }
    render();
  }

  async function send() {
    const bodyEl = byId('chat-composer-body');
    const body = (bodyEl?.value || '').trim();
    const key = state.chat.selected;
    if (!body || !key) return;
    const isChannel = key.startsWith('channel:');
    const id = key.slice(key.indexOf(':') + 1);
    const expectsReply = byId('chat-expects-reply')?.checked;
    const queueIfBusy = byId('chat-queue')?.checked;
    try {
      const response = await sendMessage({
        isChannel, target: id, identity: state.chat.identity, body,
        expectsReply: !!expectsReply, queueIfBusy: !!queueIfBusy,
      });
      if (bodyEl) bodyEl.value = '';
      if (!isChannel) {
        const t = deliveryToastFor(response, id);
        toast(t.text, t.tone);
      } else {
        toast(`Posted to #${id}`, 'ok');
      }
      await refresh();
      if (isChannel) { try { await loadConversation(id); } catch (_) {} }
      render();
    } catch (error) {
      toast(`Send failed: ${error?.message || error}`, 'error');
    }
  }

  return { render, open, send, renderRail, renderConversation };
}
