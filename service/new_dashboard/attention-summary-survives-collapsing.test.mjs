/**
 * The Needs Attention strip says how many, in the one place collapsing does not hide.
 *
 * THE DEFECT, read off the live dashboard 2026-08-27. `.attention-strip.collapsed` sets
 * `display: none` on both `#metrics` and `#attention-list` (styles.css:262-263), leaving only the
 * title. So the panel whose entire job is answering "does anything need me?" answered nothing in the
 * state the operator actually leaves it in -- and finding out the list was empty meant expanding it,
 * which is the opposite of what collapsing is for. The collapse preference persists across reloads
 * (layout-prefs.mjs), so this is the steady state, not a transient.
 *
 * THE CAP IS WHY THE COUNT IS NOT JUST `items.length`. The list renders at most 8. A header reading
 * "8" while 30 contracts were overdue would be a silent truncation dressed as a total -- the same
 * shape as a report that bounds coverage without saying what it dropped.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { state } from './state.mjs';
import { attentionSummaryLabel, renderAttention } from './work-loop-panels.mjs';

test('clear reads as clear, not as a zero', () => {
  // "0" beside a heading called Needs Attention is a number the eye has to interpret. A tick is not.
  assert.equal(attentionSummaryLabel(0, 0), '✓ clear');
});

test('a full list shows the count alone', () => {
  assert.equal(attentionSummaryLabel(1, 1), '1');
  assert.equal(attentionSummaryLabel(8, 8), '8');
});

test('past the cap it names BOTH numbers, so the truncation is visible', () => {
  // The case the plain count would lie about: 30 need attention, 8 are on screen.
  assert.equal(attentionSummaryLabel(30, 8), '8 of 30');
  assert.equal(attentionSummaryLabel(9, 8), '8 of 9');
});

test('it never reports more shown than exist', () => {
  // A caller cannot produce this -- `items` is a slice of `matching` -- but a label reading "8 of 3"
  // would be worse than useless, so the ordering is asserted rather than assumed.
  assert.equal(attentionSummaryLabel(3, 8), '3');
});

test('junk does not throw or invent a count', () => {
  // It runs inside `renderAll`, which is unconditional; a throw here takes the whole render down.
  for (const [total, shown] of [[null, null], [undefined, undefined], [NaN, 0], ['', ''], [-5, -2]]) {
    assert.equal(attentionSummaryLabel(total, shown), '✓ clear',
      `invented a count from ${JSON.stringify([total, shown])}`);
  }
  assert.equal(attentionSummaryLabel('12', '8'), '8 of 12', 'numeric strings are real counts');
});

test('THE MARKUP CARRIES THE NODE, and it is inside the header', () => {
  // The pure function is worthless if nothing renders it, and the point of the fix is WHERE it
  // renders -- inside the <h2>, which is the only part of the strip that survives `.collapsed`.
  const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  const head = /<h2>.*?Needs Attention.*?<\/h2>/s.exec(html);
  assert.ok(head, 'positive control: the Needs Attention heading was not found at all');
  assert.match(head[0], /id="attention-summary"/,
    'the summary span is not inside the heading, so collapsing hides it again');
});

test('the borrowed classes exist, or the count renders unstyled', () => {
  // The fix adds NO css: it reuses `.chat-unread` (the rail's accent count pill) and `.subtle`.
  // Borrowing means a rename elsewhere silently unstyles this, so the dependency is asserted rather
  // than assumed -- styles.css is 1,844 lines and outside both size gates, which is why borrowing
  // was the right call and also why it needs pinning.
  const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
  assert.match(css, /^\.chat-unread \{/m, '.chat-unread is gone; the attention count is unstyled');
  assert.match(css, /\.subtle/, '.subtle is gone; the clear state is unstyled');
});

test('the collapsed rule still hides the list, which is what makes the header the only place', () => {
  // If this ever stops being true the fix is unnecessary rather than wrong -- but it should be a
  // decision, not a drift, so the premise is pinned where the fix lives.
  const css = readFileSync(new URL('./styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.attention-strip\.collapsed #metrics,\s*\n\.attention-strip\.collapsed #attention-list \{ display: none; \}/);
});

// ---------------------------------------------------------------------------
// THE CALL SITE. Everything above tests the label builder, and a mutation that made `renderAttention`
// count AFTER the cap -- the exact truncation lie the builder exists to prevent -- SURVIVED all of
// it. A green helper suite hides a disconnected call site; the only thing that catches this is
// driving the renderer with more contracts than it will draw.
// ---------------------------------------------------------------------------

function fakeEl() {
  return {
    textContent: '',
    innerHTML: '',
    classes: new Set(),
    classList: { toggle(name, on) { if (on) this._o.classes.add(name); else this._o.classes.delete(name); } },
  };
}

function withAttentionDom(run) {
  const summary = fakeEl(); summary.classList._o = summary;
  const list = fakeEl(); list.classList._o = list;
  const hadDoc = 'document' in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: (id) => ({ 'attention-summary': summary, 'attention-list': list }[id] ?? null) };
  try { run({ summary, list }); }
  finally { if (hadDoc) globalThis.document = prev; else delete globalThis.document; }
}

function overdue(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: `c${i}`, subject: `s${i}`, preview: '', from: 'a', targetAgentId: 'b', state: 'queued', overdue: true,
  }));
}

test('CALL SITE: past the cap the header names both numbers, not the eight it drew', () => {
  state.filter = '';
  state.contracts = overdue(30);
  withAttentionDom(({ summary, list }) => {
    renderAttention();
    assert.equal(summary.textContent, '8 of 30',
      'the header reported the RENDERED count as if it were the total');
    assert.equal(summary.className, 'chat-unread',
      'a real count must wear the same accent pill the conversation rail uses');
    assert.ok(!list.classes.has('is-clear'), 'positive control: 30 overdue contracts rendered as clear');
  });
});

test('CALL SITE: a short list reports its real length', () => {
  state.filter = '';
  state.contracts = overdue(3);
  withAttentionDom(({ summary }) => {
    renderAttention();
    assert.equal(summary.textContent, '3');
  });
});

test('CALL SITE: nothing matching reads as clear, and marks itself clear', () => {
  state.filter = '';
  // Present but NOT needing attention -- the filter must be what empties it, not an empty input.
  state.contracts = [{ id: 'c', subject: 's', state: 'completed', overdue: false }];
  withAttentionDom(({ summary, list }) => {
    renderAttention();
    assert.equal(summary.textContent, '✓ clear');
    assert.equal(summary.className, 'subtle', 'a clear strip still wore the accent count pill');
    assert.ok(list.classes.has('is-clear'));
  });
});

test('CALL SITE: a missing summary node does not stop the list rendering', () => {
  // `renderAttention` runs inside the unconditional renderAll loop. The strip's own guard comment
  // says a missing node must never throw out of it; the new node needs the same tolerance.
  state.filter = '';
  state.contracts = overdue(2);
  const list = fakeEl(); list.classList._o = list;
  const hadDoc = 'document' in globalThis;
  const prev = globalThis.document;
  globalThis.document = { getElementById: (id) => (id === 'attention-list' ? list : null) };
  try {
    renderAttention();
    assert.ok(list.innerHTML.length > 0, 'the list did not render without the summary node');
  } finally { if (hadDoc) globalThis.document = prev; else delete globalThis.document; }
});
