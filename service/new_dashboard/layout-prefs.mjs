// Layout preferences the operator sets by clicking, persisted to localStorage. Moved out of app.js in
// v0.5.4.
//
// All three answer the same question — what should the page look like when this operator opens it
// again — and all three fail the same quiet way: the click works, the layout changes, and the choice is
// gone on the next reload. Nothing on screen distinguishes that from working.

import { byId } from './ui.js';

// localStorage THROWS rather than returning null when it is unavailable — private/incognito windows,
// and any browser where site data is blocked by policy. These two were the only readers of it in the
// whole dashboard boot path without a guard, which `boot-wiring.test.mjs` found by running the boot
// against a throwing storage. `toggleSessionGroupCollapsed` below has always had one.
//
// It mattered because of WHERE `preferredNavCollapsed` is called: near the end of
// `restorePersistedPreferences`, after `setPage('chat')` and `updateStaticLinks()`. So in a private
// window the page painted, and then the boot stopped — the Work-view restore and everything after it
// never ran, with no error visible to the operator.
//
// Both degrade the same way, which is the only sensible one: the layout still works, the preference
// just does not persist. That is exactly what a private window is for.

export function setNavCollapsed(collapsed) {
  const shell = byId('app-shell');
  shell?.classList.toggle('nav-collapsed', Boolean(collapsed));
  byId('toggle-nav')?.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
  byId('toggle-nav')?.setAttribute('title', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  // The DOM update above is deliberately NOT inside the try: the sidebar must collapse even when the
  // choice cannot be remembered.
  try { localStorage.setItem('aify.next.navCollapsed', collapsed ? '1' : '0'); } catch { /* unavailable */ }
}

//: The Needs-Attention strip, which had none of this.
//:
//: Its collapsed state existed ONLY as a CSS rotation on the toggle glyph:
//: `.attention-strip.collapsed .attention-collapse { transform: rotate(-90deg); }`. Read off the
//: live page 2026-08-25, the button carried no aria-expanded, no aria-pressed and no aria-controls,
//: and its title never changed -- so whether the strip was open or shut was legible to a sighted
//: mouse user and to nobody else. setNavCollapsed above had answered the same question correctly
//: since v0.5.4; the strip's toggle simply never learned it.
//:
//: aria-expanded rather than aria-pressed, because this is a disclosure -- it reveals a region -- and
//: aria-controls names the region it reveals. toggle-nav uses aria-pressed, which is right for it:
//: it changes the layout rather than disclosing a panel.
export function setAttentionCollapsed(collapsed) {
  const strip = byId('attention-strip');
  strip?.classList.toggle('collapsed', Boolean(collapsed));
  const button = byId('attention-collapse');
  button?.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  button?.setAttribute('aria-controls', 'attention-list');
  button?.setAttribute('title', collapsed ? 'Expand Needs Attention' : 'Collapse Needs Attention');
  // Outside the try for the same reason as setNavCollapsed: the strip must collapse even when the
  // choice cannot be remembered.
  try { localStorage.setItem('aify.next.attentionCollapsed', collapsed ? '1' : '0'); } catch { /* unavailable */ }
}

export function preferredAttentionCollapsed() {
  let stored = null;
  try { stored = localStorage.getItem('aify.next.attentionCollapsed'); } catch { /* unavailable */ }
  // Default COLLAPSED, preserving the operator's landing-page request: chat is the hero, and the
  // strip stays a slim banner until asked for. Only an explicit '0' opens it.
  return stored !== '0';
}

export function preferredNavCollapsed() {
  let stored = null;
  try { stored = localStorage.getItem('aify.next.navCollapsed'); } catch { /* unavailable */ }
  if (stored) return stored === '1';
  // No readable preference falls through to the viewport, which is the same answer an operator who has
  // never touched the toggle already gets.
  return window.matchMedia('(max-width: 760px)').matches;
}

export function toggleSessionGroupCollapsed(envId, collapsed) {
  try {
    const set = new Set(JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []);
    if (collapsed) set.add(envId); else set.delete(envId);
    localStorage.setItem('aifyCollapsedSessionGroups', JSON.stringify([...set]));
  } catch { /* ignore */ }
}
