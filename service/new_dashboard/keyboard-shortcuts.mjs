// The global keyboard shortcuts, moved out of app.js in v0.5.4.
//
// This was a whole top-level `document.addEventListener('keydown', …)` — not a branch of the click
// handler — and it is the largest single statement extracted so far. Three of its four rules are
// ACCESSIBILITY: Escape dismisses overlays, and Enter/Space operate every `role=button` span --
// the status-why popover, the favourite star and the triage tiles -- which have no native key
// handling at all. That list was TWO until 2026-09-01 and this comment enumerated it, which is
// exactly how the third went missing; the gate now derives it from the markup instead. A
// keyboard-only operator loses those controls entirely if any of this stops firing, and nothing on
// screen looks wrong.
//
// `closeInspector` and `toggleFavorite` are INJECTED — they stay in app.js. The body is byte-identical
// to the listener it left, at the same indentation, so nothing about the rules changed.

import { copyActiveConsole } from './clipboard.mjs';
import { state } from './state.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { jumpFromDiagnostic } from './work-loop-panels.mjs';
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
  // The triage tiles, for the same reason and missed for the same reason: this list is hand-kept,
  // the comment at the top of this file enumerates what it covers, and a third `role=button` span
  // shipped without anyone comparing the two lists. `every-role-button-is-keyboard-operable.test.mjs`
  // now DERIVES the population from the markup so a fourth cannot arrive unnoticed.
  if ((event.key === 'Enter' || event.key === ' ') && event.target?.matches?.('[data-diag-jump]')) {
    event.preventDefault();
    jumpFromDiagnostic(event.target);
  }
  // Ctrl+Shift+C copies the console when it has a selection (xterm swallows plain Ctrl+C as
  // SIGINT into the PTY, so the copy shortcut is shifted — parity with the old dashboard).
  if (event.ctrlKey && event.shiftKey && (event.key === 'C' || event.key === 'c') && state.activeXterm?.term) {
    event.preventDefault();
    copyActiveConsole();
  }
}
