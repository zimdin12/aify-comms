// The global keyboard shortcuts, moved out of app.js in v0.5.4.
//
// This was a whole top-level `document.addEventListener('keydown', …)` — not a branch of the click
// handler — and it is the largest single statement extracted so far. Three of its four rules are
// ACCESSIBILITY: Escape dismisses overlays, and Enter/Space operate the status-why popover and the
// favourite star, which are `role=button` spans and therefore have no native key handling at all. A
// keyboard-only operator loses those controls entirely if any of this stops firing, and nothing on
// screen looks wrong.
//
// `closeInspector` and `toggleFavorite` are INJECTED — they stay in app.js. The body is byte-identical
// to the listener it left, at the same indentation, so nothing about the rules changed.

import { copyActiveConsole } from './clipboard.mjs';
import { state } from './state.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { byId } from './ui.js';

export function handleGlobalKeydown(event, closeInspector, toggleFavorite) {
  if (event.key === 'Escape') {
    closeStatusWhy();
    // Escape also dismisses the inspector/agent drawer when it's open and focus isn't in a field.
    if (byId('inspector')?.classList.contains('open') && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')) closeInspector();
  }
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-status-why]')) {
    event.preventDefault();
    openStatusWhy(event.target);
  }
  // Keyboard-operable favorite star (role=button span) — WS-L a11y.
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-fav-toggle]')) {
    event.preventDefault();
    toggleFavorite(event.target.dataset.favToggle);
  }
  // Ctrl+Shift+C copies the console when it has a selection (xterm swallows plain Ctrl+C as
  // SIGINT into the PTY, so the copy shortcut is shifted — parity with the old dashboard).
  if (event.ctrlKey && event.shiftKey && (event.key === 'C' || event.key === 'c') && state.activeXterm?.term) {
    event.preventDefault();
    copyActiveConsole();
  }
}
