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
  #lines = [];
  #current = -1;
  //: WHICH TERMINAL THESE MATCHES DESCRIBE. Absolute buffer rows mean nothing against a different
  //: terminal, and the dashboard disposes and remounts one on every session switch — from four call
  //: sites, none of which clears this search, because it is a module singleton the teardown paths
  //: know nothing about. Rather than add a fifth thing for each of them to remember, the search
  //: checks its own state: cleanup that must hold for ALL paths keys on the state, not on an event.
  #matchedTerminal = null;

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
    this.#current = -1;
    this.#recompute();
    return this.step(1);
  }

  /**
   * Move to the next or previous hit and reveal it.
   *
   * RE-READS FIRST, and that is not belt-and-braces. The console is live and the buffer is capped at
   * 5,000 lines, so once it is full every new line shifts every absolute row down by one — and this
   * reveals BY ABSOLUTE ROW. Stepping through a list taken at the last keystroke put the highlight
   * further from the real hit with each press while a chatty agent kept printing. The re-read costs
   * one walk of the buffer, which is what every keystroke already pays.
   */
  step(direction = 1) {
    this.#recompute();
    this.#current = stepMatch(this.#matches.length, this.#current, direction);
    this.#reveal();
    return { index: this.#current, summary: this.summary, count: this.#matches.length };
  }

  /**
   * Take the matches from the terminal as it is NOW.
   *
   * A DIFFERENT TERMINAL VOIDS THE POSITION, not just the matches. After a session switch the rows
   * this search is holding describe a buffer that is gone, and revealing one would scroll the NEW
   * console somewhere arbitrary and highlight an unrelated block — with nothing thrown and no clue.
   */
  #recompute() {
    const terminal = this.#getTerminal();
    if (terminal !== this.#matchedTerminal) {
      this.#matchedTerminal = terminal;
      this.#current = -1;
    }
    const buffer = terminal?.buffer?.active;
    // THE LINES ARE KEPT, not just the matches: each carries the per-physical-row lengths that turn
    // an offset into an exact screen position. Dividing by the terminal width instead assumes every
    // wrapped row contributed a full screenful, which trimming makes false.
    this.#lines = buffer && this.#query ? logicalLines(buffer) : [];
    this.#matches = this.#lines.length
      ? findMatches(this.#lines, this.#query, { caseSensitive: this.#caseSensitive })
      : [];
    // The buffer may have scrolled matches out from under the cursor; keep it inside the new list
    // rather than pointing past the end.
    if (this.#current >= this.#matches.length) this.#current = this.#matches.length - 1;
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
    this.#lines = [];
    this.#current = -1;
    const terminal = this.#getTerminal();
    try { terminal?.clearSelection?.(); } catch { /* a disposed terminal has nothing to clear */ }
  }

  #reveal() {
    const match = this.#matches[this.#current];
    const terminal = this.#getTerminal();
    // NOTHING TO SHOW MEANS NOTHING SHOWN. This used to return here and leave the previous hit
    // highlighted on a live console — so clearing the find box left a selection the operator could
    // not get rid of, and `clipboard.mjs` copies a selection when there is one, so the next
    // Ctrl+Shift+C silently copied six stale characters instead of the buffer.
    if (!match) {
      try { terminal?.clearSelection?.(); } catch { /* disposed */ }
      return;
    }
    if (!terminal) return;
    const { row, col } = matchPosition(match, terminal.cols, this.#lines[match.line]);
    // EVERY CALL IS GUARDED SEPARATELY. These reach into a live xterm, and a terminal disposed
    // between the buffer read above and this line throws from whichever call gets there first. A
    // find that throws would break the console it is searching, which is a far worse outcome than
    // a hit that failed to scroll.
    try { terminal.scrollToLine?.(Math.max(0, row - Math.floor((Number(terminal.rows) || 2) / 2))); } catch { /* not scrollable */ }
    try { terminal.select?.(col, row, match.length); } catch { /* not selectable */ }
  }
}
