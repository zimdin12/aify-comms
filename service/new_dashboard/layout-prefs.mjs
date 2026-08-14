// Layout preferences the operator sets by clicking, persisted to localStorage. Moved out of app.js in
// v0.5.4.
//
// All three answer the same question — what should the page look like when this operator opens it
// again — and all three fail the same quiet way: the click works, the layout changes, and the choice is
// gone on the next reload. Nothing on screen distinguishes that from working.

import { byId } from './ui.js';

export function setNavCollapsed(collapsed) {
  const shell = byId('app-shell');
  shell?.classList.toggle('nav-collapsed', Boolean(collapsed));
  byId('toggle-nav')?.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
  byId('toggle-nav')?.setAttribute('title', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  localStorage.setItem('aify.next.navCollapsed', collapsed ? '1' : '0');
}

export function preferredNavCollapsed() {
  const stored = localStorage.getItem('aify.next.navCollapsed');
  if (stored) return stored === '1';
  return window.matchMedia('(max-width: 760px)').matches;
}

export function toggleSessionGroupCollapsed(envId, collapsed) {
  try {
    const set = new Set(JSON.parse(localStorage.getItem('aifyCollapsedSessionGroups') || '[]') || []);
    if (collapsed) set.add(envId); else set.delete(envId);
    localStorage.setItem('aifyCollapsedSessionGroups', JSON.stringify([...set]));
  } catch { /* ignore */ }
}
