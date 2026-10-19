"""The status shadow's disagreement evidence (S2c-E E1): every counted disagreement of an evidence owner is kept as one
immutable item or counted as a gap, persisted with its window's counts, and read back exactly once.

Each test names the control from the design review it witnesses: what an item holds, the capture failures, the
window's failure and recovery history, rotation, completeness, the frontier-bound read, and expiry.
"""
import asyncio
from contextlib import closing
import json
import pathlib
import sqlite3
from unittest import mock

from service.api_core import partial_status_shadow as shadow, shadow_evidence as ev, shadow_evidence_store as store
from service.api_core import shadow_windows as windows, view_owners as vo
from service.status_engine import StatusInputs
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import row as env_row

MACHINE = 'linux:test-host'
ROW = {'id': 'a', 'machine_id': MACHINE, 'status': 'online'}
V, V2 = windows.Versions(comms='aaaa1111'), windows.Versions(comms='bbbb2222')
LIVE, PROMOTION, READINESS = vo.LIVE_COUNTS, vo.PROMOTION, vo.READINESS


def at(seconds):
    return f'2026-10-19T10:{seconds // 60:02d}:{seconds % 60:02d}Z'


def inputs(**extra):
    return StatusInputs(mode='managed', alive=True, in_turn=False, awaiting_input=False, worker_present=True,
                        env_reachable=True, disabled=False, bridge_stale=False, has_live_session=True, **extra)


def body(generation=1, publication=1, instance='i1', state='idle', machine=MACHINE, pad=0, agents=('a',),
         kind='snapshot'):
    records = [dict(env_row(agent), state=state, **({'pad': 'x' * pad} if pad else {})) for agent in agents]
    value = dict(kind=kind, machineId=machine, instance=instance, generation=generation, incarnationId='boot',
                 publication=publication, inputs={}, agents=records, removed=[])
    return dict(value, complete=True) if kind == 'snapshot' else value


async def _wrap(connect, proxy):
    return proxy(await connect())


class _Evidence(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.recorder, self.mirror = shadow.Recorder(), shadow.Mirror()
        self.now = at(0)
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(shadow, 'mirror', self.mirror),
                  mock.patch.object(shadow, 'clock', side_effect=lambda: self.now)):
            p.start(); self.addCleanup(p.stop)

    def wait(self, coroutine):
        return asyncio.run(coroutine)

    def feed(self, applied, **kwargs):
        self.mirror.feed(shadow.prepare(body(**kwargs), applied))

    def disagree(self, when, source='refresh', **extra):
        """comms says offline where the fresh record says idle: one counted word disagreement."""
        self.now = when
        shadow.observe(shadow.bind(), ROW, inputs(**extra), 'offline', source)

    def taken(self):
        items, gaps = self.recorder.evidence.take()
        return [json.loads(i.payload) for i in items], gaps

    def disagreed(self, owner=shadow.WORD, site='refresh'):
        return sum(n for (o, _, s, name), n in self.recorder._pending.items()
                   if (o, s, name) == (owner, site, 'partialDisagreed'))

    def sql(self, query, params=()):
        with closing(sqlite3.connect(self._db_path)) as db:
            return db.execute(query, params).fetchall()

    def stored(self):
        """{window id: [(seq, evalAt)]}, every persisted item."""
        rows = {}
        for window, seq, payload in self.sql('SELECT window_id, seq, payload FROM status_shadow_evidence ORDER BY seq'):
            rows.setdefault(window, []).append((seq, json.loads(payload)['evalAt']))
        return rows

    def windows_(self):
        return self.sql('SELECT id, opened_at, closed_at, close_reason, flushed_seq, evidence_rows FROM '
                        'status_shadow_windows ORDER BY id')

    def page(self, window, **kwargs):
        async def read():
            from service.db import get_db
            db = await get_db()
            try:
                return await store.read_page(db, window, **kwargs)
            finally:
                await db.close()
        return self.wait(read())


