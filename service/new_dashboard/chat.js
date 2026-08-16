// Chat-first landing (DASHBOARD_REBUILD_PLAN §3.1): a conversation rail (DMs + channels)
// with presence dots, unread, favorites, identity switcher, and search; a threaded timeline
// with read/unread + wake-vs-stored badges; and a composer with an "expects reply" toggle,
// queue option, and the delivery-truthfulness toast ladder. Pure rail/timeline builders are
// exported for unit testing; the page wires app state + send via createChatController().
import { esc } from './util.js';

import { toast } from './ui.js';
import { fleetPulseHtml } from './analytics.js';
// The conversation-list builders left for `chat-select.mjs` in v0.5.4, with the HTML builders that
// went to `chat-render.mjs` before them. What remains here is the controller — the part that needs
// a document — and it is the only caller of both.
import { chatConversationItems, dmMessages, sortChronological } from './chat-select.mjs';
// The pure HTML builders left for `chat-render.mjs` in v0.5.4 — data in, string out, no app state and
// no DOM. The controller below is their only caller here; `chat.test.mjs` imports them from their new
// owner rather than through this module, so nothing re-exports them.
import {
  deliveryToastFor,
  messageHtml,
  railItemHtml,
  renderAnalyticsPanelHtml,
} from './chat-render.mjs';

