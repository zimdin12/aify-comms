// The console's find bar: the DOM half of searching 5,000 lines of scrollback.
//
// The decisions are in `terminal-search.mjs` and the state is in `terminal-search-controller.mjs`.
// What is left here is opening a bar, reading a box and writing a count — deliberately thin, because
// this is the layer a test can only reach through fake elements.
//
// ONE SEARCH, like one console. `state.activeXterm` is a singleton: the dashboard mounts exactly one
// xterm at a time and disposes it on every switch. A search per host would have to be reaped
// alongside, and a stale one would drive a disposed terminal — which the controller survives, but
// only by finding nothing, silently. One search that always asks for the current terminal cannot get
// out of step with which console is open.

import { state } from './state.mjs';
import { TerminalSearch } from './terminal-search-controller.mjs';

const search = new TerminalSearch({ getTerminal: () => state.activeXterm?.term ?? null });

/** The bar, its box and its counter, or nulls when this host has no console rendered. */
function parts(host) {
  return {
    bar: host?.querySelector?.('.console-find') ?? null,
    input: host?.querySelector?.('.console-find-input') ?? null,
    summary: host?.querySelector?.('.console-find-summary') ?? null,
  };
}

/** Repeat the current query and paint the count. */
export function applyConsoleFind(host) {
  const { input, summary } = parts(host);
  if (!input) return;
  search.run(input.value ?? '');
  if (summary) summary.textContent = search.summary;
}

/** Next or previous hit. */
export function stepConsoleFind(host, direction = 1) {
  const { summary } = parts(host);
  // NOTHING SEARCHED YET means this is a first search, not a step past the end. Pressing Enter in an
  // untouched box should find, and treating it as a step would move through an empty match list and
  // report "no results" for a query nobody has run.
  if (search.count === 0) return applyConsoleFind(host);
  search.step(direction);
  if (summary) summary.textContent = search.summary;
  return undefined;
}

export function openConsoleFind(host) {
  const { bar, input } = parts(host);
  if (!bar) return;
  bar.hidden = false;
  // SELECT THE EXISTING TEXT rather than clearing it. Reopening find to look for the same thing
  // again is the common case, and typing over a selection is one keystroke either way.
  try { input?.focus?.(); input?.select?.(); } catch { /* not focusable in this host */ }
  if (input?.value) applyConsoleFind(host);
}

export function closeConsoleFind(host) {
  const { bar, input, summary } = parts(host);
  if (!bar) return;
  bar.hidden = true;
  // THE HIGHLIGHT GOES WITH THE BAR. A find that closes leaving its last hit selected leaves the
  // operator a selection they did not ask for and cannot easily clear, on a live console.
  search.clear();
  if (summary) summary.textContent = '';
  // The query is KEPT in the box on purpose — see `openConsoleFind`.
  if (input) input.value = input.value ?? '';
  // FOCUS GOES BACK TO THE TERMINAL, or the operator's next keystroke lands in a hidden input and
  // appears to do nothing at all.
  try { state.activeXterm?.term?.focus?.(); } catch { /* disposed */ }
}

export function toggleConsoleFind(host) {
  const { bar } = parts(host);
  if (!bar) return;
  if (bar.hidden === false) closeConsoleFind(host);
  else openConsoleFind(host);
}

/**
 * The find box's own keys.
 *
 * Enter steps forward, Shift+Enter back, Escape closes. Handled HERE rather than globally because
 * they only mean this while the box has focus — Escape in particular already dismisses the inspector.
 */
export function handleConsoleFindKey(host, event) {
  const key = String(event?.key || '');
  if (key === 'Escape') {
    event.preventDefault?.();
    closeConsoleFind(host);
    return true;
  }
  if (key === 'Enter') {
    event.preventDefault?.();
    stepConsoleFind(host, event.shiftKey ? -1 : 1);
    return true;
  }
  return false;
}

/** Test seam: the search is a module singleton, so a test needs a way back to a known state. */
export function resetConsoleFindForTests() {
  search.clear();
}
