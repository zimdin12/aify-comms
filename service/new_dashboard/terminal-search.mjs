// Finding something in 5,000 lines of scrollback.
//
// B1, "the browser pseudo-terminal, fast and good". The console keeps `scrollback: 5000` and had no
// way to look through it: an operator watching an agent work could scroll, and that was all. The
// thing people actually want from a terminal buffer is to find the error they saw go past.
//
// NO NEW DEPENDENCY. `@xterm/addon-search` exists, but everything under `/assets/vendor` was
// deliberately vendored so the console works on an air-gapped LAN, and each addition is a pinned
// third-party blob somebody has to keep. xterm's buffer is public API and the search itself is
// twenty lines of string matching, so this reads the buffer directly and adds nothing to vendor.
//
// PURE, AND THE BUFFER IS INJECTED. Nothing here touches a Terminal, a DOM node or a window, so the
// whole thing runs in Node — the repo's own rule that logic living only in the browser can only fail
// in production.

/**
 * The hotkey, and it is NOT Ctrl+F.
 *
 * Ctrl+F IS A REAL KEY IN A TERMINAL. readline binds it to forward-char, vim to page-forward, and
 * emacs to forward-char — so a console that swallowed it would break the very TUIs this console
 * exists to show, and it would break them silently, in a way that looks like the PTY dropping input.
 * Ctrl+Shift+F is what VS Code and the terminals it borrows from use for exactly this reason: the
 * shifted chord has no PTY meaning, because a terminal cannot encode it.
 */
export function isSearchHotkey(event) {
  if (!event || !event.ctrlKey || !event.shiftKey) return false;
  if (event.altKey || event.metaKey) return false;
  return String(event.key || '').toLowerCase() === 'f';
}

/**
 * The buffer's PHYSICAL rows joined into LOGICAL lines, with the row each one starts at.
 *
 * WRAPPING IS THE WHOLE DIFFICULTY. xterm stores a line longer than the viewport as several rows,
 * each flagged `isWrapped` except the first. A search that read rows independently would fail to
 * find any string that happens to straddle a wrap — and the strings people search for are long ones:
 * a file path, a stack frame, a command they typed. It would find short queries reliably and miss
 * long ones, which is worse than not having search, because the failure looks like "that text isn't
 * in the buffer".
 *
 * The starting ROW is carried so a caller can scroll to a hit. The row is what xterm scrolls to; the
 * logical line is only what we matched against.
 *
 * @param {{length: number, getLine: (i: number) => ({isWrapped?: boolean, translateToString: (trim?: boolean) => string}|null|undefined)}} buffer
 * @returns {{text: string, row: number}[]}
 */
export function logicalLines(buffer) {
  const out = [];
  const total = Number(buffer?.length) || 0;
  for (let row = 0; row < total; row += 1) {
    // A NULL ROW IS SKIPPED, NOT TREATED AS EMPTY. xterm returns undefined for a row outside the
    // buffer, and a race between a reflow and this walk can hand one back mid-iteration. Appending
    // "" for it would silently split a wrapped line in two and reintroduce the bug above.
    const line = buffer.getLine(row);
    if (!line) continue;
    const text = line.translateToString(true);
    if (line.isWrapped && out.length > 0) {
      const previous = out[out.length - 1];
      previous.text += text;
      previous.segments.push({ row, length: text.length });
      continue;
    }
    // WHAT EACH PHYSICAL ROW ACTUALLY CONTRIBUTED. Mapping an offset back by dividing by `cols`
    // assumes every wrapped row handed over exactly a screenful — and `translateToString(true)`
    // TRIMS THE RIGHT, so a row that wrapped after trailing spaces contributes fewer. Every offset
    // past such a row then resolved one row too high, on precisely the long wrapped matches the join
    // exists to find. Recording the lengths makes the mapping exact instead of an assumption.
    out.push({ text, row, segments: [{ row, length: text.length }] });
  }
  return out;
}

/**
 * Every match, in buffer order.
 *
 * CASE-INSENSITIVE BY DEFAULT, because the thing being searched is program output and nobody
 * remembers whether the error said "Timeout" or "timeout".
 *
 * The query is matched LITERALLY rather than as a regular expression. Terminal output is full of
 * brackets, dots and parentheses — `[ERROR] (retry 1)` is a normal thing to paste into a find box —
 * and treating that as a pattern gives either a wrong match or a syntax error, neither of which the
 * person typing it would understand.
 *
 * @returns {{row: number, line: number, index: number, length: number}[]}
 */
