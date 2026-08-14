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