// Build the controller that renders the page and wires send. deps: { state, byId, sendMessage,
// refresh, loadConversation, loadAgentAnalytics, ... } (channel loading is driven from app.js).
export function createChatController(deps) {
  const { state, byId, sendMessage, refresh, loadConversation, loadAgentAnalytics, mountChatConsole, loadPulse, persistDrafts } = deps;
  // Answering a peer marks their messages read (see send()). Defaulted to a no-op so the unit tests
  // can construct the controller without stubbing the read API.
  const markConversationRead = typeof deps.markConversationRead === 'function'
    ? deps.markConversationRead
    : async () => {};
  // Optional hook fired whenever the selected conversation CHANGES, so the page can keep
  // selection-dependent UI (the agent details drawer) in step. Defaulted to a no-op so the
  // unit tests can construct the controller without it.
  const onSelectionChange = typeof deps.onSelectionChange === 'function' ? deps.onSelectionChange : () => {};

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
      + (dmItems.length ? dmItems.map((i) => railItemHtml(i, state.chat.selected, state.chat.drafts, state.chat.identity === 'all')).join('') : `<p class="subtle chat-rail-empty">${dmEmpty}</p>`)
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
    const readOnly = state.chat.identity === 'all';
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
        const candidates = readOnly ? [] : (state.agents || []).map((a) => a.id).filter((aid) => aid && aid !== 'dashboard' && !members.includes(aid));
        const addControl = candidates.length
          ? `<select id="chat-add-member-${esc(id)}" class="chat-add-member"><option value="">+ Add member…</option>${candidates.map((aid) => `<option value="${esc(aid)}">${esc(aid)}</option>`).join('')}</select><button class="ghost" data-channel-add-member="${esc(id)}">Add</button>`
          : '';
        actions.innerHTML = `<span class="chat-members" title="${esc(members.join(', '))}">${count} member${count === 1 ? '' : 's'}</span>`
          + (readOnly ? '' : (isMember
            ? `<button class="ghost" data-chat-channel-action="leave" data-channel="${esc(id)}">Leave</button>`
            : `<button class="ghost" data-chat-channel-action="join" data-channel="${esc(id)}">Join</button>`))
          + (readOnly ? '' : `<button class="ghost" data-chat-channel-action="read" data-channel="${esc(id)}">Mark read</button>`
          + `<button class="ghost danger" data-chat-channel-action="delete" data-channel="${esc(id)}" title="Delete this channel">Delete</button>`)
          + addControl;
        // Member chips with remove buttons below the action row.
        if (members.length) {
          actions.innerHTML += `<div class="chat-member-chips">${members.map((mbr) => `<span class="chat-member-chip">${esc(mbr)}${readOnly ? '' : `<button data-channel-remove-member="${esc(id)}" data-member="${esc(mbr)}" aria-label="Remove ${esc(mbr)}" title="Remove ${esc(mbr)}">✕</button>`}</span>`).join('')}</div>`;
        }
      } else {
        // Messenger | Console segmented toggle — inline terminal access without leaving Chat.
        const view = !readOnly && state.chat.view === 'console' ? 'console' : 'messenger';
        const toggle = readOnly ? '' : `<span class="chat-view-toggle" role="group" aria-label="Conversation view">`
          + `<button class="seg${view === 'messenger' ? ' active' : ''}" data-chat-view="messenger" aria-pressed="${view === 'messenger'}">Messenger</button>`
          + `<button class="seg${view === 'console' ? ' active' : ''}" data-chat-view="console" aria-pressed="${view === 'console'}" title="Open ${esc(id)}'s live terminal inline">Console</button>`
          + `</span>`;
        actions.innerHTML = toggle
          + (readOnly ? '' : `<button class="ghost" data-mark-conv-read="${esc(id)}" title="Mark all messages from ${esc(id)} read">Mark all read</button>`)
          + `<button class="ghost" data-agent-drawer="${esc(id)}">Details</button>`
          + `<button class="ghost" data-chat-analytics="${esc(id)}">Analytics</button>`;
      }
    }
    // Console view (DMs only): render the agent's live terminal inline instead of the message
    // timeline. Guard against poll re-renders re-mounting the xterm — only (re)build the host
    // when it's missing or points at a different agent, so the terminal stays stable.
    if (!isChannel && !readOnly && state.chat.view === 'console') {
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
    if (composer) composer.hidden = state.chat.identity === 'all';
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
    const ids = ['dashboard', 'all', ...(state.agents || []).map((a) => a.id).filter((id) => id && id !== 'dashboard').sort()];
    const html = ids.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join('');
    if (sel.dataset.optionSig !== html) { sel.innerHTML = html; sel.dataset.optionSig = html; }
    if (sel.value !== state.chat.identity) sel.value = state.chat.identity;
    const newChannel = byId('chat-new-channel-form');
    if (newChannel) newChannel.hidden = state.chat.identity === 'all';
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
      onSelectionChange();
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
    onSelectionChange();
  }

  // Re-clicking the open conversation closes it back to the Fleet pulse view.
  function close() {
    state.chat.selected = '';
    state.chat.analytics = { agent: '', data: null };
    state.chat.pulse.data = null; // force a fresh pulse fetch on return
    render();
    onSelectionChange();
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

  // `queue` is EXPLICIT and per-message: only the Queue half of the split Send button passes it.
  // Enter and Send never queue. The old `#chat-queue` checkbox was sticky and hidden inside the
  // collapsed Options disclosure, so one tick silently queued every subsequent message — reported
  // 2026-07-27 as "what does ordinary pressing enter do? ... message was queued". Removed rather
  // than surfaced: a per-send choice does not want a persistent mode.
  async function send({ queue = false } = {}) {
    const bodyEl = byId('chat-composer-body');
    const body = (bodyEl?.value || '').trim();
    const key = state.chat.selected;
    if (!body || !key) return;
    const isChannel = key.startsWith('channel:');
    const id = key.slice(key.indexOf(':') + 1);
    const expectsReply = byId('chat-expects-reply')?.checked;
    const queueIfBusy = !!queue;
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
      // Answering a peer IS reading them (operator report 2026-07-27: "if i write to you then it
      // should disappear"). Their unread badge previously survived a reply and could only be cleared
      // by the explicit Mark-all-read button, so a conversation you had just answered still shouted
      // for attention. Quiet on purpose — the send already produced a delivery toast, and a second
      // "marked N read" toast for something the operator did implicitly is noise.
      //
      // Deliberately NOT clearing on scroll: the operator flagged that as too aggressive, and it is
      // — scrolling past a message is not evidence anyone read it, which is the same "state that
      // lies" trap the rest of this project keeps closing. A reply IS evidence.
      //
      // DMs only: channel read state is per-membership (`/channels/{name}/read`), a different
      // contract, and posting to a channel is not the same act as reading its backlog.
      if (!isChannel) {
        try { await markConversationRead(id, { quiet: true }); } catch (_) { /* never block a sent message */ }
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
