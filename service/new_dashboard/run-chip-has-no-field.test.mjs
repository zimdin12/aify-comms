// The run chip cannot render, because nothing supplies the field it needs.
//
// `messageHtml` builds a "Run …" chip only when it finds a run id, and it looks for FOUR names:
// dispatchRunId, dispatch_run_id, runId, run_id. Measured against the live service on 2026-08-25:
// across 160 message rows from /messages/recent and /messages/inbox/dashboard, with 15 and 16 distinct
// keys respectively, NOT ONE key contains "run". The chip has never rendered.
//
// It is not unwired — click-dispatch.mjs has a handler for `[data-run-chip]`. So the dashboard carries
// a complete click path to a button no payload can produce. A four-name fallback reads as careful and
// is exactly what hid it: every name misses, the result is '', and an absent chip looks like "this
// message did not wake anything" rather than like a defect.
//
// NOT FIXED, deliberately, and the reason is a measurement: the link lives in dispatch_runs.message_id
// (21,189 rows populated) and that column has NO INDEX, so supplying the field costs a full scan of
// 21k rows on an endpoint polled every ~15s. Wiring it needs a schema migration first. This test
// records the state instead of leaving it invisible, and INVERTS the day someone adds the field --
// then it fails, and whoever added it is told the chip is now reachable.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { messageHtml } from './chat-render.mjs';

/** Every name messageHtml will accept as a run id. Kept here so the test states the contract. */
const ACCEPTED_RUN_ID_FIELDS = ['dispatchRunId', 'dispatch_run_id', 'runId', 'run_id'];

/** The keys a message row actually carries, measured from the live service on 2026-08-25. */
const LIVE_MESSAGE_KEYS = [
  'body', 'channel', 'dispatchRequested', 'from', 'id', 'inReplyTo', 'preview',
  'priority', 'read', 'readAt', 'source', 'subject', 'timestamp', 'to', 'type',
];

test('no field the service sends can satisfy the chip', () => {
  const overlap = ACCEPTED_RUN_ID_FIELDS.filter((f) => LIVE_MESSAGE_KEYS.includes(f));
  assert.deepEqual(
    overlap, [],
    'a message row now carries a run id — the chip is reachable, so wire it and delete this test',
  );
});

test('a real live message row renders no chip', () => {
  // The point of building the row from the measured key list rather than from a hand-written literal:
  // a literal is a guess about the payload, and guessing is what this whole class of bug is made of.
  const row = Object.fromEntries(LIVE_MESSAGE_KEYS.map((k) => [k, k === 'read' ? true : `v-${k}`]));
  const html = messageHtml(row, 'dashboard');
  assert.ok(!html.includes('data-run-chip'), 'the chip rendered from a payload that has no run id');
});

test('the chip DOES render once a run id is present, so the branch itself is sound', () => {
  // Positive control. Without it, the assertions above pass equally well if messageHtml were broken,
  // renamed, or returning an empty string — an absence proves nothing unless presence is shown too.
  const row = Object.fromEntries(LIVE_MESSAGE_KEYS.map((k) => [k, k === 'read' ? true : `v-${k}`]));
  for (const field of ACCEPTED_RUN_ID_FIELDS) {
    const html = messageHtml({ ...row, [field]: 'run_abc123' }, 'dashboard');
    assert.ok(
      html.includes('data-run-chip'),
      `messageHtml stopped accepting ${field} as a run id`,
    );
  }
});

test('the woke badge does not depend on the missing field', () => {
  // `woke` reads `!!runId || m.dispatchRequested`, and dispatchRequested IS emitted — so that half
  // works. Worth pinning: if someone "tidies" the dead runId out, the badge must not go with it.
  const row = Object.fromEntries(LIVE_MESSAGE_KEYS.map((k) => [k, k === 'read' ? true : `v-${k}`]));
  assert.ok(messageHtml({ ...row, dispatchRequested: true }, 'dashboard').includes('woke'));
  assert.ok(messageHtml({ ...row, dispatchRequested: false }, 'dashboard').includes('stored'));
});
