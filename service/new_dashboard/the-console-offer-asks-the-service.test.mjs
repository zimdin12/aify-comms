// Whether a console can be opened is the service's answer, and the fallback fails closed.
//
// THE DEAD FIELD. `records.py` emits `consoleAvailable` on every agent row, with a comment saying
// "the dashboard should hide the button for these". Found by listing every field the live /agents
// payload carries and searching the whole repo for a consumer: it had none. Computed on every
// request, serialised to every client, read by nothing -- while the dashboard derived the same
// answer from `sessionMode` a few lines away.
//
// THE TWO DISAGREED ON THE EMPTY CASE, in opposite directions. The service normalises an unknown or
// absent mode to `resident` (`_normalize_session_mode`), so it says no console. The dashboard
// compared `=== 'resident'`, so an empty mode was NOT resident and it offered a console that cannot
// attach to anything. Unreachable on the live fleet -- all 47 agents carry resident or managed, and
// `agent_sessions` has no session_mode column for the fallback to read -- but a guard that opens
// when its input is missing is decoration.
//
// WHAT THIS FILE TESTS is the decision, not the DOM: which of the two sources is believed, and what
// happens when neither says anything. The rendering around it needs a document and is covered by the
// console chooser's own tests.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(HERE, 'session-console.mjs'), 'utf8');

/** The decision under test, transcribed from session-console.mjs. */
function isResident(agent, session) {
  const normalizedSessionMode = String(
    agent?.sessionMode || session?.sessionMode || session?.session_mode || '',
  ).toLowerCase();
  return typeof agent?.consoleAvailable === 'boolean'
    ? !agent.consoleAvailable
    : normalizedSessionMode.trim() !== 'managed';
}

test('THE TRANSCRIPTION MATCHES THE MODULE, or this file tests a copy that has drifted', () => {
  // A hand-transcribed predicate is worth exactly as much as its agreement with the original. The
  // module's expression is asserted present verbatim so an edit there fails here rather than leaving
  // this file quietly testing something else.
  assert.match(SRC, /typeof agent\?\.consoleAvailable === 'boolean'/);
  assert.match(SRC, /\? !agent\.consoleAvailable/);
  assert.match(SRC, /: normalizedSessionMode\.trim\(\) !== 'managed'/);
});

test("the service's answer is believed when it sends one", () => {
  assert.equal(isResident({ consoleAvailable: true, sessionMode: 'managed' }, null), false);
  assert.equal(isResident({ consoleAvailable: false, sessionMode: 'resident' }, null), true);
});

test('the service OVERRIDES a disagreeing local mode', () => {
  // The point of asking: one source of truth, not two that usually agree. If the service says a
  // console cannot attach, a stale `sessionMode` on the same row must not offer one anyway.
  assert.equal(isResident({ consoleAvailable: false, sessionMode: 'managed' }, null), true);
  assert.equal(isResident({ consoleAvailable: true, sessionMode: 'resident' }, null), false);
});

test('WITHOUT the field, an empty mode fails CLOSED', () => {
  // The defect this closes. `=== 'resident'` made an empty mode "not resident", which offered a
  // console for an agent the service had already decided could not have one.
  for (const agent of [{}, { sessionMode: '' }, { sessionMode: '   ' }, null, undefined]) {
    assert.equal(isResident(agent, null), true, `an empty mode offered a console: ${JSON.stringify(agent)}`);
  }
});

test('…but a real managed mode still opens it', () => {
  // ANTI-VACUITY: failing closed on everything would satisfy the test above and hide every console.
  assert.equal(isResident({ sessionMode: 'managed' }, null), false);
  assert.equal(isResident({ sessionMode: 'MANAGED' }, null), false, 'case is folded');
  assert.equal(isResident(null, { sessionMode: 'managed' }), false, 'the session fallback still works');
  assert.equal(isResident(null, { session_mode: 'managed' }), false, '…in both spellings');
});

test('a non-boolean consoleAvailable is ignored rather than coerced', () => {
  // `typeof === 'boolean'` and not truthiness: a serialiser sending "" or 0 for "unknown" would
  // otherwise be read as a definite "no console", and a definite answer is exactly what it is not.
  for (const value of ['', 0, null, undefined, 'true']) {
    assert.equal(
      isResident({ consoleAvailable: value, sessionMode: 'managed' }, null), false,
      `a ${JSON.stringify(value)} consoleAvailable was treated as an answer`,
    );
  }
});

test('the FALLBACK still matches the service, because the vocabulary is still two values', () => {
  // The fallback hardcodes one name, which is only equivalent to `_normalize_session_mode` while
  // SESSION_MODES is {managed, resident}: that function maps anything outside the set to resident,
  // so with exactly two members "not managed" and "resident" are the same rule. Add a third mode and
  // they stop being the same -- silently, in the direction of offering a console that cannot attach.
  // The contract file is the same one the Python side loads, so this cannot drift from it.
  const contract = JSON.parse(readFileSync(join(HERE, '..', 'contracts', 'vocabulary.json'), 'utf8'));
  const modes = contract.sessionModes?.values;
  assert.ok(Array.isArray(modes) && modes.length, 'positive control: sessionModes not found in the contract');
  assert.deepEqual(
    [...modes].sort(), ['managed', 'resident'],
    'SESSION_MODES gained a value, so `!== "managed"` no longer means what _normalize_session_mode means',
  );
});
