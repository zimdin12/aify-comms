// The sentence a dead session's Console tab shows, and the four answers it must keep apart.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
  deadConsoleCauseText,
  deadConsoleFailureLine,
  fillDeadConsoleCause,
} from './dead-console-cause.mjs';

const dead = (extra) => ({ ok: true, live: false, historical: true, ...extra });

test('a signalled death is named, because that is the answer', () => {
  assert.equal(deadConsoleCauseText(dead({ exitSignal: 'SIGKILL', exitCode: null })),
    'Killed by SIGKILL.');
});

test('a non-zero exit carries its number', () => {
  assert.equal(deadConsoleCauseText(dead({ exitCode: 137 })), 'Exited with code 137.');
  assert.equal(deadConsoleCauseText(dead({ exitCode: 1 })), 'Exited with code 1.');
});

test('a CLEAN exit prints, and does not read as silence', () => {
  // The trap every layer of this feature had to avoid: `if (exitCode)` drops zero, which is the most
  // common exit there is. Under a session that should still be running, "exited cleanly" is itself
  // a diagnosis and a different one from "we do not know".
  assert.equal(deadConsoleCauseText(dead({ exitCode: 0 })), 'Exited cleanly (code 0).');
  assert.notEqual(deadConsoleCauseText(dead({ exitCode: 0 })),
    deadConsoleCauseText(dead({ exitCode: null })));
});

test('nothing recorded says so, rather than inventing a cause', () => {
  // An older bridge sends no exit fields at all. Guessing here would be the exact failure the column
  // was added to end.
  assert.equal(deadConsoleCauseText(dead({})), 'It did not report why it ended.');
  assert.equal(deadConsoleCauseText(dead({ exitCode: null, exitSignal: '' })),
    'It did not report why it ended.');
});

test('a LIVE console gets no sentence at all', () => {
  // The control. This text belongs on a dead card; putting it on a running one would be a lie in the
  // most visible place on the page.
  assert.equal(deadConsoleCauseText({ live: true, historical: false, exitCode: 0 }), '');
  assert.equal(deadConsoleCauseText({ live: false, historical: false }), '');
  assert.equal(deadConsoleCauseText(null), '');
});

test('the failure line is collapsed and capped', () => {
  assert.equal(deadConsoleFailureLine({ failureLine: '  spread   over\nlines  ' }), 'spread over lines');
  const long = 'x'.repeat(400);
  const trimmed = deadConsoleFailureLine({ failureLine: long }, 40);
  assert.equal(trimmed.length, 40);
  assert.ok(trimmed.endsWith('…'), 'a truncated line must say it was truncated');
  assert.equal(deadConsoleFailureLine({}), '');
});

test('the filler asks the endpoint and writes both halves', async () => {
  const asked = [];
  const el = { textContent: '' };
  const text = await fillDeadConsoleCause(el, 'sc-claude', {
    api: async (path) => {
      asked.push(path);
      return dead({ exitSignal: 'SIGTERM', failureLine: 'hermes gateway did not become ready' });
    },
  });
  assert.deepEqual(asked, ['/agents/sc-claude/console?lines=1']);
  assert.match(text, /Killed by SIGTERM\./);
  assert.match(text, /Last output: hermes gateway did not become ready/);
  assert.equal(el.textContent.trim(), text);
});

test('a failed fetch leaves the card exactly as it was', async () => {
  // This decorates a card that is already correct without it. Replacing a working message with an
  // error would make the dashboard worse in the moment the operator most needs it to work.
  const el = { textContent: 'ORIGINAL' };
  const text = await fillDeadConsoleCause(el, 'sc-claude', {
    api: async () => { throw new Error('Failed to fetch'); },
  });
  assert.equal(text, '');
  assert.equal(el.textContent, 'ORIGINAL');
});

test('a missing element or agent id is a no-op, not a throw', async () => {
  assert.equal(await fillDeadConsoleCause(null, 'a', { api: async () => dead({}) }), '');
  assert.equal(await fillDeadConsoleCause({ textContent: '' }, '', { api: async () => dead({}) }), '');
  assert.equal(await fillDeadConsoleCause({ textContent: '' }, 'a', {}), '');
});
