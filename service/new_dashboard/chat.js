// Chat-first landing (DASHBOARD_REBUILD_PLAN §3.1): a conversation rail (DMs + channels)
// with presence dots, unread, favorites, identity switcher, and search; a threaded timeline
// with read/unread + wake-vs-stored badges; and a composer with an "expects reply" toggle,
// queue option, and the delivery-truthfulness toast ladder. Pure rail/timeline builders are
// exported for unit testing; the page wires app state + send via createChatController().
import { esc, relTime } from './util.js';
import { renderStatusChip, resolveStatus } from './status.js';
import { toast } from './ui.js';
import { fleetPulseHtml } from './analytics.js';

// Which of state.messages belong to a DM conversation with `agentId`, scoped to the viewing
// identity. (The server already scopes the dashboard inbox; this is the per-peer filter.)
export function dmMessages(messages, agentId, identity = 'dashboard') {
  const peer = String(agentId || '');
  return (messages || []).filter((m) => {
    // Exclude channel-broadcast rows (bughunt 2026-07-03): /messages/recent returns
    // channel posts (source:'channel', to:null); without this guard a channel message
    // rendered inside a DM timeline and got DM-only controls (Reply/Mark-read/Unsend)
    // that misfire — Mark-read 403s on the NULL recipient, Unsend deletes the channel
    // post from the wrong surface, and the DM rail preview/count is polluted.
    if (String(m.source || '') === 'channel' || m.channel) return false;
    const from = String(m.from || '');
    const to = String(m.to || m.targetAgentId || m.target_agent_id || '');
    return from === peer || to === peer;
  });
}

// A chat timeline renders OLDEST→NEWEST (newest at the bottom), but DM rows arrive from
// `/messages/recent` in DESCENDING order (newest first). Sorting ascending by timestamp here
// is the single fix for the 2026-07-06 "my sent message never appears" bug: without it the
// just-sent row landed at the top and the auto-scroll yanked the operator to old scrollback.
// Pure + stable (returns a NEW array; falls back across timestamp/createdAt/time field names).
export function sortChronological(messages) {
  const ts = (m) => Number(m && (m.timestamp ?? m.createdAt ?? m.time) || 0) || 0;
  return (messages || []).slice().sort((a, b) => ts(a) - ts(b));
}

// Rank for the "status" sort + working-first hoist: busy/blocked float up, dead sink.
// 6-state proof-based model (idle/stale were removed 2026-06-18 and normalize away in status.js).
const STATUS_SORT_RANK = { working: 0, blocked: 1, online: 2, available: 3, offline: 4, stopped: 5, unknown: 6 };
function statusRank(kind) { return STATUS_SORT_RANK[kind] ?? 6; }