class ItemTest(_Evidence):
    def test_three_same_key_disagreements_are_three_items_with_their_own_coordinates(self):
        self.feed(at(0), generation=1, publication=1)
        self.disagree(at(10))
        self.feed(at(20), generation=1, publication=2)
        self.disagree(at(25))
        self.feed(at(40), generation=2, publication=3)
        self.disagree(at(50))
        items, gaps = self.taken()
        self.assertEqual((len(items), gaps, self.disagreed()), (3, {}, 3))
        self.assertEqual([(i['event']['seq'], i['evalAt'], i['env']['records'][0]['origin']['publication'],
                           i['env']['records'][0]['origin']['appliedAt']) for i in items],
                         [(1, at(10), 1, at(0)), (2, at(25), 2, at(20)), (3, at(50), 3, at(40))],
                         'every evaluation, the middle one included, with its own clock and lineage')

    def test_the_item_is_frozen_at_capture_whatever_happens_before_the_flush(self):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, at(0)))
        held = body()
        self.mirror.feed(shadow.prepare(held, at(0)))
        self.feed(at(0), instance='i2', agents=('someone-else',))  # a non-holder on the same machine
        held['agents'][0]['state'] = 'mutated'  # the caller's dict, after application
        self.disagree(at(10))
        self.feed(at(20), instance='i2', generation=9, publication=9, agents=('someone-else',))  # a later publication
        self.wait(asyncio.sleep(0))
        self.wait(owner.tick(V, at(30)))
        [(payload,)] = self.sql('SELECT payload FROM status_shadow_evidence')
        item = json.loads(payload)
        self.assertEqual(json.loads(item['env']['records'][0]['raw'])['state'], 'idle')
        self.assertEqual([(p['instance'], p['generation'], p['publication'], p['holds'])
                          for p in item['env']['publishers']], [('i1', 1, 1, True), ('i2', 1, 1, False)],
                         'the census the candidate read, not the view at the flush')

    def test_the_word_keeps_status_inputs_real_values_not_the_projection(self):
        self.feed(at(0))
        self.disagree(at(10), config_defect='no wake path')
        [item], _ = self.taken()
        self.assertEqual(item['comms']['inputs']['config_defect'], 'no wake path')
        self.assertEqual((item['today'], item['candidate'], item['comms']['word']), ('offline', 'online', 'offline'))

    def test_the_gate_keeps_its_held_status_apart_from_the_association_status(self):
        self.feed(at(0))
        self.now = at(10)
        vo.observe_live_count(dict(ROW, status='online'), False, 'gate', 'offline (no wake path)')
        [item], _ = self.taken()
        self.assertEqual((item['comms']['held'], item['comms']['live'], item['comms']['association']['status']),
                         ('offline (no wake path)', False, 'online'))
        self.assertEqual((item['today'], item['candidate']), (False, True))

    def test_promotion_keeps_the_sites_status_pair_and_its_run(self):
        self.feed(at(0))
        self.now = at(10)
        vo.observe_promotion(ROW, 'idle', 'idle', {'activeRun': {'runId': 'r1', 'status': 'running'}}, 'serialize')
        [item], _ = self.taken()
        self.assertEqual(item['comms'], dict(base='idle', effective='idle', activeRun=dict(runId='r1', status='running'),
                                             association=dict(id='a', machineId=MACHINE, status='online')))

    def test_the_env_side_is_a_per_publisher_vector_and_the_wrapper_is_a_pin(self):
        self.feed(at(0), instance='i1', generation=3, publication=7)
        self.feed(at(5), instance='i2', generation=4, publication=2)
        self.feed(at(5), machine='other-host', instance='i9')
        self.now = at(10)
        vo.observe_live_count(ROW, False, 'gate', 'offline')  # a fresh conflict: row 4's `unknown`, which is live
        [item], _ = self.taken()
        self.assertEqual([(r['instance'], r['origin']['generation'], r['origin']['publication'], r['origin']['envBuild'])
                          for r in item['env']['records']], [('i1', 3, 7, 'not-published'), ('i2', 4, 2, 'not-published')])
        self.assertEqual([p['instance'] for p in item['env']['publishers']], ['i1', 'i2'], 'only the agent\'s machine')
        pin = json.loads((pathlib.Path(ev.__file__).resolve().parents[2] / 'mcp/stdio/package.json').read_text())
        self.assertEqual(item['versions']['wrapperPin'],
                         dict(sha=pin['dependencies']['aify-wrapper'].rsplit('#', 1)[1], label='pinned'))
        self.assertEqual(item['versions']['runningWrapper'], 'unknown')

    def test_a_record_keeps_the_origin_it_was_applied_with_not_its_publishers_later_one(self):
        self.feed(at(0), generation=1, publication=1)
        self.feed(at(20), generation=1, publication=2, kind='changes', agents=('b',))  # leaves a's record alone
        self.disagree(at(25))
        [item], _ = self.taken()
        [record] = item['env']['records']
        self.assertEqual((record['origin']['publication'], record['origin']['appliedAt']), (1, at(0)))
        self.assertEqual([(p['publication'], p['dataAppliedAt']) for p in item['env']['publishers']], [(2, at(20))])

    def test_the_census_says_what_the_candidate_could_not_read_and_what_it_never_consulted(self):
        self.assertEqual(ev.census(shadow.Binding(None, None, True), (MACHINE, 'a')),
                         (dict(consulted=True, at=None, lost=True), ['env-view']))
        self.assertEqual(ev.census(None, (MACHINE, 'a')), (dict(consulted=False), []))
        self.feed(at(0))
        self.now = at(10)
        vo.observe_promotion(ROW, 'idle', 'working', {}, 'preflight')  # today promotes with no running run
        [item], _ = self.taken()
        self.assertEqual((item['today'], item['candidate'], item['env'], item['evalAt'], item['incomplete']),
                         (True, False, dict(consulted=False), None, []))

    def test_a_record_over_the_raw_limit_is_kept_as_an_incomplete_item(self):
        self.feed(at(0), pad=shadow.RAW_LIMIT)
        self.disagree(at(10))
        items, _ = self.recorder.evidence.take()
        self.assertEqual((json.loads(items[0].payload)['incomplete'], items[0].complete), (['env-raw'], False))


