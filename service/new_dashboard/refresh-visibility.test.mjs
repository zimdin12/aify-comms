// A hidden dashboard does not fetch; a dashboard shown again catches up once.
//
// Driven with a fake document whose visibility the test flips, because the real failure was a tab
// nobody was looking at: it has to be observed as a SEQUENCE -- hidden, events arrive, shown -- and not
// as one call.

import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';

import { createRefreshGate } from './refresh-visibility.mjs';

function fakeDocument(state = 'visible') {
  const listeners = [];
  return {
    visibilityState: state,
    addEventListener(type, fn) { if (type === 'visibilitychange') listeners.push(fn); },
    show() { this.visibilityState = 'visible'; listeners.forEach((fn) => fn()); },
    hide() { this.visibilityState = 'hidden'; listeners.forEach((fn) => fn()); },
  };
}

test('a visible page refreshes, and nothing is owed afterwards', () => {
  const doc = fakeDocument('visible');
  let caughtUp = 0;
  const gate = createRefreshGate({ doc, onVisibleAgain: () => { caughtUp += 1; } });
  assert.equal(gate.admit(), true);
  assert.equal(gate.stale, false);
  doc.hide();
  doc.show();
  assert.equal(caughtUp, 0, 'a page that never skipped a refresh was refreshed again on being shown');
});

test('a hidden page does not refresh, however many times it is asked', () => {
  const doc = fakeDocument('hidden');
  const gate = createRefreshGate({ doc });
  for (let i = 0; i < 5; i += 1) assert.equal(gate.admit(), false, 'a hidden tab fetched the poll bundle');
  assert.equal(gate.stale, true);
});

test('shown again, a page that skipped refreshes catches up exactly ONCE', () => {
  const doc = fakeDocument('hidden');
  let caughtUp = 0;
  const gate = createRefreshGate({ doc, onVisibleAgain: () => { caughtUp += 1; } });
  gate.admit();
  gate.admit();
  doc.show();
  assert.equal(caughtUp, 1, 'a tab brought back showed stale data, or refreshed once per skipped poll');
  assert.equal(gate.stale, false);
  doc.show();
  assert.equal(caughtUp, 1, 'a second visibility event refreshed again with nothing owed');
  assert.equal(gate.admit(), true, 'a page shown again stays held back');
});

test('a page hidden again before it was shown owes nothing extra', () => {
  const doc = fakeDocument('hidden');
  let caughtUp = 0;
  createRefreshGate({ doc, onVisibleAgain: () => { caughtUp += 1; } }).admit();
  doc.hide();
  assert.equal(caughtUp, 0, 'a hide event triggered the catch-up refresh');
});

test('no document, or no visibility state, never stops refreshes', () => {
  // A missing API must not become a dashboard that silently stops updating.
  assert.equal(createRefreshGate({ doc: null }).admit(), true);
  assert.equal(createRefreshGate({ doc: {} }).admit(), true);
  assert.equal(createRefreshGate({ doc: { visibilityState: 'prerender' } }).admit(), true);
});

test('THE CALL SITE: app.js refreshes through the gate, and catches up through refresh itself', () => {
  // The gate proven alone says nothing about whether the dashboard asks it -- the failure this repo
  // records as a green helper suite beside a disconnected call site.
  const app = fs.readFileSync(new URL('./app.js', import.meta.url), 'utf8');
  const body = app.slice(app.indexOf('async function refresh() {'));
  const firstStatement = body.split('\n')[1].trim();
  assert.match(firstStatement, /^if \(!refreshGate\.admit\(\)\) return;/, 'refresh() does not consult the gate first');
  assert.match(app, /const refreshGate = createRefreshGate\(\{ onVisibleAgain: \(\) => refresh\(\) \}\);/);
});