// Build rail items (DMs + channels) from state. Pure → unit-tested. Honors sortMode, the
// live-only / open-only / working-first toggles, the status filter set, and global text search.
export function chatConversationItems(state) {
  const chat = state.chat || {};
  const identity = chat.identity || 'dashboard';
  const filter = String(chat.filter || '').trim().toLowerCase();
  const liveOnly = !!chat.liveOnly;
  const openOnly = !!chat.openOnly;
  const workingUp = !!chat.workingUp;
  const unreadOnly = !!chat.unreadOnly;
  const scope = chat.scope || 'all'; // 'all' | 'dm' | 'channel' | 'favorites'
  const sortMode = chat.sortMode || 'activity';
  const statusSet = chat.statusFilter instanceof Set ? chat.statusFilter : null;
  const ts = (v) => { const n = Date.parse(String(v || '')); return Number.isFinite(n) ? n : Number(v) || 0; };

  // Bucket messages by peer once (O(M)) so per-agent lookups and search are O(1) instead of
  // re-filtering the whole message list per agent (was O(A·M), doubled on every search keystroke).
  const buckets = new Map();
  const push = (peer, m) => { const b = buckets.get(peer); if (b) b.push(m); else buckets.set(peer, [m]); };
  for (const m of (state.messages || [])) {
    const from = String(m.from || '');
    const to = String(m.to || m.targetAgentId || m.target_agent_id || '');
    if (from) push(from, m);
    if (to && to !== from) push(to, m);
  }

  const dms = (state.agents || [])
    .filter((a) => a.id && a.id !== 'dashboard')
    .map((a) => {
      const msgs = buckets.get(a.id) || [];
      const last = msgs[msgs.length - 1];
      // Unread = messages addressed TO the viewing identity only. state.messages is the
      // fleet-wide feed, so counting every read===false also counted third-party DMs and
      // channel posts (whose read is always false — no recipient receipt) → permanently
      // stuck badges that no mark-read could clear (review finding #1/#8).
      const me = String(state.chat?.identity || 'dashboard');
      const unread = msgs.filter((m) => m.read === false && String(m.to || m.targetAgentId || m.target_agent_id || '') === me).length;
      return {
        kind: 'dm', key: `dm:${a.id}`, id: a.id, status: a.status || 'unknown',
        statusNote: a.statusNote || a.status_note || '',
        role: a.role || '',
        runtime: a.runtime || a.runtimeId || '',
        preview: last ? (last.subject || last.preview || last.body || '') : '',
        msgCount: msgs.length,
        unread, favorited: !!a.favorited, lastTs: ts(last?.timestamp || last?.createdAt),
      };
    });

  const channels = (state.chat?.channels || []).filter((c) => c && c.name).map((c) => ({
    kind: 'channel', key: `channel:${c.name}`, id: c.name, status: 'online', runtime: '',
    preview: c.description || `${c.memberCount ?? (c.members?.length || 0)} members`,
    msgCount: c.messageCount ?? c.message_count ?? 0,
    unread: c.unreadCount || 0, favorited: false, lastTs: ts(c.lastMessageAt || c.createdAt),
  }));

  let items = [...dms, ...channels];
  // Scope (DMs / Channels / Favorites) — a top-level lens over the rail.
  if (scope === 'dm') items = items.filter((i) => i.kind === 'dm');
  else if (scope === 'channel') items = items.filter((i) => i.kind === 'channel');
  else if (scope === 'favorites') items = items.filter((i) => i.favorited);
  const live = new Set(['working', 'online', 'available', 'blocked']);
  if (liveOnly) items = items.filter((i) => i.kind === 'channel' || live.has(resolveStatus(i.status).kind));
  if (unreadOnly) items = items.filter((i) => i.unread > 0);
  // Status dots filter STRICTLY (a filter should narrow): channels are exempt (no agent status),
  // but a DM only shows if its status is selected — no unread/favorited escape hatch.
  if (statusSet && statusSet.size) items = items.filter((i) => i.kind === 'channel' || statusSet.has(resolveStatus(i.status).kind));
  if (openOnly) items = items.filter((i) => i.kind === 'channel' ? i.unread > 0 : i.msgCount > 0);
  if (filter) {
    // Global search (parity with old dashboard): match id/preview AND any loaded message body.
    const bodyMatch = (i) => i.kind === 'dm'
      && (buckets.get(i.id) || []).some((m) => String(m.body || m.subject || m.preview || '').toLowerCase().includes(filter));
    items = items.filter((i) => i.id.toLowerCase().includes(filter) || i.preview.toLowerCase().includes(filter) || bodyMatch(i));
  }

  const byMode = (a, b) => {
    if (sortMode === 'name') return a.id.localeCompare(b.id);
    if (sortMode === 'name-desc') return b.id.localeCompare(a.id);
    if (sortMode === 'unread') return (b.unread - a.unread) || (b.lastTs - a.lastTs);
    if (sortMode === 'status') return (statusRank(resolveStatus(a.status).kind) - statusRank(resolveStatus(b.status).kind)) || (b.lastTs - a.lastTs);
    if (sortMode === 'runtime') return String(a.runtime).localeCompare(String(b.runtime)) || (b.lastTs - a.lastTs);
    return b.lastTs - a.lastTs; // activity (default)
  };
  items.sort((a, b) => (
    (b.favorited - a.favorited)                              // favorites always float
    || (workingUp ? (statusRank(resolveStatus(a.status).kind) - statusRank(resolveStatus(b.status).kind)) : 0)
    || byMode(a, b)
    || a.id.localeCompare(b.id)
  ));
  return items;
}

