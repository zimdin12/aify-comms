// The environment card says when a host is running code its own disk has moved past.
//
// B4's remaining half. `bridge-current` answered a version of this until v0.6.1 retired the tier that
// reported a build of this repo; the question outlived the check, and the operator said what the CLI
// answer is worth to them: *"i never go to that path... will never use it."*
//
// WHAT THIS FILE IS REALLY GUARDING IS THE SILENCE. A badge that appears on every host until the whole
// fleet is upgraded is one nobody reads twice, and it would appear exactly where there is no evidence.
// So `unknown` draws nothing, and the absence of a badge is deliberately NOT a claim that a host is
// current -- the doctor row is where you find out which of the two it is.

import assert from 'node:assert/strict';
import test from 'node:test';

import { staleCodeBadge } from './environments-panels.mjs';

test('a host running older code than its disk gets a badge naming BOTH identities', () => {
  const html = staleCodeBadge({ codeCurrency: { state: 'stale', running: 'aaaa1111', onDisk: 'bbbb2222' } });
  assert.match(html, /aaaa1111/);
  assert.match(html, /bbbb2222/);
  // The remedy costs the operator every worker that host is running, so the badge has to say so.
  // Advice with that price attached and no warning is how somebody loses a fleet to a tooltip.
  assert.match(html, /reaps the managed workers/);
});

test('a current host draws NOTHING', () => {
  assert.equal(staleCodeBadge({ codeCurrency: { state: 'current', running: 'a', onDisk: 'a' } }), '');
});

test('NO EVIDENCE DRAWS NOTHING, and that is the case this badge could get wrong', () => {
  // An advertiser too old to report the pair has told us nothing. Drawing a warning would fire on
  // every host mid-upgrade; drawing an "all clear" would be the false green this repo has shipped
  // twice. It draws neither, and the doctor row carries the distinction.
  for (const currency of [
    { state: 'unknown', running: 'aaaa1111', onDisk: '' },
    { state: 'unknown', running: '', onDisk: '' },
    { state: 'stale', running: 'aaaa1111', onDisk: '' },   // a state that contradicts its own data
    undefined,
    null,
  ]) {
    assert.equal(staleCodeBadge({ codeCurrency: currency }), '', JSON.stringify(currency));
  }
  assert.equal(staleCodeBadge({}), '');
  assert.equal(staleCodeBadge(null), '');
});

test('the title is ESCAPED, because both identities come off the wire', () => {
  const html = staleCodeBadge({
    codeCurrency: { state: 'stale', running: '"><script>x</script>', onDisk: 'bbbb2222' },
  });
  assert.ok(!html.includes('<script>'), `an advertised value reached the DOM unescaped: ${html}`);
});