class CaptureFailureTest(_Evidence):
    def record(self, evidence, owner=LIVE):
        self.recorder.record_owner(owner, '-', 'gate', False, True, evidence=evidence)

    def test_each_failure_is_a_gap_and_every_disagreement_is_an_item_or_a_gap(self):
        good = lambda: dict(agentId='a', evalAt=at(1), incomplete=[])  # noqa: E731
        self.record(lambda: 1 / 0)
        self.record(lambda: dict(good(), bad={1, 2}))
        with mock.patch.object(ev, 'ITEM_BYTES', 10):
            self.record(good)
        with mock.patch.object(ev, 'PENDING_ITEMS', 1):
            self.record(good)
            self.record(good)
        self.record(None)  # an instrumented site that passed no builder
        items, gaps = self.recorder.evidence.take()
        self.assertEqual(gaps, {(LIVE, '-', 'gate', 'builder-error'): 2, (LIVE, '-', 'gate', 'encode-error'): 1,
                                (LIVE, '-', 'gate', 'item-too-large'): 1, (LIVE, '-', 'gate', 'pending-overflow'): 1})
        self.assertEqual(len(items) + sum(gaps.values()), self.disagreed(LIVE, 'gate'))
        self.assertEqual(len(items), 1)

    def test_a_pending_byte_overflow_is_a_gap(self):
        good = lambda: dict(agentId='a', evalAt=at(1), incomplete=[])  # noqa: E731
        size = len(ev.encode(dict(good(), event=dict(bootId=ev.BOOT_ID, seq=1))))
        with mock.patch.object(ev, 'PENDING_BYTES', size + 1):
            self.record(good)
            self.record(good)
        items, gaps = self.recorder.evidence.take()
        self.assertEqual((len(items), gaps), (1, {(LIVE, '-', 'gate', 'pending-overflow'): 1}))

    def test_an_owner_outside_e1_keeps_nothing_and_its_count_stands(self):
        self.recorder.record_owner(READINESS, '-', 'claim', False, True, evidence=lambda: 1 / 0)
        self.assertEqual(self.recorder.evidence.take(), ((), {}))
        self.assertEqual(self.disagreed(READINESS, 'claim'), 1)