// Chat overview shown when no conversation is open (re-click an open chat to return here).
function railItemHtml(item, selectedKey, drafts = {}) {
  const active = item.key === selectedKey ? ' active' : '';
  const favClass = item.favorited ? ' fav' : '';
  const hasDraft = !!((drafts[item.key] || '').trim());
  const draftBadge = hasDraft ? '<span class="chat-draft-badge" title="Half-written message saved for this chat">draft</span>' : '';
  const dotStatus = item.kind === 'dm' ? resolveStatus(item.status) : null;
  const dot = item.kind === 'dm'
    ? `<span class="status-dot ${esc(dotStatus.dotKind)}" role="img" title="${esc(dotStatus.label)}" aria-label="${esc(dotStatus.label)}"></span>`
    : '<span class="chat-rail-hash">#</span>';
  const unread = item.unread > 0 ? `<span class="chat-unread">${item.unread}</span>` : '';
  // Loud awaiting-input marker (operator feedback 2026-07-02): a blocked agent (a real
  // prompt paused its turn) needs the operator — a red dot alone was too easy to miss.
  const awaitBadge = item.kind === 'dm' && dotStatus?.dotKind === 'blocked'
    ? '<span class="chat-await-badge" title="Agent is blocked on an interactive prompt — open its Console">⌛ input</span>'
    : '';
  // DMs get a clickable star (PATCH /agents/{id}/favorite); channels have no server favorite.
  const fav = item.kind === 'dm'
    ? `<span class="chat-fav-toggle${item.favorited ? ' on' : ''}" data-fav-toggle="${esc(item.id)}" role="button" tabindex="0" aria-label="${item.favorited ? 'Unfavorite' : 'Favorite'} ${esc(item.id)}" title="${item.favorited ? 'Unfavorite' : 'Favorite'}">${item.favorited ? '★' : '☆'}</span>`
    : (item.favorited ? '<span class="chat-fav" title="Favorite">★</span>' : '');
  // Sub-line carries the same compact context the old dashboard showed: "role · status · preview"
  // for DMs (parity target), preview-only for channels. Keeps each row scannable at a glance.
  const meta = item.kind === 'dm'
    ? [item.role, resolveStatus(item.status).label].filter(Boolean).join(' · ')
    : '';
  const sub = [meta, item.preview || ''].filter(Boolean).join(' · ');
  return `<button class="chat-rail-item${active}${favClass}" data-chat-open="${esc(item.key)}" title="${esc(item.id)}">
    <span class="chat-rail-head">${fav}${dot}<span class="chat-rail-name clip">${esc(item.id)}</span>${awaitBadge}${draftBadge}${unread}</span>
    <span class="chat-rail-preview clip">${esc(sub)}</span>
  </button>`;
}

// Wake-vs-stored badge: a message that triggered a dispatch run "woke" the agent; otherwise
// it was stored to the inbox. read/unread shown alongside.
function messageHtml(m, identity = 'dashboard', isChannel = false) {
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
  const runChip = runId ? `<button class="run-chip" data-run-chip="${esc(runId)}" data-message-id="${esc(id)}">Run ${esc(runId.slice(0, 10))}</button>` : '';
  // Reply / read-toggle / unsend all operate on state.messages (DM store); channel messages
  // live in state.chat.channelMessages and use fan-out read ids, so those controls would be
  // dead/incorrect on channel rows — only show them for DMs.
  const reply = !isChannel ? `<button class="chat-msg-reply" data-chat-reply="${esc(id)}" title="Reply to this message">Reply</button>` : '';
  const readToggle = (!mine && !isChannel) ? `<button class="chat-msg-act" data-msg-read="${esc(id)}" data-read="${m.read === false ? '0' : '1'}" title="Mark ${m.read === false ? 'read' : 'unread'}">${m.read === false ? 'Mark read' : 'Unread'}</button>` : '';
  const unsendBtn = (mine && !isChannel) ? `<button class="chat-msg-act danger" data-msg-unsend="${esc(id)}" title="Unsend this message">Unsend</button>` : '';
  // The ⋯ detail lookup only searches the DM store, so it's dead on channel rows — DMs only.
  const detail = !isChannel ? `<button class="chat-msg-detail" data-message-detail="${esc(id)}" aria-label="Message details" title="Message details">⋯</button>` : '';
  const actions = `${runChip}${reply}${readToggle}${unsendBtn}${detail}`;
  return `<article class="chat-msg${mine ? ' chat-msg-mine' : ''}" data-kind="message" data-id="${esc(id)}" id="chat-msg-${esc(id)}">
    <div class="chat-msg-head"><strong>${esc(m.from || 'unknown')}</strong>
      <span class="chat-msg-badges">${badges}${actions}</span>
    </div>
    ${m.subject ? `<h4 class="chat-msg-subject">${esc(m.subject)}</h4>` : ''}
    <p class="chat-msg-body">${esc(m.body || m.preview || '')}</p>
    <small class="chat-msg-time">${(() => { const t = relTime(m.timestamp || m.createdAt); return t ? esc(t) + ' ago' : ''; })()}</small>
  </article>`;
}

