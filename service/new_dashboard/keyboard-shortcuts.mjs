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
import { applyConsoleFind, handleConsoleFindKey, toggleConsoleFind } from './console-find.mjs';
import { isSearchHotkey } from './terminal-search.mjs';
import { state } from './state.mjs';
import { closeStatusWhy, openStatusWhy } from './status-why-popover.mjs';
import { jumpFromDiagnostic } from './work-loop-panels.mjs';
import { byId } from './ui.js';

export function handleGlobalKeydown(event, closeInspector, toggleFavorite) {
  // THE FIND BOX FIRST, and it returns. Enter and Escape mean something else everywhere on this
  // page -- Escape dismisses the inspector, which is directly above the console -- so while the
  // caret is in the find box those keys belong to it and nothing below may also act on them.
  //
  // DELEGATED rather than bound at render: the console re-renders on every poll, and a listener
  // attached per render is a leak that grows for as long as the page is open.
  if (event.target?.matches?.('.console-find-input')) {
    if (handleConsoleFindKey(consoleHostOf(event.target), event)) return;
  }
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
  // Ctrl+Shift+F searches the scrollback, SHIFTED FOR THE SAME REASON as the copy shortcut above:
  // plain Ctrl+F is a real key in a terminal -- readline binds it to forward-char and vim to
  // page-forward -- so claiming it would break those inside the very consoles this searches, and
  // the breakage would look like the PTY dropping input.
  if (isSearchHotkey(event) && state.activeXterm?.term) {
    event.preventDefault();
    // The console this belongs to, from the container the mount recorded. A global lookup would
    // drive whichever console came first in the DOM, and a Chat-embedded console and the Sessions
    // console are both live on one page.
    const container = state.activeXterm.container;
    toggleConsoleFind(container?.closest?.('.console-embed') ?? container);
  }
}

/** The console embed a find-bar element sits in. */
export function consoleHostOf(el) {
  return el?.closest?.('.console-embed') ?? el;
}

/**
 * Search as the operator types.
 *
 * Delegated from ONE document-level `input` listener for the same reason the keys are: the console
 * re-renders on every poll. Live results are the whole difference between a find box and a form --
 * typing three characters and seeing `no results` says immediately that the word is not there.
 */
export function handleGlobalInput(event) {
  if (!event?.target?.matches?.('.console-find-input')) return;
  applyConsoleFind(consoleHostOf(event.target));
}