class WindowHistoryTest(_Evidence):
    def owner(self):
        return windows.ShadowWindows(self.recorder)

    def fail_once(self, owner):
        connect = owner._connect

        async def unavailable():
            owner._connect = connect
            raise OSError('database unavailable')
        owner._connect = unavailable

    def test_a_disagreement_before_the_window_opens_is_in_no_window(self):
        self.feed(at(0))
        self.disagree(at(1))
        owner = self.owner()
        self.wait(owner.start(V, at(2)))
        self.wait(owner.tick(V, at(30)))
        self.assertEqual(self.stored(), {})

    def test_the_recovery_batch_stays_in_the_failing_window_0_30_45_60(self):
        owner = self.owner()
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.disagree(at(10))
        self.fail_once(owner)
        with self.assertRaises(OSError):
            self.wait(owner.tick(V, at(30)))
        self.disagree(at(45))
        self.wait(owner.tick(V, at(60)))
        (w, opened, closed, reason, *_), (w2, opened2, closed2, *_) = self.windows_()
        self.assertEqual((opened, closed, reason, opened2, closed2), (at(0), at(60), 'flush-lost', at(60), None))
        self.assertEqual(self.stored(), {w: [(2, at(45))]}, 'the item from 45 in W [0, 60]; the one taken at 30 lost')
        self.assertEqual(self.sql("SELECT count FROM status_shadow_window_counts WHERE window_id = ? AND "
                                  "name = 'partialDisagreed'", (w,)), [(1,)])

    def test_repeated_failures_lose_each_batch_and_the_first_success_closes_the_window(self):
        owner = self.owner()
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        for when in (10, 40):
            self.disagree(at(when))
            self.fail_once(owner)
            with self.assertRaises(OSError):
                self.wait(owner.tick(V, at(when + 20)))
        self.disagree(at(70))
        self.wait(owner.tick(V, at(90)))
        (w, _, closed, reason, *_), (w2, *_) = self.windows_()
        self.assertEqual((closed, reason), (at(90), 'flush-lost'))
        self.assertEqual(self.stored(), {w: [(3, at(70))]})

    def test_a_crash_before_recovery_closes_at_the_last_flush_and_invents_no_loss(self):
        owner = self.owner()
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.disagree(at(10))
        self.wait(owner.tick(V, at(30)))
        self.disagree(at(40))
        self.fail_once(owner)
        with self.assertRaises(OSError):
            self.wait(owner.tick(V, at(60)))
        self.wait(windows.ShadowWindows(shadow.Recorder()).start(V, at(90)))  # the next process
        (w, _, closed, reason, flushed_seq, rows), (w2, opened2, closed2, *_) = self.windows_()
        self.assertEqual((closed, reason, flushed_seq, rows), (at(30), 'service-restart', 1, 1))
        self.assertEqual(self.stored(), {w: [(1, at(10))]})
        self.assertEqual((opened2, closed2), (at(90), None))

    def test_a_close_that_fails_after_the_commit_keeps_the_batch_exactly_once(self):
        from service.db import get_db
        failures = [1]

        class FailOneClose:
            def __init__(self, connection): self.connection = connection
            async def execute(self, sql, params=()): return await self.connection.execute(sql, params)
            async def commit(self): await self.connection.commit()
            async def close(self):
                await self.connection.close()
                if failures:
                    failures.pop()
                    raise OSError('injected close failure')
        owner = self.owner()
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.disagree(at(10))
        connect, owner._connect = owner._connect, lambda: _wrap(get_db, FailOneClose)
        with self.assertRaisesRegex(OSError, 'injected'):
            self.wait(owner.tick(V, at(30)))
        owner._connect = connect
        self.disagree(at(45))
        self.wait(owner.tick(V, at(60)))
        [(w, _, closed, reason, flushed_seq, rows)] = self.windows_()
        self.assertEqual((closed, reason, flushed_seq, rows), (None, None, 2, 2), 'no flush-lost, no replay')
        self.assertEqual(self.stored(), {w: [(1, at(10)), (2, at(45))]})

    def test_a_refused_evidence_insert_persists_nothing_of_the_batch(self):
        from service.db import get_db

        class RefuseEvidence:
            def __init__(self, connection): self.connection = connection
            async def execute(self, sql, params=()):
                if sql.startswith('INSERT INTO status_shadow_evidence '):
                    raise OSError('injected insert refusal')
                return await self.connection.execute(sql, params)
            async def commit(self): await self.connection.commit()
            async def rollback(self): await self.connection.rollback()
            async def close(self): await self.connection.close()
        owner = self.owner()
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.disagree(at(10))
        connect, owner._connect = owner._connect, lambda: _wrap(get_db, RefuseEvidence)
        with self.assertRaisesRegex(OSError, 'injected'):
            self.wait(owner.tick(V, at(30)))
        owner._connect = connect
        self.assertEqual((self.stored(), self.sql('SELECT * FROM status_shadow_window_counts'),
                          self.sql('SELECT * FROM status_shadow_evidence_gaps')), ({}, [], []),
                         'counts, items and gaps fail together')
        self.wait(owner.tick(V, at(60)))
        self.assertEqual(self.windows_()[0][3], 'flush-lost')