// Map a /messages/send response to a single truthful delivery toast (the plan's "ladder":
// steered / queued-busy / console-delivered / woke / stored-offline).
export function deliveryToastFor(response, to) {
  // /messages/send returns the runs under `dispatchRuns`, not `runs` — reading the wrong key
  // silently collapsed the steered/queued/woke ladder to a generic "Sent".
  const run = (response?.dispatchRuns || response?.runs || [])[0] || {};
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
  const peers = (Array.isArray(data.byPeer) ? data.byPeer : []).slice(0, 5);
  const peerMax = Math.max(1, ...peers.map((p) => Number(p.count || 0)));
  const peerBars = peers.length ? peers.map((p) => {
    const w = Math.max(2, Math.round((Number(p.count || 0) / peerMax) * 100));
    return `<div class="an-bar-row"><span class="an-bar-label clip">${esc(p.peer)}</span><span class="an-bar-track"><span class="an-bar-fill" style="width:${w}%"></span></span><span class="an-bar-val">${Number(p.count || 0)}</span></div>`;
  }).join('') : '<p class="subtle">No peers yet.</p>';
  const owed = Number(data.openContracts || 0);
  // Run success rate 7d (completed / (completed+failed)) — the per-agent analogue of the global leaderboard.
  const _comp = Number(runs.completed || 0), _fail = Number(runs.failed || 0);
  const succ = (_comp + _fail) > 0 ? Math.round((_comp / (_comp + _fail)) * 100) : null;
  // Daily activity — 14-day in/out series (already returned by the endpoint; was never rendered).
  const daily = Array.isArray(data.dailyActivity) ? data.dailyActivity : [];
  const dGet = (d) => ({ inc: Number(d.received ?? d.in ?? 0), out: Number(d.sent ?? d.out ?? 0) });
  const dMax = Math.max(1, ...daily.map((d) => { const v = dGet(d); return v.inc + v.out; }));
  const dailyBars = daily.map((d) => {
    const v = dGet(d); const tot = v.inc + v.out;
    const h = Math.max(2, Math.round((tot / dMax) * 100));
    return `<span class="an-hod-col" title="${esc(String(d.date || d.day || ''))}: ${v.inc} in / ${v.out} out"><span class="an-hod-fill" style="height:${h}%"></span></span>`;
  }).join('');
  const dailySection = daily.length ? `<h4 class="an-h">Daily activity — 14 days</h4><div class="an-hod">${dailyBars}</div>` : '';
  // Hour-of-day histogram (0..23, all-time) — when is this agent most active?
  const hod = Array.isArray(data.messagesPerHourOfDay) ? data.messagesPerHourOfDay : [];
  const hodMax = Math.max(1, ...hod.map((b) => Number(b.count || 0)));
  const hodBars = hod.length ? hod.map((b) => {
    const c = Number(b.count || 0);
    const h = Math.max(2, Math.round((c / hodMax) * 100));
    return `<span class="an-hod-col" title="${String(b.hour).padStart(2, '0')}:00 — ${c} msg"><span class="an-hod-fill" style="height:${h}%"></span></span>`;
  }).join('') : '';
  const hodSection = hod.length
    ? `<h4 class="an-h">By hour of day (UTC, all-time)</h4><div class="an-hod">${hodBars}</div>`
    : '';
  return `<div class="chat-analytics">
    <div class="an-cards">
      <div class="an-card"><div class="an-n">${Number(data.messagesReceived || 0)}</div><div class="an-l">Received</div></div>
      <div class="an-card"><div class="an-n">${Number(data.messagesSent || 0)}</div><div class="an-l">Sent</div></div>
      <div class="an-card"><div class="an-n" title="Total time this agent has spent as a dispatch target, all-time">${esc(workLabel)}</div><div class="an-l">Working (total)</div></div>
      <div class="an-card"><div class="an-n">${mr ? esc(mrLabel) : '—'}</div><div class="an-l">Median reply 7d</div></div>
      <div class="an-card"><div class="an-n">${succ == null ? '—' : succ + '%'}</div><div class="an-l">Run success 7d</div></div>
      <div class="an-card"><div class="an-n${owed ? ' an-bad' : ''}">${owed}</div><div class="an-l">Owes replies</div></div>
    </div>
    ${dailySection}
    <h4 class="an-h">Work runs — 7 days</h4>
    <dl class="an-runs"><dt>Completed</dt><dd>${Number(runs.completed || 0)}</dd><dt>Failed</dt><dd>${Number(runs.failed || 0)}${runs.lastFailedSubject ? ` <span class="subtle clip" title="${esc(runs.lastFailedSubject)}">· ${esc(runs.lastFailedSubject)}</span>` : ''}</dd><dt>Open</dt><dd>${Number(runs.open || 0)}</dd><dt>Avg turn</dt><dd>${data.avgRunMinutes7d ? `${data.avgRunMinutes7d} min` : '—'}</dd></dl>
    ${hodSection}
    <h4 class="an-h">Top peers</h4>${peerBars}
  </div>`;
}

