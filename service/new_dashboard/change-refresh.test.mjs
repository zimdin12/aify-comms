// What the change-driven refresh fetches, and when. Each test names the defect it would catch.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CHANGE_DEBOUNCE_MS, ChangeDrivenRefresh, LIVENESS_INTERVAL_MS, RETRY_AFTER_MS, SLICE_MIN_INTERVAL_MS,
} from './change-refresh.mjs';
import { SLICE_TABLES, slicesReading } from './slice-tables.mjs';

/** A clock and timers the test advances by hand, so nothing here depends on real time. */
function rig({ failing = [] } = {}) {
  let now = 1_000_000;
  let nextId = 1;
  const timers = new Map();
  const log = { full: 0, slices: [], pollSeconds: 15 };
  const refresher = new ChangeDrivenRefresh({
    fullRefresh: () => { log.full += 1; },
    refreshSlices: async (slices) => { log.slices.push([...slices]); return slices.filter((s) => failing.includes(s)); },
    pollSeconds: () => log.pollSeconds,
    now: () => now,
    timers: {
      setTimeout: (fn, ms) => { const id = nextId++; timers.set(id, { fn, at: now + ms }); return id; },
      clearTimeout: (id) => { timers.delete(id); },
    },
  });
  async function advance(ms) {
    const until = now + ms;
    for (;;) {
      const next = [...timers].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      const [id, t] = next;
      timers.delete(id);
      now = t.at;
      t.fn();
      await new Promise((resolve) => setImmediate(resolve));
    }
    now = until;
  }
  return { refresher, log, advance, timers, setNow: (value) => { now = value; }, getNow: () => now };
}

const change = (seq, tables = [], liveness = [], instance = 'svc-1') => ({ seq, instance, tables, liveness });

test('a change fetches only the slices that read its tables, once for a burst', async () => {
  const { refresher, log, advance } = rig();
  refresher.opened({ reconnected: false });
  refresher.changed(change(1, ['shared_artifacts']));
  refresher.changed(change(2, ['shared_artifacts']));
  await advance(CHANGE_DEBOUNCE_MS - 1);
  assert.deepEqual(log.slices, [], 'fetched before the debounce, so a burst is several fetches');
  await advance(1);
  assert.deepEqual(log.slices, [slicesReading(['shared_artifacts'])]);
  assert.deepEqual(log.slices[0], ['stats', 'files'].filter((s) => SLICE_TABLES[s].includes('shared_artifacts')));
  assert.equal(log.full, 0, 'a change the client can name must not refetch everything');
});

test('liveness refreshes only the slices that show an age, and at most once a minute', async () => {
  const { refresher, log, advance } = rig();
  refresher.opened({ reconnected: false });
  refresher.fullyRefreshed(1_000_000);
  refresher.changed(change(1, [], ['agents']));
  await advance(LIVENESS_INTERVAL_MS - 1);
  assert.deepEqual(log.slices, [], 'a heartbeat refetched inside the liveness interval');
  await advance(1);
  assert.equal(log.slices.length, 1);
  for (const slice of log.slices[0]) {
    assert.ok(['agents', 'sessions', 'environments'].includes(slice), `liveness refetched ${slice}, which shows no age`);
  }
  assert.ok(log.slices[0].includes('agents'));
});

test('a real change is not held behind a waiting liveness refresh', async () => {
  const { refresher, log, advance } = rig();
  refresher.opened({ reconnected: false });
  refresher.fullyRefreshed(1_000_000);
  refresher.changed(change(1, [], ['agents']));
  refresher.changed(change(2, ['agents']));
  await advance(CHANGE_DEBOUNCE_MS);
  assert.equal(log.slices.length, 1, 'a real change to agents waited for the liveness window');
  assert.ok(log.slices[0].includes('agents'));
});

