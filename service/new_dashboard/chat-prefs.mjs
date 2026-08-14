// The chat rail's preferences: what gets persisted, and how the chips reflect it.
//
// NOT merged into `chat.js`, deliberately. That module's own header says "pure rail/timeline builders are
// exported for unit testing" and it touches `document` three times in total; `syncChatChips` alone makes
// seven `querySelectorAll` calls. Joining an existing owner is the standing rule here, but not when it
// would contradict what that owner says it is.
//
// TWO SIDES OF ONE SUBJECT. `persistChatPrefs` writes the nine preference fields to localStorage;
// `syncChatChips` reads the same fields back onto the toggle chips. A preference added to one and not the
// other is silently half-implemented — it survives a reload but its chip shows the wrong state, or the chip
// works and the setting is forgotten. Keeping them adjacent is what makes that visible.
//
// The aria-pressed mirroring in `syncChatChips` is an accessibility contract, not decoration: the status
// dots carry no text, so without it their toggle state is conveyed by colour alone.
//
// Extracted from app.js in v0.5.4. The declarations are byte-identical to those that stood there; the only
// substitution is the added `export `.


import { state } from './state.mjs';

export function persistChatPrefs() {
  try {
    localStorage.setItem('aify.next.chatPrefs', JSON.stringify({
      liveOnly: state.chat.liveOnly, openOnly: state.chat.openOnly,
      workingUp: state.chat.workingUp, unreadOnly: state.chat.unreadOnly,
      scope: state.chat.scope, statusFilter: [...(state.chat.statusFilter || [])],
      sortMode: state.chat.sortMode, compact: state.chat.compact, peek: state.chat.peek,
    }));
  } catch { /* ignore */ }
}
export function syncChatChips() {
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

// Chat DRAFTS — the same subject as the preferences above: per-conversation state the rail restores on
// reload. Extracted from app.js in v0.5.4, joining the existing owner rather than starting a third
// chat-state module.

export function persistChatDrafts() {
  try {
    const d = state.chat.drafts || {};
    const pruned = {};
    for (const k of Object.keys(d)) { if (String(d[k] || '').trim()) pruned[k] = d[k]; }
    localStorage.setItem('aifyChatDrafts', JSON.stringify(pruned));
  } catch { /* ignore quota/serialization */ }
}