export function findMatches(lines, query, { caseSensitive = false } = {}) {
  const needle = String(query ?? '');
  // AN EMPTY QUERY MATCHES NOTHING, rather than matching at every position. `indexOf("")` is 0 for
  // every string, so the obvious loop would report one hit per line the moment the box is cleared.
  if (!needle) return [];
  const wanted = caseSensitive ? needle : needle.toLowerCase();
  const found = [];
  lines.forEach((entry, line) => {
    const haystack = caseSensitive ? entry.text : entry.text.toLowerCase();
    let at = haystack.indexOf(wanted);
    while (at !== -1) {
      found.push({ row: entry.row, line, index: at, length: needle.length });
      // ADVANCE BY ONE, NOT BY THE MATCH LENGTH, so overlapping occurrences are all found: "aa" in
      // "aaa" is two matches at 0 and 1, and a reader stepping through hits expects both.
      at = haystack.indexOf(wanted, at + 1);
    }
  });
  return found;
}

/**
 * The next match to show, wrapping at both ends.
 *
 * WRAPPING IS THE POINT. A find that stops at the last hit makes the operator guess whether there
 * are more above; wrapping means "next" always moves and the count tells them where they are.
 *
 * `current` of -1 means nothing is selected yet, so forward goes to the first hit and backward to
 * the last — the behaviour of every find box, and the reason this is a function rather than a `+1`
 * at the call site.
 */
export function stepMatch(count, current, direction = 1) {
  const total = Number(count) || 0;
  if (total <= 0) return -1;
  const step = direction < 0 ? -1 : 1;
  const from = Number.isInteger(current) ? current : -1;
  if (from < 0) return step > 0 ? 0 : total - 1;
  return ((from + step) % total + total) % total;
}

/**
 * What the find box says beside the query.
 *
 * A COUNT, NOT JUST A HIGHLIGHT. "no results" and "1 of 40" are different facts and an operator
 * acts differently on them — the first means try another word, the second means keep pressing next.
 * Rendering only the highlight leaves them indistinguishable when the match is off-screen.
 */
export function matchSummary(count, current) {
  const total = Number(count) || 0;
  if (total <= 0) return 'no results';
  const at = Number.isInteger(current) && current >= 0 ? current + 1 : 1;
  return `${at} of ${total}`;
}

/**
 * Where a match SITS ON THE SCREEN, which is not where it sits in the logical line.
 *
 * `findMatches` reports an offset into the joined line, and joining is what made a wrapped match
 * findable at all. But xterm selects by physical row and column, so an offset of 95 in an 80-column
 * terminal is row+1, column 15 — and handing the raw offset to `select` would highlight the wrong
 * text, one row too high and off the right edge, for exactly the long matches the join exists to
 * find. The bug would therefore appear only on the hits this feature was built for.
 *
 * A NON-POSITIVE WIDTH LEAVES THE MATCH WHERE IT IS rather than dividing by zero. `cols` comes from
 * a live terminal and is 0 before the first fit completes.
 */
export function matchPosition(match, cols, line = null) {
  const index = Number(match?.index) || 0;
  const row = Number(match?.row) || 0;

  // THE SEGMENTS ARE EXACT AND THE WIDTH IS A GUESS, so prefer them whenever the caller has them.
  // Each segment says how many characters one physical row contributed to the joined line, which is
  // the only thing that survives `translateToString(true)` trimming the right of a wrapped row.
  const segments = Array.isArray(line?.segments) ? line.segments : null;
  if (segments && segments.length) {
    let remaining = index;
    for (const segment of segments) {
      const length = Number(segment?.length) || 0;
      if (remaining < length || segment === segments[segments.length - 1]) {
        return { row: Number(segment?.row) || row, col: remaining };
      }
      remaining -= length;
    }
  }

  // FALLBACK, for a caller with no segments: divide by the terminal width. Inexact for a wrapped row
  // that was trimmed, which is why the segments exist.
  const width = Number(cols);
  if (!Number.isFinite(width) || width <= 0) return { row, col: index };
  return { row: row + Math.floor(index / width), col: index % width };
}