// Build the controller that renders the page and wires send. deps: { state, byId, sendMessage,
// refresh, loadConversation, loadAgentAnalytics, ... } (channel loading is driven from app.js).
export function createChatController(deps) {
  const { state, byId, sendMessage, refresh, loadConversation, loadAgentAnalytics, mountChatConsole, loadPulse, persistDrafts } = deps;

  // One-shot "pin to newest" flag. The timeline renders oldest→newest, so the
  // newest message lives at the BOTTOM. On a fresh conversation open (or right
  // after the operator sends), we must land on the bottom regardless of the
  // current scroll position — otherwise open() would leave the operator staring
  // at the OLDEST scrollback (2026-07-06 fix follow-up). Poll re-renders still
  // use the gentler follow-bottom heuristic so we never yank someone reading up.
  let forceScrollBottom = false;

  function renderRail() {
    const host = byId('chat-rail-list');
    if (!host) return;
    const items = chatConversationItems(state);
    const dmItems = items.filter((i) => i.kind === 'dm');
    const chItems = items.filter((i) => i.kind === 'channel');
    // Before the first successful refresh, show "Loading…" rather than "No agents." so a cold
    // load (or a slow cross-origin fetch) never looks like an empty/broken roster.
    const loaded = state.loaded !== false;
    const dmEmpty = loaded ? 'No agents.' : 'Loading…';
    const chEmpty = loaded ? 'No channels.' : 'Loading…';
    const html = (
      `<div class="chat-rail-section">Direct messages</div>`
      + (dmItems.length ? dmItems.map((i) => railItemHtml(i, state.chat.selected, state.chat.drafts)).join('') : `<p class="subtle chat-rail-empty">${dmEmpty}</p>`)
      + `<div class="chat-rail-section">Channels</div>`
      + (chItems.length ? chItems.map((i) => railItemHtml(i, state.chat.selected, state.chat.drafts)).join('') : `<p class="subtle chat-rail-empty">${chEmpty}</p>`)
    );
    // Re-render guard (2026-06-19): re-setting innerHTML on every poll recreates every .status-dot,
    // which RESTARTS the `working` pulse animation each cycle — a steady `working` dot then visibly
    // flickers ("sc-coder changes status all the time" while it was solidly working server-side).
    // Only touch the DOM when the rendered rail actually changed; listeners are delegated, so
    // skipping an identical rebuild loses nothing. The dots then persist and the pulse runs smooth.
    if (host.__railHtml === html) return;
    host.__railHtml = html;
    host.innerHTML = html;
  }

  function renderConversation() {
    const titleEl = byId('chat-conv-title');
    const timeline = byId('chat-timeline');
    if (!timeline) return;
    const msgSearch = byId('chat-msg-search');
    if (msgSearch) msgSearch.hidden = true; // shown only in the message view below
    byId('chat-scroll-bottom')?.classList.add('hidden'); // re-shown only in the message view below
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
      if (titleEl) titleEl.textContent = 'Fleet pulse';
      const actions = byId('chat-conv-actions');
      if (actions) actions.innerHTML = '';
      timeline.innerHTML = fleetPulseHtml(state.chat.pulse.data, state.chat.pulse.window);
      const composer = byId('chat-composer');
      if (composer) composer.hidden = true;
      const search = byId('chat-msg-search');
      if (search) search.hidden = true;
      // Lazy-load the pulse the first time we land here; window changes/refresh force a refetch.
      if (!state.chat.pulse.data && !state.chat.pulse.loading) loadFleetPulse();
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
        // I7: add-member select (agents not already in the channel) + per-member remove chips.
        const candidates = (state.agents || []).map((a) => a.id).filter((aid) => aid && aid !== 'dashboard' && !members.includes(aid));
        const addControl = candidates.length
          ? `<select id="chat-add-member-${esc(id)}" class="chat-add-member"><option value="">+ Add member…</option>${candidates.map((aid) => `<option value="${esc(aid)}">${esc(aid)}</option>`).join('')}</select><button class="ghost" data-channel-add-member="${esc(id)}">Add</button>`
          : '';
        actions.innerHTML = `<span class="chat-members" title="${esc(members.join(', '))}">${count} member${count === 1 ? '' : 's'}</span>`
          + (isMember
            ? `<button class="ghost" data-chat-channel-action="leave" data-channel="${esc(id)}">Leave</button>`
            : `<button class="ghost" data-chat-channel-action="join" data-channel="${esc(id)}">Join</button>`)
          + `<button class="ghost" data-chat-channel-action="read" data-channel="${esc(id)}">Mark read</button>`
          + `<button class="ghost danger" data-chat-channel-action="delete" data-channel="${esc(id)}" title="Delete this channel">Delete</button>`
          + addControl;
        // Member chips with remove buttons below the action row.
        if (members.length) {
          actions.innerHTML += `<div class="chat-member-chips">${members.map((mbr) => `<span class="chat-member-chip">${esc(mbr)}<button data-channel-remove-member="${esc(id)}" data-member="${esc(mbr)}" aria-label="Remove ${esc(mbr)}" title="Remove ${esc(mbr)}">✕</button></span>`).join('')}</div>`;
        }
      } else {
        // Messenger | Console segmented toggle — inline terminal access without leaving Chat.
        const view = state.chat.view === 'console' ? 'console' : 'messenger';
        const toggle = `<span class="chat-view-toggle" role="group" aria-label="Conversation view">`
          + `<button class="seg${view === 'messenger' ? ' active' : ''}" data-chat-view="messenger" aria-pressed="${view === 'messenger'}">Messenger</button>`
          + `<button class="seg${view === 'console' ? ' active' : ''}" data-chat-view="console" aria-pressed="${view === 'console'}" title="Open ${esc(id)}'s live terminal inline">Console</button>`
          + `</span>`;
        actions.innerHTML = toggle
          + `<button class="ghost" data-mark-conv-read="${esc(id)}" title="Mark all messages from ${esc(id)} read">Mark all read</button>`
          + `<button class="ghost" data-agent-drawer="${esc(id)}">Details</button>`
          + `<button class="ghost" data-chat-analytics="${esc(id)}">Analytics</button>`;
      }
    }
    // Console view (DMs only): render the agent's live terminal inline instead of the message
    // timeline. Guard against poll re-renders re-mounting the xterm — only (re)build the host
    // when it's missing or points at a different agent, so the terminal stays stable.
    if (!isChannel && state.chat.view === 'console') {
      let host = byId('chat-console-host');
      if (!host || host.dataset.agent !== id) {
        timeline.innerHTML = `<div id="chat-console-host" class="chat-console-host" data-agent="${esc(id)}"></div>`;
        host = byId('chat-console-host');
      }
      const composer = byId('chat-composer'); if (composer) composer.hidden = true;
      const search = byId('chat-msg-search'); if (search) search.hidden = true;
      // Always hand off to the mounter — it's signature-guarded, so it only rebuilds when the
      // resolved console actually changed (e.g. a freshly-started console). No poll-flicker.
      if (mountChatConsole && host) mountChatConsole(id, host);
      return;
    }
    // Chat timelines render OLDEST→NEWEST (newest at the bottom) — every scroll
    // behavior here assumes it: follow-bottom (line ~398), the post-send auto-scroll
    // (`scrollTop = scrollHeight`), and the "scroll to newest" button all target the
    // bottom. But DM rows come from `/messages/recent`, which is DESCENDING (newest
    // first), so without this sort the just-sent message landed at the TOP and the
    // auto-scroll yanked the operator down to 2-day-old scrollback → "my message
    // never appears" (2026-07-06). Sort ascending by timestamp so newest is last;
    // idempotent for channel rows (already roughly ordered), and stable across polls.
    const allMsgs = sortChronological(isChannel
      ? (state.chat.channelMessages?.[id] || [])
      : dmMessages(state.messages, id, state.chat.identity));
    // Per-message search within the open conversation (WS-H2).
    const msgFilter = String(state.chat.msgFilter || '').trim().toLowerCase();
    const search = byId('chat-msg-search');
    if (search) { search.hidden = false; if (document.activeElement !== search && search.value !== (state.chat.msgFilter || '')) search.value = state.chat.msgFilter || ''; }
    const msgs = msgFilter
      ? allMsgs.filter((m) => `${m.from || ''} ${m.subject || ''} ${m.body || m.preview || ''}`.toLowerCase().includes(msgFilter))
      : allMsgs;
    // Follow-bottom: on a fresh open/just-sent (forceScrollBottom) always pin to the newest;
    // otherwise only auto-scroll if the operator was already near the bottom — don't yank them
    // down while they're reading scrollback.
    const nearBottom = (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight) < 80;
    const pinBottom = forceScrollBottom;
    forceScrollBottom = false; // one-shot: consumed by this render
    const searchBanner = msgFilter ? `<p class="chat-search-banner">${msgs.length} of ${allMsgs.length} message${allMsgs.length === 1 ? '' : 's'} match “${esc(msgFilter)}”</p>` : '';
    timeline.innerHTML = allMsgs.length
      ? searchBanner + (msgs.length ? msgs.map((m) => messageHtml(m, state.chat.identity, isChannel)).join('') : '<p class="chat-search-banner">No messages match.</p>')
      : '<div class="empty-state"><span class="empty-icon">✉️</span><strong>No messages yet</strong><p>Send the first message below to start this conversation.</p></div>';
    if (pinBottom || (nearBottom && !msgFilter)) {
      timeline.scrollTop = timeline.scrollHeight;
      // A forced pin (open/send) must land EXACTLY at the bottom. Setting scrollTop right after
      // innerHTML can fall short once late reflow (font metrics, wrapped rows) grows scrollHeight —
      // landing >80px short drops us out of the follow-bottom band, so subsequent polls no longer
      // track and the newest message stays half-hidden. Re-pin after layout settles.
      if (pinBottom && typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => { timeline.scrollTop = timeline.scrollHeight; });
      }
    }
    // Scroll-to-newest button: wire its scroll listener + click once, and refresh visibility now.
    const scrollBtn = byId('chat-scroll-bottom');
    if (scrollBtn) {
      const atBottom = () => (timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight) < 80;
      const sync = () => scrollBtn.classList.toggle('hidden', atBottom());
      if (!scrollBtn.dataset.wired) {
        scrollBtn.dataset.wired = '1';
        timeline.addEventListener('scroll', sync, { passive: true });
        scrollBtn.addEventListener('click', () => { timeline.scrollTo({ top: timeline.scrollHeight, behavior: 'smooth' }); });
      }
      sync();
    }
    const composer = byId('chat-composer');
    if (composer) composer.hidden = false;
    // Type/Priority/Subject only apply to DMs — channel posts are plain {from, body}, so hide the
    // meta row on channels rather than show inert controls.
    const composerMeta = composer?.querySelector('.composer-meta');
    if (composerMeta) composerMeta.hidden = isChannel;
    // "Expects reply" is a DM-dispatch concept — inert for channel broadcasts, so hide it there too.
    const expectsRow = composer?.querySelector('#chat-expects-reply')?.closest('.check-row');
    if (expectsRow) expectsRow.hidden = isChannel;
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
      // Draft preservation: restore THIS conversation's draft — and CLEAR the box when it has
      // none. Leaving the previous conversation's text in place risked sending it to the wrong
      // peer on Enter (review finding #3: draft leak → misdirected send).
      const draft = state.chat.drafts?.[key] ?? '';
      if (placeholder.value !== draft && document.activeElement !== placeholder) placeholder.value = draft;
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
    const isChannel = key.startsWith('channel:');
    // Per-conversation message search must not leak into the next conversation.
    if (key !== state.chat.selected) state.chat.msgFilter = '';
    // Sticky view across conversation switches (operator request): switching chats keeps the
    // current mode for the NEW chat instead of snapping back to Messenger.
    //  - Analytics: if viewing a per-agent Analytics panel and switching to a DIFFERENT agent,
    //    follow analytics to that agent. Rail keys are "dm:<id>" — strip the prefix, since
    //    openAnalytics() wants the raw agent id (passing "dm:id" loaded empty/all-zero analytics).
    //    Re-opening the SAME agent (the "Back to chat" button → open dm:<currentAnalyticsAgent>)
    //    falls through to clear analytics and show messages.
    const dmAgentId = key.startsWith('dm:') ? key.slice('dm:'.length) : key;
    if (state.chat.analytics.agent && !isChannel && dmAgentId !== state.chat.analytics.agent) {
      state.chat.selected = key;
      return openAnalytics(dmAgentId);
    }
    state.chat.analytics = { agent: '', data: null }; // leaving analytics view
    //  - Console/Messenger: keep state.chat.view as-is (no reset to 'messenger'). Console only
    //    renders for agent DMs, so a channel naturally shows messages even when 'console' sticks.
    state.chat.selected = key;
    forceScrollBottom = true; // opening a conversation lands on the newest message (bottom)
    if (isChannel) {
      const name = key.slice('channel:'.length);
      try { await loadConversation(name); } catch (_) { /* toast handled upstream */ }
    }
    render();
  }

  // Re-clicking the open conversation closes it back to the Fleet pulse view.
  function close() {
    state.chat.selected = '';
    state.chat.analytics = { agent: '', data: null };
    state.chat.pulse.data = null; // force a fresh pulse fetch on return
    render();
  }

  // Fetch the window-scoped fleet pulse for the landing dashboard.
  async function loadFleetPulse(force = false) {
    if (!loadPulse) return;
    if (state.chat.pulse.loading) return; // in-flight guard — WS bursts must not stack fetches
    if (!force && state.chat.pulse.lastMs && (Date.now() - state.chat.pulse.lastMs) < 12000) return;
    const w = state.chat.pulse.window;
    state.chat.pulse.loading = true;
    try {
      const d = await loadPulse(w);
      if (state.chat.pulse.window === w) state.chat.pulse.data = d;
      // Single source of truth (2026-07-02 screenshot incident): the pulse board carries
      // freshly-derived per-agent statuses while the rail renders state.agents from an
      // OLDER /agents poll — around a status flip the two views disagreed in the same
      // frame (rail dot online vs pulse row "working now"). Patch the shared roster from
      // the pulse payload so both repaint consistently; the next /agents poll agrees.
      if (Array.isArray(d?.agents) && Array.isArray(state.agents)) {
        for (const p of d.agents) {
          const agent = state.agents.find((a) => a.id === p.id);
          if (agent && p.status && agent.status !== p.status) agent.status = p.status;
        }
        renderRail();
      }
    } catch (_) {
      state.chat.pulse.data = { ok: false };
    }
    state.chat.pulse.lastMs = Date.now();
    state.chat.pulse.loading = false;
    // Only repaint if we're still on the pulse view (no conversation / analytics open).
    if (!state.chat.selected && !state.chat.analytics.agent) renderConversation();
  }

  // Refetch the pulse without blanking the current numbers (window change / poll tick).
  // Repaint first so the window selector reflects the new selection immediately.
  function refreshPulse(force = false) {
    if (!state.chat.selected && !state.chat.analytics.agent) renderConversation();
    loadFleetPulse(force);
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
    // Composer meta (restored regression): explicit message type, priority, and subject.
    const type = byId('chat-type')?.value || 'info';
    const priority = byId('chat-priority')?.value || 'normal';
    const subjectEl = byId('chat-subject');
    const subject = (subjectEl?.value || '').trim();
    const reply = state.chat.replyTo;
    const inReplyTo = (reply && reply.conversationKey === key) ? reply.id : '';
    try {
      const response = await sendMessage({
        isChannel, target: id, identity: state.chat.identity, body,
        expectsReply: !!expectsReply, queueIfBusy: !!queueIfBusy, inReplyTo,
        type, priority, subject,
      });
      if (bodyEl) bodyEl.value = '';
      if (subjectEl) subjectEl.value = '';
      // Sent cleanly: drop the saved draft + reply context for this conversation.
      if (state.chat.drafts) delete state.chat.drafts[key];
      persistDrafts?.();
      state.chat.replyTo = null;
      if (!isChannel) {
        const t = deliveryToastFor(response, id);
        toast(t.text, t.tone);
      } else {
        toast(`Posted to #${id}`, 'ok');
      }
      await refresh();
      if (isChannel) { try { await loadConversation(id); } catch (_) {} }
      forceScrollBottom = true; // reveal the just-sent message at the bottom
      render();
    } catch (error) {
      toast(`Send failed: ${error?.message || error}`, 'error');
    }
  }

  return { render, open, close, openAnalytics, send, renderRail, renderConversation, refreshPulse };
}