class RotationTest(_Evidence):
    def test_the_bounds_leave_room_for_one_whole_batch(self):
        self.assertLessEqual(store.ROTATE_ROWS + ev.PENDING_ITEMS, 50_000)
        self.assertLessEqual(store.ROTATE_BYTES + ev.PENDING_BYTES, 64 * 1024 * 1024)
        self.assertLessEqual(ev.ITEM_BYTES, ev.PENDING_BYTES)

    def run_batches(self, batches):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, at(0)))
        clock = 0
        for size in batches:
            for _ in range(size):
                clock += 1
                self.feed(at(clock))
                self.disagree(at(clock))
            clock += 1
            self.wait(owner.tick(V, at(clock)))
        return self.windows_()

    def assert_membership(self):
        for window, opened, closed, *_ in self.windows_():
            for seq, eval_at in self.stored().get(window, []):
                self.assertTrue(opened <= eval_at and (closed is None or eval_at <= closed), (window, seq, eval_at))

    def test_rows_rotate_on_the_totals_after_the_append(self):
        with mock.patch.object(store, 'ROTATE_ROWS', 3), mock.patch.object(ev, 'PENDING_ITEMS', 2):
            rows = self.run_batches([2, 2, 1])
        self.assertEqual([(r[3], r[5]) for r in rows], [('rotation', 4), (None, 1)],
                         '2 then 4 > 3 rotates at that tick, holding 4 <= 3 + 2; the next batch starts W\' empty')
        self.assert_membership()

    def test_bytes_rotate_on_the_totals_after_the_append(self):
        self.feed(at(0))
        self.disagree(at(0))
        size = len(self.recorder.evidence.take()[0][0].payload)
        with mock.patch.object(store, 'ROTATE_BYTES', size + size // 2):
            rows = self.run_batches([1, 1, 1])
        self.assertEqual([(r[3], r[5]) for r in rows], [('rotation', 2), (None, 1)])
        self.assert_membership()

    def test_a_day_old_window_rotates_at_its_tick(self):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, '2026-10-19T10:00:00Z'))
        self.wait(owner.tick(V, '2026-10-20T09:59:59Z'))
        self.wait(owner.tick(V, '2026-10-20T10:00:00Z'))
        self.assertEqual([r[3] for r in self.windows_()], ['rotation', None])


