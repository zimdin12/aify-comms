// The find box driving a live console.
//
// What this file is really guarding: the console is LIVE and it is DISPOSED AND REMOUNTED whenever
// the operator switches session. Both facts break the obvious implementation — a cached line list
// searches a buffer that has moved on, and a held Terminal reference drives a disposed object, which
// throws inside xterm rather than politely doing nothing. A find that throws breaks the console it
// is searching, which is worse than not having find.

import assert from 'node:assert/strict';
import test from 'node:test';

import { TerminalSearch } from './terminal-search-controller.mjs';

/** A terminal shaped like xterm's, recording what was asked of it. */
function fakeTerminal(rows, { cols = 80, termRows = 24 } = {}) {
  const calls = { scrolled: [], selected: [], cleared: 0 };
  return {
    cols,
    rows: termRows,
    buffer: {
      active: {
        // A GETTER, because a live buffer GROWS. Capturing `rows.length` once made the fake unable
        // to change and failed the re-read test against a controller that re-reads correctly.
        get length() { return rows.length; },
        getLine: (i) => (i in rows
          ? { isWrapped: Boolean(rows[i].wrapped), translateToString: () => rows[i].text }
          : undefined),
      },
    },
    scrollToLine: (line) => calls.scrolled.push(line),
    select: (col, row, length) => calls.selected.push({ col, row, length }),
    clearSelection: () => { calls.cleared += 1; },
    calls,
  };
}

const linesOf = (...texts) => texts.map((text) => ({ text }));

test('a query selects its first hit and scrolls it into view', () => {
  const terminal = fakeTerminal(linesOf('one', 'two target three', 'four'));
  const search = new TerminalSearch({ getTerminal: () => terminal });

  const result = search.run('target');
  assert.equal(result.count, 1);
  assert.equal(search.summary, '1 of 1');
  assert.deepEqual(terminal.calls.selected.at(-1), { col: 4, row: 1, length: 6 });
});

test('the hit is scrolled TOWARD THE MIDDLE, not to the top edge', () => {
  // A hit pinned to the first row shows the operator nothing that came before it, which is usually
  // the context they are searching for.
  const terminal = fakeTerminal(linesOf(...Array.from({ length: 60 }, (_, i) => `line ${i} x`)), { termRows: 24 });
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('line 40');
  assert.equal(terminal.calls.scrolled.at(-1), 28, 'the hit was not centred (row 40 minus half of 24)');
});

test('stepping moves through hits and WRAPS, so "next" always moves', () => {
  const terminal = fakeTerminal(linesOf('hit', 'miss', 'hit', 'hit'));
  const search = new TerminalSearch({ getTerminal: () => terminal });

  assert.equal(search.run('hit').count, 3);
  assert.equal(search.summary, '1 of 3');
  assert.equal(search.step(1).summary, '2 of 3');
  assert.equal(search.step(1).summary, '3 of 3');
  assert.equal(search.step(1).summary, '1 of 3', 'next stopped moving at the end');
  assert.equal(search.step(-1).summary, '3 of 3', 'previous did not wrap backward');
});

test('THE BUFFER IS RE-READ on every run, because the console is live', () => {
  // Output arrives while the find box is open. A cached line list would search a buffer that no
  // longer exists and scroll to rows that have moved.
  const rows = linesOf('first');
  const terminal = fakeTerminal(rows);
  const search = new TerminalSearch({ getTerminal: () => terminal });

  assert.equal(search.run('arrived').count, 0, 'the text is not there yet');
  rows.push({ text: 'arrived later' });
  assert.equal(search.run('arrived').count, 1, 'the search did not see output that arrived after it');
});

