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
  if (filter) {
    // Global search (parity with old dashboard): match the id/preview AND any loaded message
    // body in the conversation, so searching surfaces a DM by its message contents too.
    const bodyMatch = (i) => {
      if (i.kind !== 'dm') return false;
      return dmMessages(state.messages, i.id, identity).some((m) => String(m.body || m.subject || m.preview || '').toLowerCase().includes(filter));
    };
    items = items.filter((i) => i.id.toLowerCase().includes(filter) || i.preview.toLowerCase().includes(filter) || bodyMatch(i));
  }
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
  // DMs get a clickable star (PATCH /agents/{id}/favorite); channels have no server favorite.
  const fav = item.kind === 'dm'
    ? `<span class="chat-fav-toggle${item.favorited ? ' on' : ''}" data-fav-toggle="${esc(item.id)}" role="button" title="${item.favorited ? 'Unfavorite' : 'Favorite'}">${item.favorited ? '★' : '☆'}</span>`
    : (item.favorited ? '<span class="chat-fav" title="Favorite">★</span>' : '');
  return `<button class="chat-rail-item${active}" data-chat-open="${esc(item.key)}" title="${esc(item.id)}">
    <span class="chat-rail-head">${dot}<span class="chat-rail-name clip">${esc(item.id)}</span>${fav}${unread}</span>
    <span class="chat-rail-preview clip">${esc(item.preview || '')}</span>
  </button>`;
}