class ReadTest(_Evidence):
    def history(self):
        """W1: 2 complete word items, 1 incomplete, a readiness disagreement; closed by a version change."""
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.disagree(at(1))
        self.disagree(at(2))
        self.feed(at(3), pad=shadow.RAW_LIMIT)
        self.disagree(at(4), source='engine-status')
        self.recorder.record_owner(READINESS, '-', 'claim', False, True)
        with mock.patch.object(ev, 'ITEM_BYTES', 10):
            self.disagree(at(5), source='cache-broadcast')  # a gap, not an item
        self.wait(owner.tick(V, at(10)))
        self.wait(owner.tick(V2, at(20)))
        return owner

    def site(self, page, owner, site):
        return next(r for r in page['coverage'] if r['owner'] == owner)['sites'].get(site)

    def test_completeness_is_healthy_incomplete_or_uninstrumented(self):
        self.history()
        page = self.page(1)
        self.assertEqual(self.site(page, shadow.WORD, 'refresh'), dict(
            disagreed=2, completeRows=2, incompleteRows=0, missing={}, partitionHolds=True, complete=True))
        self.assertEqual(self.site(page, shadow.WORD, 'engine-status'), dict(
            disagreed=1, completeRows=0, incompleteRows=1, missing={}, partitionHolds=True, complete=False))
        self.assertEqual(self.site(page, shadow.WORD, 'cache-broadcast'), dict(
            disagreed=1, completeRows=0, incompleteRows=0, missing={'item-too-large': 1}, partitionHolds=True,
            complete=False))
        readiness = next(r for r in page['coverage'] if r['owner'] == READINESS)
        self.assertEqual((readiness['instrumentation'], readiness['sites']['claim']['complete']),
                         ('not-instrumented', False))
        busy = [r for r in page['coverage'] if r['owner'] == 'busy']
        self.assertEqual([(r['instrumentation'], r['sites']) for r in busy], [('not-instrumented', {})] * 2,
                         'an E2 owner with no disagreements is not-instrumented, never an empty complete')

    def test_an_open_or_flush_lost_window_is_never_complete(self):
        owner = self.history()
        self.feed(at(21))  # a record within the raw limit again
        self.disagree(at(25))
        self.wait(owner.tick(V2, at(30)))  # W2 open
        open_page = self.page(2)
        self.assertEqual((open_page['window']['provisional'], self.site(open_page, shadow.WORD, 'refresh')['complete']),
                         (True, False))
        self.disagree(at(35))
        connect = owner._connect

        async def unavailable():
            owner._connect = connect
            raise OSError('lost')
        owner._connect = unavailable
        with self.assertRaises(OSError):
            self.wait(owner.tick(V2, at(40)))
        self.wait(owner.tick(V2, at(50)))
        lost = self.page(2)
        self.assertEqual((lost['window']['closeReason'], self.site(lost, shadow.WORD, 'refresh')),
                         ('flush-lost', dict(disagreed=1, completeRows=1, incompleteRows=0, missing={},
                                             partitionHolds=True, complete=False)))

    def test_pages_enumerate_the_first_pages_frontier_exactly_once(self):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        for when in range(1, 6):
            self.disagree(at(when))
        self.wait(owner.tick(V, at(10)))
        first = self.page(1, limit=2)
        self.assertEqual((first['frontier'], [i['event']['seq'] for i in first['items']], first['hasMore']),
                         (5, [1, 2], True))
        self.disagree(at(11))
        self.disagree(at(12))
        self.wait(owner.tick(V, at(20)))  # appends 6 and 7 between the pages
        seen, after = [1, 2], first['nextAfter']
        while True:
            page = self.page(1, after=after, limit=2, frontier=first['frontier'])
            seen += [i['event']['seq'] for i in page['items']]
            after = page['nextAfter']
            if not page['hasMore']:
                break
        self.assertEqual(seen, [1, 2, 3, 4, 5])
        self.assertEqual(self.page(1, limit=200)['frontier'], 7)

    def test_a_filtered_read_ends_when_no_row_sits_at_the_frontier(self):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(V, at(0)))
        self.feed(at(0))
        self.now = at(1)
        vo.observe_live_count(ROW, False, 'gate', 'offline')  # seq 1
        self.disagree(at(2))  # seq 2, the frontier, another owner
        self.wait(owner.tick(V, at(10)))
        page = self.page(1, owner=LIVE, limit=1)
        self.assertEqual(([i['event']['seq'] for i in page['items']], page['hasMore'], page['frontier']), ([1], False, 2))

    def test_an_expired_window_leaves_a_tombstone_and_the_open_one_stays(self):
        self.history()
        self.assertEqual(self.wait(windows.expire_windows('2026-11-02T10:00:19Z')), 0, 'not yet 14 days')
        self.assertEqual(self.wait(windows.expire_windows('2026-11-03T10:00:00Z')), 1)
        page = self.page(1)
        self.assertEqual((page['window']['expired'], page['items'], page['hasMore'], page['frontier']),
                         (True, [], False, 3))
        self.assertEqual(self.site(page, shadow.WORD, 'refresh'), dict(
            disagreed=2, completeRows=2, incompleteRows=0, missing={}, partitionHolds=True, complete=False))
        for table in ('status_shadow_evidence', 'status_shadow_evidence_gaps', 'status_shadow_window_counts'):
            self.assertEqual(self.sql(f'SELECT COUNT(*) FROM {table} WHERE window_id = 1'), [(0,)], table)
        self.assertEqual([r[0] for r in self.windows_()], [2], 'the open window is never expired')
        self.assertIsNone(self.page(99))

    def test_the_route_serves_a_page_and_refuses_an_unknown_window(self):
        self.history()
        response = self.client.get('/api/v1/agent-state/shadow-evidence', params=dict(window=1, limit=1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['frontier'], len(response.json()['items'])), (3, 1))
        self.assertEqual(self.client.get('/api/v1/agent-state/shadow-evidence', params=dict(window=99)).status_code, 404)

    def test_a_window_from_before_evidence_is_not_instrumented(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute("INSERT INTO status_shadow_windows (comms_build, opened_at, flushed_at, closed_at, close_reason) "
                       "VALUES ('old', ?, ?, ?, 'version-change')", (at(0), at(30), at(30)))
            db.execute("INSERT INTO status_shadow_window_counts VALUES (1, ?, '-', 'refresh', 'partialDisagreed', 4)",
                       (shadow.WORD,))
            db.commit()
        page = self.page(1)
        word = next(r for r in page['coverage'] if r['owner'] == shadow.WORD)
        self.assertEqual((word['instrumentation'], word['sites']['refresh']['partitionHolds'],
                          word['sites']['refresh']['complete']), ('not-instrumented', False, False))