test('a DISPOSED terminal finds nothing rather than throwing', () => {
  // The console is disposed and remounted on every session switch. A controller holding the old
  // Terminal would drive a disposed object; asking for it each time means the search simply stops.
  let terminal = fakeTerminal(linesOf('target here'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  assert.equal(search.run('target').count, 1);

  terminal = null;
  assert.equal(search.run('target').count, 0);
  assert.equal(search.summary, 'no results');
  search.step(1);
  search.clear();
});

test('a terminal that THROWS from select or scroll does not break the search', () => {
  // Guarded separately on purpose: a terminal disposed between the buffer read and the reveal
  // throws from whichever call gets there first, and a find that throws breaks the console.
  const terminal = fakeTerminal(linesOf('target'));
  terminal.scrollToLine = () => { throw new Error('disposed'); };
  terminal.select = () => { throw new Error('disposed'); };
  const search = new TerminalSearch({ getTerminal: () => terminal });
  assert.equal(search.run('target').count, 1, 'a throwing terminal lost the match');
});

test('clearing forgets the query AND drops the highlight', () => {
  // A find box that closes leaving its hit selected leaves the operator a highlight they cannot get
  // rid of, on a live console.
  const terminal = fakeTerminal(linesOf('target'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('target');
  search.clear();
  assert.equal(terminal.calls.cleared, 1, 'the selection was left behind');
  assert.equal(search.query, '');
  assert.equal(search.summary, 'no results');
});

test('a WRAPPED hit selects the physical row it is really on', () => {
  // The end-to-end version of `matchPosition`: the join is what found it, and the position is what
  // makes the highlight land on it.
  const terminal = fakeTerminal([
    { text: 'x'.repeat(10) },
    { text: 'y'.repeat(10) },
    { text: 'NEEDLE tail', wrapped: true },
  ], { cols: 10 });
  const search = new TerminalSearch({ getTerminal: () => terminal });
  assert.equal(search.run('NEEDLE').count, 1, 'a hit past a wrap boundary was not found');
  assert.deepEqual(terminal.calls.selected.at(-1), { col: 0, row: 2, length: 6 },
    'the highlight landed on the joined offset rather than the physical row');
});

test('no query means no selection is disturbed', () => {
  const terminal = fakeTerminal(linesOf('anything'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('');
  assert.equal(terminal.calls.selected.length, 0, 'an empty query selected something');
  assert.equal(search.summary, 'no results');
});

test('A DIFFERENT TERMINAL VOIDS THE POSITION, so a session switch cannot drive the new console', () => {
  // The dashboard disposes and remounts one xterm on every session switch, from four call sites, and
  // none of them clears this search — it is a module singleton they know nothing about. So the rows
  // held here described a buffer that was gone, and `stepConsoleFind` skips its re-search whenever
  // `count` is non-zero: pressing Enter after switching scrolled the NEW console somewhere arbitrary
  // and highlighted an unrelated block, with nothing thrown and no clue.
  let terminal = fakeTerminal(linesOf('alpha hit', 'beta', 'gamma hit'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  assert.equal(search.run('hit').count, 2);

  // The operator switches session: a new terminal, and this one has no such text.
  terminal = fakeTerminal(linesOf('a wholly different console'));
  const stepped = search.step(1);
  assert.equal(stepped.count, 0, 'it stepped through matches belonging to a terminal that is gone');
  assert.deepEqual(terminal.calls.selected, [], 'it highlighted a block of the NEW console');
});

test('hits do not DRIFT when the buffer scrolls under them', () => {
  // The console is live and its scrollback is capped, so once full every new line shifts every
  // absolute row down by one — and reveal is by absolute row. Stepping a list taken at the last
  // keystroke put the highlight further from the real hit with every press.
  const rows = linesOf('needle here', 'filler');
  const terminal = fakeTerminal(rows);
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('needle');
  assert.deepEqual(terminal.calls.selected.at(-1), { col: 0, row: 0, length: 6 });

  // The agent prints, and the line the hit is on moves down.
  rows.unshift({ text: 'new output' }, { text: 'more output' });
  search.step(1);
  assert.deepEqual(terminal.calls.selected.at(-1), { col: 0, row: 2, length: 6 },
    'the highlight kept pointing at the row the hit USED to be on');
});

test('CLEARING THE QUERY drops the highlight, or the clipboard copies it', () => {
  // `clipboard.mjs` copies the selection when there is one and only falls back to the whole buffer
  // when there is not. So a hit left highlighted after the box was emptied meant the next
  // Ctrl+Shift+C silently copied six stale characters instead of the console.
  const terminal = fakeTerminal(linesOf('target here'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('target');
  assert.equal(terminal.calls.selected.length, 1);

  const before = terminal.calls.cleared;
  search.run('');
  assert.ok(terminal.calls.cleared > before,
    'emptying the box left the previous hit selected on a live console');
});

test('CONTROL: a query that simply has no hits also clears, and does not select', () => {
  const terminal = fakeTerminal(linesOf('nothing relevant'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('absent');
  assert.equal(search.summary, 'no results');
  assert.deepEqual(terminal.calls.selected, []);
});

test('switching console restarts the search AT THE TOP, not where you were in the other one', () => {
  // The re-read alone stops a switch driving the new console from the old buffer's rows. What the
  // terminal-identity check adds is the POSITION: without it the cursor keeps whatever index it had
  // in the previous console, so "next" resumes in the middle of a list the operator has not seen.
  // Written because removing the check left every other test in this file green — untested code is
  // code nobody can defend.
  let terminal = fakeTerminal(linesOf('hit', 'hit', 'hit'));
  const search = new TerminalSearch({ getTerminal: () => terminal });
  search.run('hit');
  search.step(1);
  assert.equal(search.summary, '2 of 3', 'the operator is partway through this console');

  terminal = fakeTerminal(linesOf('hit', 'hit', 'hit'));   // a different console, same query
  assert.equal(search.step(1).summary, '1 of 3',
    'the search resumed mid-list in a console the operator had not searched yet');
});
