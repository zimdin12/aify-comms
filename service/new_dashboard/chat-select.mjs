// Which conversations the chat rail shows, in what order, and which messages belong to each.
//
// Extracted from `chat.js` in v0.5.4, completing the split `chat-render.mjs` started: the pure
// half of that file is now two modules and `chat.js` is the controller alone. Closure measured
// with the repo's own `declarationSpan` before the move — these five need `resolveStatus` and
// `LIVE_AGENT_STATUSES` and nothing else `chat.js` declares.
//
// DATA IN, DATA OUT — no DOM, no app state, which is the line this whole split follows. These are
// the functions a test can drive by calling, and they were already the tested part of `chat.js`.
//
// THE SORT IS THREE KEYS AND THE ORDER MATTERS. Unread first, because an operator scanning the
// rail is looking for what needs them; then presence rank, so a live agent outranks a stopped one
// carrying older mail; then recency. Collapsing any pair of those is how a rail sorted 'by time'
// buries the one agent that is blocked and waiting.
//
// `STATUS_SORT_RANK` IS DERIVED FROM `LIVE_AGENT_STATUSES`, not a hand-copy of it. The dashboard
// has been bitten by an independent copy of that set before — the comment inside `chat.js` records
// it — and two lists that must agree will not.
import { resolveStatus, LIVE_AGENT_STATUSES, NON_LIVE_AGENT_STATUSES } from './status.js';

// Which of state.messages belong to a DM conversation with `agentId`, scoped to the viewing
// identity. (The server already scopes the dashboard inbox; this is the per-peer filter.)
export function dmMessages(messages, agentId, identity = 'dashboard') {
  const peer = String(agentId || '');
  const viewer = String(identity || 'dashboard');
  return (messages || []).filter((m) => {
    // Exclude channel-broadcast rows (bughunt 2026-07-03): /messages/recent returns
    // channel posts (source:'channel', to:null); without this guard a channel message
    // rendered inside a DM timeline and got DM-only controls (Reply/Mark-read/Unsend)
    // that misfire — Mark-read 403s on the NULL recipient, Unsend deletes the channel
    // post from the wrong surface, and the DM rail preview/count is polluted.
    if (String(m.source || '') === 'channel' || m.channel) return false;
    const from = String(m.from || '');
    const to = String(m.to || m.targetAgentId || m.target_agent_id || '');
    if (viewer === 'all') return from === peer || to === peer;
    return (from === peer && to === viewer) || (from === viewer && to === peer);
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
// The order the LIVE statuses float in. Busy and blocked first, because the point of the sort is to
// surface who needs something; the rest of the order is the vocabulary's, not this file's.
const LIVE_PRIORITY = ['working', 'blocked'];

// DERIVED, which is what the note at the top of this file always claimed it was and it was not: the
// rank was a hand-written map of seven names, and any status missing from it silently took the
// `unknown` rank. `starting` was missing, so a booting agent -- LIVE, and drawn with the working dot
// -- sorted BELOW offline and stopped ones in the rail whose job is to show who is doing something.
//
// Built so the failure cannot recur: whatever order the pieces arrive in, every live status ranks
// above every dead one, and a status added to status.js and forgotten here lands at the end of the
// live group rather than beneath the dead.
const STATUS_SORT_ORDER = [
  ...LIVE_PRIORITY.filter((s) => LIVE_AGENT_STATUSES.includes(s)),
  ...LIVE_AGENT_STATUSES.filter((s) => !LIVE_PRIORITY.includes(s)),
  ...NON_LIVE_AGENT_STATUSES,
];
const STATUS_SORT_RANK = Object.fromEntries(STATUS_SORT_ORDER.map((s, i) => [s, i]));

// An unrecognised kind sorts last -- below every declared status, rather than tied with a real one.
function statusRank(kind) { return STATUS_SORT_RANK[kind] ?? STATUS_SORT_ORDER.length; }

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
    if (String(m.source || '') === 'channel' || m.channel) continue;
    const from = String(m.from || '');
    const to = String(m.to || m.targetAgentId || m.target_agent_id || '');
    if (identity === 'all') {
      if (from) push(from, m);
      if (to && to !== from) push(to, m);
    } else if (from === identity && to) {
      push(to, m);
    } else if (to === identity && from) {
      push(from, m);
    }
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
        statusNote: a.statusNote || '',
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
  const live = new Set(LIVE_AGENT_STATUSES);  // H1: was an independent hand-copy of the same four
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
