// Searching 5,000 lines of scrollback, and the two ways a naive version is quietly wrong.
//
// The failures this file is really about:
//   * A WRAPPED line searched row-by-row cannot be found. The strings people search for are long —
//     a path, a stack frame, a command — so a naive search would find short queries and miss long
//     ones, and the miss looks exactly like "that text is not in the buffer".
//   * Ctrl+F IS A REAL PTY KEY. Binding find to it would break readline and vim inside the very
//     consoles this exists to show, silently, looking like dropped input.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  findMatches,
  isSearchHotkey,
  logicalLines,
  matchPosition,
  matchSummary,
  stepMatch,
} from './terminal-search.mjs';

/** A buffer shaped like xterm's: physical rows, `isWrapped` on continuations. */
const bufferOf = (rows) => ({
  length: rows.length,
  getLine: (i) => (i in rows
    ? { isWrapped: Boolean(rows[i].wrapped), translateToString: () => rows[i].text }
    : undefined),
});

test('a query straddling a WRAP is found, which row-by-row search cannot do', () => {
  // The console is 40 columns; the operator searches for a path that wrapped across two rows.
  const buffer = bufferOf([
    { text: 'building' },
    { text: 'ERROR in /very/long/path/to/some' },
    { text: '/module.mjs line 12', wrapped: true },
  ]);
  const lines = logicalLines(buffer);
  assert.equal(lines.length, 2, 'the wrapped continuation was treated as its own line');

  const hits = findMatches(lines, '/some/module.mjs');
  assert.equal(hits.length, 1,
    'a string spanning a wrap boundary was not found — this is the failure that looks like the text '
    + 'not being in the buffer at all');
  assert.equal(hits[0].row, 1, 'the hit must name the row the LOGICAL line starts at, to scroll to it');
});

test('CONTROL: the same search finds an unwrapped match, so the test above is not passing by accident', () => {
  const lines = logicalLines(bufferOf([{ text: 'plain single row with target here' }]));
  assert.equal(findMatches(lines, 'target').length, 1);
});

test('a row xterm cannot return is SKIPPED, never treated as an empty line', () => {
  // A reflow racing this walk hands back undefined. Appending "" for it would split the wrapped
  // line in two and reintroduce the defect the first test guards.
  const rows = [{ text: 'first half of a long ' }, { text: 'and its continuation', wrapped: true }];
  const buffer = { length: 3, getLine: (i) => (i === 2 ? undefined : bufferOf(rows).getLine(i)) };
  const lines = logicalLines(buffer);
  assert.equal(lines.length, 1);
  assert.equal(findMatches(lines, 'long and its').length, 1, 'the missing row broke the join');
});

test('an EMPTY query matches nothing, rather than everything', () => {
  // `indexOf("")` is 0 for every string, so the obvious loop reports one hit per line the moment
  // the operator clears the box — a find that claims 5,000 results for no query.
  const lines = logicalLines(bufferOf([{ text: 'a' }, { text: 'b' }]));
  for (const query of ['', null, undefined]) {
    assert.deepEqual(findMatches(lines, query), [], `${JSON.stringify(query)} matched something`);
  }
});

test('the query is LITERAL, not a regular expression', () => {
  // `[ERROR] (retry 1)` is an ordinary thing to paste into a find box. As a pattern it either
  // matches the wrong thing or throws, and neither is explicable to the person who typed it.
  const lines = logicalLines(bufferOf([{ text: 'log: [ERROR] (retry 1) giving up' }]));
  assert.equal(findMatches(lines, '[ERROR] (retry 1)').length, 1);
  assert.equal(findMatches(lines, 'l.g:').length, 0, 'a dot behaved as a wildcard');
});

test('case-insensitive by default, exact when asked', () => {
  const lines = logicalLines(bufferOf([{ text: 'Connection Timeout after 30s' }]));
  assert.equal(findMatches(lines, 'timeout').length, 1, 'output is not typed by the reader');
  assert.equal(findMatches(lines, 'timeout', { caseSensitive: true }).length, 0);
  assert.equal(findMatches(lines, 'Timeout', { caseSensitive: true }).length, 1);
});

test('OVERLAPPING occurrences are all found', () => {
  const lines = logicalLines(bufferOf([{ text: 'aaa' }]));
  assert.equal(findMatches(lines, 'aa').length, 2, 'advancing by the match length skips a hit');
});

