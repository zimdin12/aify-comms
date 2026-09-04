// Every console button the template emits is a button the dispatch handles.
//
// `console-click-handlers.mjs` HAS WARNED ABOUT THIS SINCE IT WAS EXTRACTED, in its own first
// paragraph: "An unrecognised action falls through every branch and does NOTHING, with no error
// anywhere, so a renamed attribute in the template turns a toolbar button into a no-op that looks
// fine in review." The warning was accurate and there was nothing enforcing it — a comment is not a
// gate, and the failure it describes is invisible from both sides: the button renders, the click
// lands, the handler runs, and nothing happens.
//
// THE POPULATION IS DERIVED FROM THE MARKUP, never listed here. A hand-kept list is the same defect
// one layer up — this repo has already had a keyboard-shortcut list go stale exactly that way, and
// the fix there was the same: derive from the template. A seventh action added tomorrow is covered
// with no edit to this file.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { runConsoleAction } from './console-click-handlers.mjs';

const TEMPLATE = readFileSync(new URL('./session-console.mjs', import.meta.url), 'utf8');

/** Every literal `data-console-action="..."` the template can render. */
function actionsInTemplate() {
  return [...TEMPLATE.matchAll(/data-console-action="([a-z-]+)"/g)].map((m) => m[1]);
}

const fakeButton = (action) => ({
  dataset: { consoleAction: action, terminalId: 't1', sessionId: 's1' },
  closest: () => ({ querySelector: () => null }),
});

const noop = () => Promise.resolve();

test('POSITIVE CONTROL: the scan finds the actions that are unmistakably in the template', () => {
  // A regex that matched nothing would make every assertion below vacuous, and an empty set passes
  // an "every" check silently.
  const found = actionsInTemplate();
  assert.ok(found.length >= 6, `the scan found only ${found.length} actions, so it is not reading the template`);
  for (const known of ['copy', 'refresh', 'find']) {
    assert.ok(found.includes(known), `the scan missed ${known}, which is in the template`);
  }
});

test('NEGATIVE CONTROL: the dispatch reports an action it does not handle', () => {
  // Without this the test below cannot fail: a dispatch that returned its input unconditionally
  // would agree with any template at all.
  assert.equal(runConsoleAction(fakeButton('no-such-action'), noop, noop, noop), null);
});

test('every action in the template is handled by the dispatch', () => {
  const unhandled = actionsInTemplate()
    .filter((action) => runConsoleAction(fakeButton(action), noop, noop, noop) === null);
  assert.deepEqual(unhandled, [],
    'these console buttons render and do nothing when clicked. Either the dispatch is missing a '
    + 'branch or the template has an attribute nobody handles — both look correct in review.');
});
