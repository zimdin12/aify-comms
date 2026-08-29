// Message and channel actions: read/unread, unsend, favourite, the channel lifecycle, and the chat
// console mount.
//
// Several of these are OPTIMISTIC — they change what the operator sees before the server has agreed,
// because a read receipt or a favourite that waits for a round trip feels broken. That makes the
// REVERT the interesting behaviour: a failed request must put back exactly what was there, or the
// dashboard quietly disagrees with the server until the next poll and the operator acts on the
// difference.
//
// Three injected names, each of which reaches `refresh`.

import { sessionForAgent } from './agent-drawer.mjs';
import { openAgentChat } from './agent-session-actions.mjs';
import { api } from './api-client.mjs';
import { chatLoadChannels } from './message-transport.mjs';
import { messageId, messageIdOf, sessionId } from './record-fields.mjs';
import { agentForSession } from './session-rail.mjs';
import { state } from './state.mjs';
import { byId, toast, uiConfirm } from './ui.js';
import { esc } from './util.js';
import { disposeActiveXterm } from './xterm-lifecycle.mjs';

let chatController = { render() {}, renderRail() {}, renderConversation() {} };
let refreshSoon = () => {};
let renderSessionConsole = () => {};

/** Supply the app.js-side dependencies. Throws on a partial bag. */
export function initMessageActions(deps) {
  const REQUIRED = ['chatController', 'refreshSoon', 'renderSessionConsole'];
  const missing = REQUIRED.filter((k) => deps == null || deps[k] == null);
  if (missing.length) throw new TypeError(`initMessageActions requires ${missing.join(', ')}`);
  ({ chatController, refreshSoon, renderSessionConsole } = deps);
}


// WS-I1/I2: per-message read/unread, unsend, and mark-conversation-read. The recipient for a
// read toggle is the viewing identity (POST /messages/{id}/read {agentId, read}).
export async function markMessageRead(msgId, read) {
  try {
    await api(`/messages/${encodeURIComponent(msgId)}/read`, { method: 'POST', body: JSON.stringify({ agentId: state.chat.identity, read }) });
    const m = state.messages.find((x) => messageIdOf(x) === msgId);
    if (m) m.read = read;
    chatController.render();
  } catch (err) { toast(`Read update failed: ${err?.message || err}`, 'error'); }
}

export async function unsendMessage(messageId) {
  if (!await uiConfirm('Unsend this message? It will be removed for the recipient.', { tone: 'danger' })) return;
  try {
    // `requestedBy` is mandatory since H4 (2026-08-18) — the endpoint refuses an actor-less
    // delete. The dashboard is an operator surface, so it may unsend a message it did not write.
    await api(`/messages/${encodeURIComponent(messageId)}?requestedBy=dashboard`, { method: 'DELETE' });
    state.messages = state.messages.filter((m) => messageIdOf(m) !== messageId);
    toast('Message unsent', 'ok');
    chatController.render();
    refreshSoon();
  } catch (err) { toast(`Unsend failed: ${err?.message || err}`, 'error'); }
}

export async function markConversationRead(agentId, { quiet = false } = {}) {
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

// Favorites (WS-F): PATCH /agents/{id}/favorite, optimistic so the rail re-sorts immediately.
export async function toggleFavorite(agentId) {
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

// Mount an agent's live console inline inside the Chat conversation pane. Reuses the exact
// Sessions terminal widget (PTY xterm / hermes iframe / codex synth / start-console offer).
// Signature-guarded: called on every render while Console is open, but only rebuilds the host
// when the resolved console actually changed — so a freshly-started console auto-appears while
// idle polls don't remount (and flicker) the live xterm.
export function mountChatConsole(agentId, hostEl) {
  if (!hostEl) return;
  const session = sessionForAgent(agentId);
  const sig = session
    ? [sessionId(session), session.status || '', session.terminalStatus || '',
       agentForSession(session)?.runtimeState?.virtualTerminalId || '',
       // Include the auto-attach sources (2026-06-19 review) so a terminal that first goes live
       // via the top-level PTY / console pointer / session-bound id changes the sig and mounts
       // inline immediately, instead of lagging a poll until it lands in state.sessionTerminals.
       agentForSession(session)?.runtimeState?.terminalId || '',
       agentForSession(session)?.runtimeState?.consoleTerminal?.terminalId || '',
       session.terminalId || session.terminal?.id || '',
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

export async function chatChannelAction(action, name) {
  const identity = state.chat.identity;
  try {
    if (action === 'delete') {
      if (!await uiConfirm(`Delete channel #${name}? This removes the channel and its membership for everyone.`, { tone: 'danger' })) return;
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
export async function addChannelMember(name) {
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

export async function removeChannelMember(name, agentId) {
  if (!await uiConfirm(`Remove ${agentId} from #${name}? They stop receiving fan-out; history remains.`, { tone: 'danger', confirmLabel: 'Remove' })) return;
  try {
    await api(`/channels/${encodeURIComponent(name)}/leave`, { method: 'POST', body: JSON.stringify({ agentId }) });
    await chatLoadChannels();
    chatController.render();
    toast(`${agentId} removed from #${name}`, 'ok');
  } catch (err) { toast(`Remove member failed: ${err?.message || err}`, 'error'); }
}

// WS-J: open a message's thread in the real Chat page (not the removed Sessions composer).
export function openMessageThread(messageIdValue) {
  const message = state.messages.find((item) => messageId(item) === String(messageIdValue));
  if (!message) return;
  const agentId = message.from === 'dashboard' ? message.to : message.from;
  openAgentChat(agentId);
}
