import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FORM_INSPECTOR_KINDS,
  REFRESHABLE_INSPECTOR_KINDS,
  inspectorRefreshDecision,
  shouldRefreshInspector,
} from "./inspector-refresh.mjs";

// The operator's report: "when i have inspector open and status changes, it does not update."
test("an open agent drawer refreshes — the case that was reported", () => {
  assert.equal(inspectorRefreshDecision({ kind: 'agent' }, { isOpen: true }), 'refresh');
  assert.ok(shouldRefreshInspector({ kind: 'agent' }, { isOpen: true }));
});

test("every read-only kind refreshes", () => {
  for (const kind of REFRESHABLE_INSPECTOR_KINDS) {
    assert.equal(inspectorRefreshDecision({ kind }, { isOpen: true }), 'refresh', kind);
  }
});

// The reason the naive fix would have been worse than the bug.
test("a FORM drawer is never auto-refreshed — that would eat what you are typing", () => {
  for (const kind of FORM_INSPECTOR_KINDS) {
    assert.equal(inspectorRefreshDecision({ kind }, { isOpen: true }), 'form', kind);
  }
});

test("an unclassified kind FAILS CLOSED", () => {
  // Asymmetric cost: a stale read-only drawer is an annoyance, a wiped form is lost work. So a kind
  // nobody has classified must not be refreshed on the assumption that it is probably safe.
  assert.equal(inspectorRefreshDecision({ kind: 'some-future-panel' }, { isOpen: true }), 'unknown-kind');
  assert.equal(inspectorRefreshDecision({ kind: '' }, { isOpen: true }), 'no-kind');
  assert.equal(inspectorRefreshDecision({}, { isOpen: true }), 'no-kind');
  assert.equal(inspectorRefreshDecision(null, { isOpen: true }), 'no-kind');
});

test("a closed drawer is not work to do", () => {
  assert.equal(inspectorRefreshDecision({ kind: 'agent' }, { isOpen: false }), 'closed');
  assert.equal(inspectorRefreshDecision({ kind: 'agent' }, {}), 'closed');
});

test("focus inside the drawer suppresses the refresh", () => {
  // Even a read-only drawer must not be yanked out from under a gesture — text selected for copying,
  // a focused "load more" button.
  assert.equal(inspectorRefreshDecision({ kind: 'run' }, { isOpen: true, containsFocus: true }), 'focused');
});

test("a drawer with its own fetch in flight is left alone", () => {
  assert.equal(inspectorRefreshDecision({ kind: 'run' }, { isOpen: true, isLoading: true }), 'loading');
});

test("the two kind sets are disjoint", () => {
  // A kind in both lists would make the decision order-dependent, and the form check must win.
  const overlap = [...REFRESHABLE_INSPECTOR_KINDS].filter((k) => FORM_INSPECTOR_KINDS.has(k));
  assert.deepEqual(overlap, [], `a kind cannot be both read-only and a form: ${overlap.join(', ')}`);
});

test("the classification covers every kind app.js actually sets", async () => {
  // The gate that stops this module drifting from the UI it describes. A kind that app.js opens but
  // neither set names would silently fail closed forever — correct, but invisible, so it would look
  // like the original bug for that one panel.
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('./app.js', import.meta.url), 'utf8');
  const found = new Set(
    [...src.matchAll(/state\.inspector\s*=\s*\{[^}]*?kind:\s*'([a-z-]+)'/g)].map((m) => m[1]),
  );
  const unclassified = [...found].filter(
    (k) => !REFRESHABLE_INSPECTOR_KINDS.has(k) && !FORM_INSPECTOR_KINDS.has(k),
  );
  assert.deepEqual(
    unclassified, [],
    `app.js opens inspector kind(s) this module does not classify: ${unclassified.join(', ')}. `
    + 'Add each to REFRESHABLE_INSPECTOR_KINDS or FORM_INSPECTOR_KINDS — failing closed is correct '
    + 'but silent, so an unclassified panel just looks stale forever.',
  );
  assert.ok(found.size >= 5, `the kind scan looks wrong: found ${found.size}`);
});