test('a skipped seq, or a restarted service, refetches everything once', async () => {
  const { refresher, log, advance } = rig();
  refresher.opened({ reconnected: false });
  refresher.changed(change(1, ['messages']));
  refresher.changed(change(3, ['messages']));
  assert.equal(log.full, 1, 'a missed event was not recovered');
  await advance(CHANGE_DEBOUNCE_MS);
  assert.deepEqual(log.slices, [], 'the partial fetch pending at the gap survived the full refetch');
  refresher.changed(change(4, ['messages']));
  assert.equal(log.full, 1, 'the count did not start over after the recovery');
  await advance(CHANGE_DEBOUNCE_MS);
  assert.deepEqual(log.slices, [slicesReading(['messages'])], 'the next change after a recovery was not fetched');
  refresher.changed(change(1, ['messages'], [], 'svc-2'));
  assert.equal(log.full, 2, 'a restarted service was read as a continuing count');
});

test('the first change on a connection sets the baseline without a full refetch', () => {
  const { refresher, log } = rig();
  refresher.opened({ reconnected: false });
  assert.equal(refresher.covering, false, 'a named event would be ignored before any change arrived');
  refresher.changed(change(41, ['messages']));
  assert.equal(log.full, 0);
  assert.equal(refresher.covering, true);
});

test('the timed poll runs only while the socket is down', async () => {
  const { refresher, log, advance } = rig();
  refresher.armPoll();
  await advance(15_000);
  assert.equal(log.full, 1, 'a disconnected dashboard stopped polling');
  refresher.opened({ reconnected: false });
  await advance(120_000);
  assert.equal(log.full, 1, 'the poll kept running with the socket up');
  refresher.armPoll();
  await advance(120_000);
  assert.equal(log.full, 1, 're-arming from a settings change restarted the poll while connected');
  log.pollSeconds = 30;
  refresher.closed();
  await advance(29_999);
  assert.equal(log.full, 1, 'the poll ignored the operator interval');
  await advance(1);
  assert.equal(log.full, 2);
});

test('stats is held to its own floor', async () => {
  const { refresher, log, advance } = rig();
  refresher.opened({ reconnected: false });
  refresher.fullyRefreshed(1_000_000);
  refresher.changed(change(1, ['channels']));
  await advance(CHANGE_DEBOUNCE_MS);
  assert.ok(!log.slices.flat().includes('stats'), 'stats refetched inside its floor');
  await advance(SLICE_MIN_INTERVAL_MS.stats);
  assert.ok(log.slices.flat().includes('stats'), 'a stats change was dropped rather than deferred');
});

test('a slice whose fetch failed is tried again', async () => {
  const { refresher, log, advance } = rig({ failing: ['files'] });
  refresher.opened({ reconnected: false });
  refresher.changed(change(1, ['shared_artifacts']));
  await advance(CHANGE_DEBOUNCE_MS);
  const first = log.slices.length;
  await advance(RETRY_AFTER_MS);
  assert.ok(log.slices.length > first, 'a failed slice was never fetched again');
  assert.ok(log.slices.at(-1).includes('files'));
});

test('a change that arrives while a full refresh is fetching is still fetched afterwards', async () => {
  const { refresher, log, advance, setNow } = rig();
  refresher.opened({ reconnected: false });
  refresher.changed(change(1, []));
  setNow(2_000_000);
  const startedAt = 2_000_000;
  setNow(2_000_100);
  refresher.changed(change(2, ['messages']));
  refresher.fullyRefreshed(startedAt);
  await advance(CHANGE_DEBOUNCE_MS);
  assert.deepEqual(log.slices, [slicesReading(['messages'])], 'the full refresh swallowed a change newer than its start');
});

test('a change older than a full refresh is not fetched again', async () => {
  const { refresher, log, advance, setNow } = rig();
  refresher.opened({ reconnected: false });
  refresher.changed(change(1, ['messages']));
  setNow(1_000_100);
  refresher.fullyRefreshed(1_000_050);
  await advance(CHANGE_DEBOUNCE_MS);
  assert.deepEqual(log.slices, [], 'a full refresh left an older change pending');
});

test('nothing is fetched on changes while disconnected', async () => {
  const { refresher, log, advance } = rig();
  refresher.changed(change(1, ['messages']));
  await advance(CHANGE_DEBOUNCE_MS);
  assert.deepEqual(log.slices, []);
});
