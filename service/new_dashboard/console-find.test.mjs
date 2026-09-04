// The find bar, tested by CALLING it.
//
// The bar is thin on purpose, so what is worth testing here is the handful of places it could be
// quietly wrong in ways an operator would experience as the console misbehaving:
//   * closing it leaving a highlight on a live console that they cannot clear;
//   * closing it leaving focus in a hidden input, so their next keystroke does nothing;
//   * Enter in an untouched box reporting "no results" for a query nobody ran.

import assert from 'node:assert/strict';
import test from 'node:test';

import { state } from './state.mjs';
import {
  applyConsoleFind,
  closeConsoleFind,
  handleConsoleFindKey,
  openConsoleFind,
  resetConsoleFindForTests,
  stepConsoleFind,
  toggleConsoleFind,
} from './console-find.mjs';

function fakeHost(rows = []) {
  const bar = { hidden: true, className: 'console-find' };
  const input = { value: '', focused: 0, selected: 0, focus() { this.focused += 1; }, select() { this.selected += 1; } };
  const summary = { textContent: '' };
  return {
    bar,
    input,
    summary,
    querySelector: (sel) => {
      if (sel === '.console-find') return bar;
      if (sel === '.console-find-input') return input;
      if (sel === '.console-find-summary') return summary;
      return null;
    },
    rows,
  };
}

function mountFakeTerminal(rows, { cols = 80, termRows = 24 } = {}) {
  const calls = { selected: [], cleared: 0, focused: 0 };
  state.activeXterm = {
    term: {
      cols,
      rows: termRows,
      buffer: {
        active: {
          get length() { return rows.length; },
          getLine: (i) => (i in rows
            ? { isWrapped: Boolean(rows[i].wrapped), translateToString: () => rows[i].text }
            : undefined),
        },
      },
      scrollToLine: () => {},
      select: (col, row, length) => calls.selected.push({ col, row, length }),
      clearSelection: () => { calls.cleared += 1; },
      focus: () => { calls.focused += 1; },
    },
  };
  return calls;
}

test.afterEach(() => { state.activeXterm = null; resetConsoleFindForTests(); });

test('a query paints a count, not just a highlight', () => {
  // "no results" and "1 of 40" are different facts and an operator acts differently on them. With
  // only a highlight they are indistinguishable when the hit is off-screen.
  mountFakeTerminal([{ text: 'alpha' }, { text: 'beta alpha' }]);
  const host = fakeHost();
  host.input.value = 'alpha';
  applyConsoleFind(host);
  assert.equal(host.summary.textContent, '1 of 2');
});

test('a query with no hits SAYS so', () => {
  mountFakeTerminal([{ text: 'alpha' }]);
  const host = fakeHost();
  host.input.value = 'nowhere';
  applyConsoleFind(host);
  assert.equal(host.summary.textContent, 'no results');
});

test('ENTER in an untouched box SEARCHES, rather than stepping an empty list', () => {
  // Otherwise the first Enter reports "no results" for a query nobody has run yet — the box would
  // look broken on its very first use.
  mountFakeTerminal([{ text: 'target' }]);
  const host = fakeHost();
  host.input.value = 'target';
  const handled = handleConsoleFindKey(host, { key: 'Enter', preventDefault() {} });
  assert.equal(handled, true);
  assert.equal(host.summary.textContent, '1 of 1');
});

test('Enter steps forward and Shift+Enter steps back, both wrapping', () => {
  mountFakeTerminal([{ text: 'hit' }, { text: 'hit' }, { text: 'hit' }]);
  const host = fakeHost();
  host.input.value = 'hit';
  applyConsoleFind(host);
  assert.equal(host.summary.textContent, '1 of 3');
  handleConsoleFindKey(host, { key: 'Enter', preventDefault() {} });
  assert.equal(host.summary.textContent, '2 of 3');
  handleConsoleFindKey(host, { key: 'Enter', shiftKey: true, preventDefault() {} });
  assert.equal(host.summary.textContent, '1 of 3');
  handleConsoleFindKey(host, { key: 'Enter', shiftKey: true, preventDefault() {} });
  assert.equal(host.summary.textContent, '3 of 3', 'stepping back from the first did not wrap');
});

test('CLOSING drops the highlight and returns focus to the terminal', () => {
  // Both halves are operator-visible: a selection they cannot clear on a live console, and a next
  // keystroke that lands in a hidden input and appears to do nothing.
  const calls = mountFakeTerminal([{ text: 'target' }]);
  const host = fakeHost();
  host.input.value = 'target';
  openConsoleFind(host);
  assert.equal(host.bar.hidden, false);

  closeConsoleFind(host);
  assert.equal(host.bar.hidden, true);
  assert.ok(calls.cleared > 0, 'the selection was left on the console');
  assert.equal(calls.focused, 1, 'focus stayed in the hidden input');
  assert.equal(host.summary.textContent, '');
});

test('Escape closes it', () => {
  mountFakeTerminal([{ text: 'x' }]);
  const host = fakeHost();
  openConsoleFind(host);
  assert.equal(handleConsoleFindKey(host, { key: 'Escape', preventDefault() {} }), true);
  assert.equal(host.bar.hidden, true);
});

test('a key the bar does not own is NOT swallowed', () => {
  // The box is an ordinary text input; claiming every key would stop the operator typing in it.
  const host = fakeHost();
  assert.equal(handleConsoleFindKey(host, { key: 'a' }), false);
  assert.equal(handleConsoleFindKey(host, {}), false);
});

test('reopening KEEPS the query and selects it', () => {
  // Looking for the same string again is the common case; typing over a selection costs nothing.
  mountFakeTerminal([{ text: 'target' }]);
  const host = fakeHost();
  host.input.value = 'target';
  openConsoleFind(host);
  closeConsoleFind(host);
  openConsoleFind(host);
  assert.equal(host.input.value, 'target', 'the query was cleared on close');
  assert.ok(host.input.selected > 0, 'the retained query was not selected for overtyping');
  assert.equal(host.summary.textContent, '1 of 1', 'reopening did not re-run the retained query');
});

test('toggling opens then closes', () => {
  mountFakeTerminal([{ text: 'x' }]);
  const host = fakeHost();
  toggleConsoleFind(host);
  assert.equal(host.bar.hidden, false);
  toggleConsoleFind(host);
  assert.equal(host.bar.hidden, true);
});

test('a host with NO console rendered is left alone rather than throwing', () => {
  // Every one of these runs from a click or a global keystroke, which can arrive on a page that has
  // no console on it at all.
  const empty = { querySelector: () => null };
  openConsoleFind(empty);
  closeConsoleFind(empty);
  toggleConsoleFind(empty);
  stepConsoleFind(empty, 1);
  applyConsoleFind(empty);
  assert.ok(true, 'a page without a console threw');
});
