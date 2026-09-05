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
import { toast } from './ui.js';

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

/**
 * Put the saved draft back into the composer for the selected conversation.
 *
 * THE HALF THAT WAS MISSING, and its absence was worse than never saving at all. Every keystroke was
 * written to `state.chat.drafts` and mirrored to localStorage; the rail rendered a badge reading
 * "Half-written message saved for this chat"; a successful send deleted the entry. Nothing ever
 * assigned the text back to the textarea -- there was no `chat-composer-body.value =` anywhere in the
 * dashboard. So the page PROMISED the message was safe and then never returned it.
 *
 * OPERATOR-REPORTED, and the path that makes it bite: a 401 mounts the API-key prompt, whose accepted
 * handler calls `location.reload()`. A reload the operator did not ask for then destroyed whatever
 * they had typed, which is why the workaround was "copy the text, refresh, paste it back".
 *
 * CALLED ON SELECTION, not on every render. A render can happen mid-typing -- the poll repaints the
 * rail every cycle -- and assigning `value` then would move the caret to the end of the operator's
 * own sentence, or overwrite a keystroke that had not yet reached `state`. Switching conversation is
 * the one moment the box is meant to change under them.
 */
export function restoreChatDraft(doc) {
  // Resolved INSIDE the body, never as a default parameter: `no-module-scope-browser-globals` reads
  // the signature line as module scope, and it is right to -- a module that touches `document` while
  // it loads is as unimportable as the app.js this was extracted from.
  const target = doc || globalThis.document;
  const el = target?.getElementById?.('chat-composer-body');
  if (!el) return '';
  const key = state.chat.selected;
  const draft = key ? String((state.chat.drafts || {})[key] || '') : '';
  el.value = draft;
  return draft;
}

// The two chat-shell toggles, moved out of app.js's delegated click handler in v0.5.4. They belong here
// because every line of them already did: both flip a flag on `state.chat` and then call this module's own
// persist + chip-sync pair. app.js keeps each `closest()` guard and its `return;`.
export function toggleChatCompact() {
  state.chat.compact = !state.chat.compact;
  persistChatPrefs(); syncChatChips(); // syncChatChips toggles the .chat-shell.compact class
}

export function toggleChatPeek() {
  // Peek mode: watch conversations without auto-marking their messages read on open.
  state.chat.peek = !state.chat.peek;
  persistChatPrefs(); syncChatChips();
  toast(state.chat.peek ? 'Peek mode on — opening a chat won’t mark it read' : 'Peek mode off', 'ok');
}
