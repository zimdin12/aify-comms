// Driving a terminal from a find box: what to select, where to scroll, what the count says.
//
// The decisions live in `terminal-search.mjs`; this is the part that HOLDS THE STATE — the current
// query, the hits, and which hit is showing. It is a class because that is exactly what it is: a
// thing with identity and state, as opposed to the pure functions it composes.
//
// THE TERMINAL IS INJECTED AS A GETTER, not held. A console is disposed and remounted whenever the
// operator switches session, and a controller holding the old Terminal would drive a disposed
// object — which throws inside xterm rather than politely doing nothing. Asking for it each time
// means a search that outlives its terminal simply finds nothing.

import { findMatches, logicalLines, matchPosition, matchSummary, stepMatch } from './terminal-search.mjs';

export class TerminalSearch {
  #getTerminal;
  #query = '';
  #caseSensitive = false;
  #matches = [];
  #current = -1;

  /** @param {{getTerminal: () => object|null}} deps */
  constructor({ getTerminal }) {
    this.#getTerminal = getTerminal;
  }

  get query() { return this.#query; }

  get summary() { return matchSummary(this.#matches.length, this.#current); }

  get count() { return this.#matches.length; }

  /**
   * Run a query and show its first hit.
   *
   * RE-READ THE BUFFER EVERY TIME. The console is live: output arrives while the find box is open,
   * so a cached line list would search a buffer that no longer exists and scroll to rows that have
   * moved. Re-reading is O(scrollback) on a 5,000-line cap and happens on a keystroke, which is
   * nothing next to what the terminal renderer is already doing.
   */
  run(query, { caseSensitive = this.#caseSensitive } = {}) {
    this.#query = String(query ?? '');
    this.#caseSensitive = Boolean(caseSensitive);
    const terminal = this.#getTerminal();
    const buffer = terminal?.buffer?.active;
    this.#matches = buffer
      ? findMatches(logicalLines(buffer), this.#query, { caseSensitive: this.#caseSensitive })
      : [];
    this.#current = -1;
    return this.step(1);
  }

  /** Move to the next or previous hit and reveal it. */
  step(direction = 1) {
    this.#current = stepMatch(this.#matches.length, this.#current, direction);
    this.#reveal();
    return { index: this.#current, summary: this.summary, count: this.#matches.length };
  }

  /**
   * Forget the search, and take the highlight with it.
   *
   * CLEARING THE SELECTION IS THE POINT. A find box that closes leaving its last hit selected leaves
   * the operator with a highlight they cannot get rid of and did not ask for, on a live console.
   */
  clear() {
    this.#query = '';
    this.#matches = [];
    this.#current = -1;
    const terminal = this.#getTerminal();
    try { terminal?.clearSelection?.(); } catch { /* a disposed terminal has nothing to clear */ }
  }

  #reveal() {
    const match = this.#matches[this.#current];
    const terminal = this.#getTerminal();
    if (!match || !terminal) return;
    const { row, col } = matchPosition(match, terminal.cols);
    // EVERY CALL IS GUARDED SEPARATELY. These reach into a live xterm, and a terminal disposed
    // between the buffer read above and this line throws from whichever call gets there first. A
    // find that throws would break the console it is searching, which is a far worse outcome than
    // a hit that failed to scroll.
    try { terminal.scrollToLine?.(Math.max(0, row - Math.floor((Number(terminal.rows) || 2) / 2))); } catch { /* not scrollable */ }
    try { terminal.select?.(col, row, match.length); } catch { /* not selectable */ }
  }
}