// Wake-vs-stored badge: a message that triggered a dispatch run "woke" the agent; otherwise
// it was stored to the inbox. read/unread shown alongside.
function messageHtml(m, identity = 'dashboard') {
  const id = String(m.id || m.messageId || '');
  const runId = String(m.dispatchRunId || m.dispatch_run_id || m.runId || m.run_id || '');
  const woke = !!runId || m.dispatchRequested || m.dispatch_requested;
  const priority = String(m.priority || '').toLowerCase();
  const mine = String(m.from || '') === String(identity);
  const badges = [
    `<span class="msg-badge ${m.read === false ? 'unread' : 'read'}">${m.read === false ? 'unread' : 'read'}</span>`,
    woke ? '<span class="msg-badge woke">woke</span>' : '<span class="msg-badge stored">stored</span>',
    m.type ? `<span class="msg-badge type t-${esc(m.type)}">${esc(m.type)}</span>` : '',
    (priority === 'high' || priority === 'urgent') ? `<span class="msg-badge p-${esc(priority)}">${esc(priority)}</span>` : '',
    (m.expectsReply || m.expects_reply) && m.read === false ? '<span class="msg-badge await">awaiting</span>' : '',
  ].join('');
  return `<article class="chat-msg${mine ? ' chat-msg-mine' : ''}" data-kind="message" data-id="${esc(id)}" id="chat-msg-${esc(id)}">
    <div class="chat-msg-head"><strong>${esc(m.from || 'unknown')}</strong>
      <span class="chat-msg-badges">${badges}${runId ? `<button class="run-chip" data-run-chip="${esc(runId)}" data-message-id="${esc(id)}">Run ${esc(runId.slice(0, 10))}</button>` : ''}<button class="chat-msg-reply" data-chat-reply="${esc(id)}" title="Reply to this message">Reply</button><button class="chat-msg-detail" data-message-detail="${esc(id)}" title="Message details">⋯</button></span>
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

// Per-agent analytics panel (reuses GET /analytics/agent/{id} — the revamped metrics).
// Pure HTML builder so it can be unit-tested and rendered into the timeline area.
export function renderAnalyticsPanelHtml(agentId, data) {
  if (!data || data.ok === false) return `<div class="chat-empty">Analytics unavailable for ${esc(agentId)}.</div>`;
  const wm = Math.max(0, Math.round(Number(data.workingMinutes || 0)));
  const workLabel = `${Math.floor(wm / 60)}h ${wm % 60}m`;
  const mr = Number(data.medianReplyMinutes7d || 0);
  const mrLabel = mr >= 60 ? `${Math.floor(mr / 60)}h ${Math.round(mr % 60)}m` : `${Math.round(mr)}m`;
  const runs = data.runs7d || {};
  const days = Array.isArray(data.dailyActivity) ? data.dailyActivity : [];
  const dayMax = Math.max(1, ...days.map((d) => Number(d.sent || 0) + Number(d.received || 0)));
  const dayBars = days.length ? days.map((d) => {
    const inN = Number(d.received || 0); const outN = Number(d.sent || 0);
    const w = Math.max(2, Math.round(((inN + outN) / dayMax) * 100));
    return `<div class="an-bar-row"><span class="an-bar-label">${esc(String(d.date || '').slice(5))}</span><span class="an-bar-track"><span class="an-bar-fill" style="width:${w}%"></span></span><span class="an-bar-val" title="${inN} received · ${outN} sent">${inN}↓ ${outN}↑</span></div>`;
  }).join('') : '<p class="subtle">No activity in 14 days.</p>';
  const peers = (Array.isArray(data.byPeer) ? data.byPeer : []).slice(0, 8);
  const peerMax = Math.max(1, ...peers.map((p) => Number(p.count || 0)));
  const peerBars = peers.length ? peers.map((p) => {
    const w = Math.max(2, Math.round((Number(p.count || 0) / peerMax) * 100));
    return `<div class="an-bar-row"><span class="an-bar-label clip">${esc(p.peer)}</span><span class="an-bar-track"><span class="an-bar-fill" style="width:${w}%"></span></span><span class="an-bar-val">${Number(p.count || 0)}</span></div>`;
  }).join('') : '<p class="subtle">No peers yet.</p>';
  const owed = Number(data.openContracts || 0);
  return `<div class="chat-analytics">
    <div class="an-cards">
      <div class="an-card"><div class="an-n">${Number(data.messagesReceived || 0)}</div><div class="an-l">Received</div></div>
      <div class="an-card"><div class="an-n">${Number(data.messagesSent || 0)}</div><div class="an-l">Sent</div></div>
      <div class="an-card"><div class="an-n">${esc(workLabel)}</div><div class="an-l">Working</div></div>
      <div class="an-card"><div class="an-n">${mr ? esc(mrLabel) : '—'}</div><div class="an-l">Median reply 7d</div></div>
      <div class="an-card"><div class="an-n${owed ? ' an-bad' : ''}">${owed}</div><div class="an-l">Owes replies</div></div>
    </div>
    <h4 class="an-h">Activity — 14 days (received↓ / sent↑)</h4>${dayBars}
    <h4 class="an-h">Work runs — 7 days</h4>
    <dl class="an-runs"><dt>Completed</dt><dd>${Number(runs.completed || 0)}</dd><dt>Failed</dt><dd>${Number(runs.failed || 0)}${runs.lastFailedSubject ? ` <span class="subtle clip" title="${esc(runs.lastFailedSubject)}">· ${esc(runs.lastFailedSubject)}</span>` : ''}</dd><dt>Avg turn</dt><dd>${data.avgRunMinutes7d ? `${data.avgRunMinutes7d} min` : '—'}</dd></dl>
    <h4 class="an-h">Top peers</h4>${peerBars}
  </div>`;
}

// Build the controller that renders the page and wires send. deps: { state, byId, sendMessage,
// loadChannels, refresh, loadConversation, loadAgentAnalytics }.
export function createChatController(deps) {
  const { state, byId, sendMessage, loadChannels, refresh, loadConversation, loadAgentAnalytics } = deps;

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
    // Analytics view survives polls/re-renders (state-tracked, not a one-shot DOM write).
    const an = state.chat.analytics || {};
    if (an.agent) {
      if (titleEl) titleEl.textContent = `Analytics · ${an.agent}`;
      const actions = byId('chat-conv-actions');
      if (actions) actions.innerHTML = `<button class="ghost" data-chat-open="dm:${esc(an.agent)}">Back to chat</button>`;
      timeline.innerHTML = an.data ? renderAnalyticsPanelHtml(an.agent, an.data) : '<div class="chat-empty">Loading analytics…</div>';
      const composer = byId('chat-composer');
      if (composer) composer.hidden = true;
      return;
    }
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
        actions.innerHTML = `<button class="ghost" data-agent-drawer="${esc(id)}">Details</button>`
          + `<button class="ghost" data-chat-analytics="${esc(id)}">Analytics</button>`;
      }
    }
    const msgs = isChannel
      ? (state.chat.channelMessages?.[id] || [])
      : dmMessages(state.messages, id, state.chat.identity);
    // Follow-bottom: only auto-scroll to the newest message if the operator was already near
    // the bottom — don't yank them down while they're reading scrollback.
    const nearBottom = (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight) < 80;
    timeline.innerHTML = msgs.length
      ? msgs.map((m) => messageHtml(m, state.chat.identity)).join('')
      : '<div class="chat-empty">No messages yet in this conversation.</div>';
    if (nearBottom) timeline.scrollTop = timeline.scrollHeight;
    const composer = byId('chat-composer');
    if (composer) composer.hidden = false;
    // Reply-context banner: when replying to a message, show what we're replying to + a clear button.
    const reply = state.chat.replyTo;
    let banner = byId('chat-reply-banner');
    if (reply && reply.conversationKey === key) {
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'chat-reply-banner';
        banner.className = 'chat-reply-banner';
        composer?.parentNode?.insertBefore(banner, composer);
      }
      banner.innerHTML = `<span class="clip">↳ Replying to <strong>${esc(reply.from)}</strong>: ${esc((reply.subject || reply.preview || '').slice(0, 60))}</span><button class="ghost" type="button" data-chat-reply-clear>✕</button>`;
    } else if (banner) {
      banner.remove();
    }
    const placeholder = byId('chat-composer-body');
    if (placeholder) {
      placeholder.placeholder = isChannel ? `Message #${id}` : `Message ${id}`;
      // Draft preservation: restore any per-conversation draft when switching in.
      const draft = state.chat.drafts?.[key];
      if (draft != null && placeholder.value !== draft && document.activeElement !== placeholder) placeholder.value = draft;
    }
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
    state.chat.analytics = { agent: '', data: null }; // leaving analytics view
    state.chat.selected = key;
    if (key.startsWith('channel:')) {
      const name = key.slice('channel:'.length);
      try { await loadConversation(name); } catch (_) { /* toast handled upstream */ }
    }
    render();
  }

  async function openAnalytics(agentId) {
    state.chat.analytics = { agent: agentId, data: null };
    renderConversation(); // shows the loading state immediately
    let data = { ok: false };
    try { data = await loadAgentAnalytics(agentId); } catch (_) { data = { ok: false }; }
    // Only paint if still viewing this agent's analytics (avoid a stale async overwrite).
    if (state.chat.analytics.agent === agentId) {
      state.chat.analytics.data = data;
      renderConversation();
    }
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
    const reply = state.chat.replyTo;
    const inReplyTo = (reply && reply.conversationKey === key) ? reply.id : '';
    try {
      const response = await sendMessage({
        isChannel, target: id, identity: state.chat.identity, body,
        expectsReply: !!expectsReply, queueIfBusy: !!queueIfBusy, inReplyTo,
      });
      if (bodyEl) bodyEl.value = '';
      // Sent cleanly: drop the saved draft + reply context for this conversation.
      if (state.chat.drafts) delete state.chat.drafts[key];
      state.chat.replyTo = null;
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

  return { render, open, openAnalytics, send, renderRail, renderConversation };
}