test('stepping wraps at both ends, and an unstarted search goes to the right end', () => {
  assert.equal(stepMatch(3, -1, 1), 0, 'forward from nothing selected must land on the first hit');
  assert.equal(stepMatch(3, -1, -1), 2, 'backward from nothing selected must land on the last');
  assert.equal(stepMatch(3, 2, 1), 0, 'forward did not wrap, so "next" would stop moving');
  assert.equal(stepMatch(3, 0, -1), 2, 'backward did not wrap');
  assert.equal(stepMatch(0, -1, 1), -1, 'there is nothing to step to');
});

test('the summary distinguishes NO RESULTS from a result, because they are acted on differently', () => {
  assert.equal(matchSummary(0, -1), 'no results');
  assert.equal(matchSummary(40, 0), '1 of 40');
  assert.equal(matchSummary(40, 39), '40 of 40');
});

test('THE HOTKEY IS NOT Ctrl+F, because Ctrl+F belongs to the PTY', () => {
  // readline binds it to forward-char and vim to page-forward. A console that swallowed it would
  // break those inside the consoles this feature exists to make usable, and the breakage would look
  // like the terminal dropping input.
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: false, key: 'f' }), false,
    'find claimed Ctrl+F, which readline and vim need');
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: true, key: 'F' }), true);
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: true, key: 'f' }), true,
    'the browser reports the shifted letter as either case depending on layout');
  // A chord carrying another modifier is somebody else's binding.
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: true, altKey: true, key: 'f' }), false);
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: true, metaKey: true, key: 'f' }), false);
  assert.equal(isSearchHotkey(null), false);
  assert.equal(isSearchHotkey({ ctrlKey: true, shiftKey: true, key: 'g' }), false);
});

test('a WRAPPED match maps back to the physical row and column xterm selects by', () => {
  // The offset `findMatches` reports is into the JOINED line, and joining is what made the match
  // findable. xterm selects by physical row/column, so handing it the raw offset would highlight the
  // wrong text — one row too high and off the right edge — for precisely the long matches this
  // feature exists to find. The bug would only ever show on the hits that matter.
  assert.deepEqual(matchPosition({ row: 10, index: 95 }, 80), { row: 11, col: 15 });
  assert.deepEqual(matchPosition({ row: 10, index: 5 }, 80), { row: 10, col: 5 },
    'an unwrapped match must not move');
  assert.deepEqual(matchPosition({ row: 10, index: 160 }, 80), { row: 12, col: 0 },
    'a match two wraps down');
});

test('a terminal that has not been fitted yet does not divide by zero', () => {
  assert.deepEqual(matchPosition({ row: 3, index: 7 }, 0), { row: 3, col: 7 });
  assert.deepEqual(matchPosition({ row: 3, index: 7 }, undefined), { row: 3, col: 7 });
});

test('A TRIMMED WRAPPED ROW maps back exactly, which dividing by cols cannot', () => {
  // `translateToString(true)` trims the right, so a row that wrapped after trailing spaces hands
  // over FEWER than `cols` characters. Dividing the offset by `cols` assumed every wrapped row gave
  // a full screenful, so every offset past a trimmed row resolved one row too high — on precisely
  // the long wrapped matches the join exists to find. The segments record what each physical row
  // actually contributed, which makes the mapping exact instead of an assumption.
  const buffer = bufferOf([
    { text: 'short' },                       // 5 of a 20-column terminal: trimmed
    { text: 'NEEDLE follows here', wrapped: true },
  ]);
  const lines = logicalLines(buffer);
  const hits = findMatches(lines, 'NEEDLE');
  assert.equal(hits.length, 1);

  const exact = matchPosition(hits[0], 20, lines[hits[0].line]);
  assert.deepEqual(exact, { row: 1, col: 0 },
    'the hit is at the start of the second physical row, and the mapping said otherwise');

  // The control: the OLD arithmetic, with no segments, gets it wrong — so this test is not passing
  // by describing something that was already true.
  const guessed = matchPosition(hits[0], 20);
  assert.notDeepEqual(guessed, exact,
    'dividing by cols already agreed, so the segments guard nothing — re-derive rather than delete');
});

test('CONTROL: an unwrapped hit is unmoved by the segment mapping', () => {
  const buffer = bufferOf([{ text: 'plain target here' }]);
  const lines = logicalLines(buffer);
  const hit = findMatches(lines, 'target')[0];
  assert.deepEqual(matchPosition(hit, 80, lines[hit.line]), { row: 0, col: 6 });
});
