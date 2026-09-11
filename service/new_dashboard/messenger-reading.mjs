// Receipts belong to the visible Messenger viewport, never to conversation selection.
import { dmMessages, sortChronological } from './chat-select.mjs';
export function createMessengerReading({ state, byId, history, render, markVisibleRead }) {
  let entry = '', generation = 0, scheduled = false, jumping = false, notice = '', failedEntry = '';
  const conversationKey = () => `${state.chat.identity}:${state.chat.selected}`;
  const failureNotice = 'Could not load older messages. Automatic read receipts are paused. Retry the oldest-unread search.';
  const pending = new Set();
  const doc = () => globalThis.document;
  const frame = () => new Promise(resolve => requestAnimationFrame(resolve));
  const rows = () => dmMessages(history ? history.combined(state.messages) : state.messages, String(state.chat.selected).slice(3), state.chat.identity);
  function visible() {
    const timeline = byId('chat-timeline');
    if (!timeline?.getBoundingClientRect || !doc()?.hasFocus?.() || doc().visibilityState !== 'visible') return false;
    if (!String(state.chat.selected || '').startsWith('dm:') || state.chat.identity === 'all' || state.chat.view === 'console' || state.chat.analytics?.agent) return false;
    for (let el = timeline; el; el = el.parentElement) {
      const style = getComputedStyle(el);
      if (el.hidden || style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) return false;
    }
    const r = timeline.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight;
  }
  const unread = m => m.read === false && String(m.to || m.targetAgentId || m.target_agent_id || '') === state.chat.identity;
  async function acknowledge() {
    if (!visible() || state.chat.peek || jumping || failedEntry === conversationKey() || history?.loading || !markVisibleRead) return;
    const timeline = byId('chat-timeline'), box = timeline.getBoundingClientRect();
    const candidates = new Map(rows().filter(unread).map(m => [String(m.id || m.messageId), m]));
    const messages = [...timeline.querySelectorAll('article[data-kind="message"]')].flatMap(el => {
      const m = candidates.get(el.dataset.id), r = el.getBoundingClientRect();
      const key = `${state.chat.identity}:${el.dataset.id}`;
      return m && !pending.has(key) && r.width > 0 && r.height > 0
        && r.bottom > Math.max(box.top, 0) && r.top < Math.min(box.bottom, innerHeight)
        && r.right > Math.max(box.left, 0) && r.left < Math.min(box.right, innerWidth) ? [m] : [];
    });
    if (!messages.length) return;
    const identity = state.chat.identity;
    const keys = messages.map(m => `${identity}:${m.id || m.messageId}`);
    keys.forEach(k => pending.add(k));
    try {
      const ok = await markVisibleRead(messages, identity);
      if (ok === false) return;
      messages.forEach(m => { m.read = true; });
      // A successful receipt changes data once. The next render cannot re-acknowledge it.
      render();
    } catch { /* A later visibility/scroll/render event retries; no hot retry loop. */ }
    finally { keys.forEach(k => pending.delete(k)); }
  }
  async function enter(token) {
    jumping = !!state.chat.jumpUnread && !state.chat.msgFilter;
    try {
      if (jumping) {
        // The recent window cannot prove the oldest unread. Page to the beginning, bounded
        // so a large fleet cannot turn opening Messenger into an endless request chain.
        let pages = 0;
        while (history && !history.exhausted && pages < 20) {
          if (token !== generation || !visible()) return;
          if (history.loading) { await frame(); continue; }
          const added = await history.loadOlder(state.messages); pages++;
          if (!added) break;
        }
        if (token !== generation || !visible()) return;
        render();
        await frame(); await frame();
        if (token !== generation || !visible()) return;
        const first = sortChronological(rows()).find(unread);
        const timeline = byId('chat-timeline');
        if (history && !history.complete) {
          notice = 'Jump limited to loaded history. Scroll up to load older messages.';
          render();
        }
        const target = first && [...timeline.querySelectorAll('article[data-kind="message"]')].find(el => el.dataset.id === String(first.id || first.messageId));
        if (target) timeline.scrollTop += target.getBoundingClientRect().top - timeline.getBoundingClientRect().top;
        // Keep the automatic jump's scroll event from starting an extra history page.
        await frame();
      }
    } catch {
      if (token === generation) {
        failedEntry = conversationKey();
        notice = failureNotice;
        render();
      }
      return;
    }
    finally { if (token === generation) jumping = false; }
    await frame();
    if (token === generation) acknowledge();
  }
  function update() {
    if (!doc()?.addEventListener || typeof requestAnimationFrame !== 'function') return;
    if (failedEntry && failedEntry !== conversationKey()) { failedEntry = ''; notice = ''; }
    const key = visible() ? conversationKey() : '';
    if (key !== entry) {
      entry = key; generation++; jumping = false; notice = failedEntry ? failureNotice : '';
      if (key && !failedEntry) { enter(generation); return; }
    }
    if (!key || scheduled || jumping || failedEntry) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; acknowledge(); });
  }
  // One controller owns one persistent timeline and these listeners.
  byId('chat-timeline')?.addEventListener?.('click', event => {
    if (!event.target.closest?.('[data-messenger-retry]') || !visible() || failedEntry !== conversationKey()) return;
    failedEntry = ''; notice = '';
    enter(++generation);
    render();
  });
  byId('chat-timeline')?.addEventListener?.('scroll', update, { passive: true });
  doc()?.addEventListener?.('visibilitychange', update);
  globalThis.addEventListener?.('focus', update);
  globalThis.addEventListener?.('blur', update);
  return { update, get jumping() { return jumping; }, get failed() { return !!failedEntry; }, get notice() { return notice; }, visible };
}
