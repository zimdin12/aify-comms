"""S2c-E E2: the evidence of busy, send-time queue, claim and worker readiness, and the windows' own owner sets.

Each test names the obligation it holds (design rev2, D1-D4). Items are read through the real recorder, buffer and
encoder, so a value that cannot be named becomes a counted gap exactly as it would live.
"""
import asyncio
from dataclasses import asdict, replace
import json
import math
from unittest import mock
import unittest

import aiosqlite

from service.api_core import busy_consumers as bc, channel_delivery as cd, claim_gating as cg, comms_view as cv
from service.api_core import delivery_turns as dt, partial_status_shadow as shadow, shadow_evidence as ev
from service.api_core import shadow_evidence_store as store, shadow_windows as windows, view_owners as vo
from service.api_core.claim_gating import BusyDecision
from service.db import E1_EVIDENCE_OWNERS, _migrate_status_shadow_windows
from service.tests import test_shadow_evidence as e1, test_view_owners as tvo

ROW = {'id': 'a', 'machine_id': 'm', 'status': 'online'}
NOW, FRESH = tvo.NOW, '2026-10-19T00:09:50Z'
TODAY_FREE = BusyDecision(False, receipt=dict(read='no-row'))
E2_OWNERS = sorted(ev.EVIDENCE_OWNERS)


def record(lifetime, clock, busy_if):
    return shadow.Record('a', lifetime, 'working', (), clock, busy_if, ('running', 'yes'), b'{"r":1}',
                         (1, 'boot', 1, FRESH))


class _Busy(unittest.TestCase):
    """A real Mirror, fold and recorder at a fixed clock. The agent is held by two publishers, fed i2 first, so a
    holder joined by position instead of by publisher would swap their facts."""

    def setUp(self):
        self.recorder, self.mirror, self.fold = shadow.Recorder(), shadow.Mirror(), dt.DeliveryTurns()
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(shadow, 'mirror', self.mirror),
                  mock.patch.object(shadow, 'clock', return_value=NOW), mock.patch.object(dt, 'FOLD', self.fold)):
            p.start(); self.addCleanup(p.stop)
        self.mirror.feed(shadow.Projection(('m', 'i2'), 'snapshot', 1, FRESH, (record('L2', (True, 0, 1), (True, True)),), ()))
        self.mirror.feed(shadow.Projection(('m', 'i1'), 'snapshot', 1, FRESH,
                                           (record('L1', (False, 0, 1), (False, False)),), ()))

    def observe(self, decision=TODAY_FREE):
        return asyncio.run(cv.observe_busy(None, ROW, decision, 'claim'))

    def items(self):
        items, gaps = self.recorder.evidence.take()
        return [json.loads(i.payload) for i in items], gaps

    def disagreed(self, owner):
        return sum(n for (o, _, _, name), n in self.recorder._pending.items() if (o, name) == (owner, 'partialDisagreed'))


class BusyItemTest(_Busy):
    def test_d1_unknown_facts_encode_as_unknown_and_their_known_twin_as_values(self):
        self.observe()  # the fold knows nothing of the delivery turn
        unknown, gaps = self.items()
        self.assertEqual((len(unknown), gaps), (2, {}), 'strict and P-1 each disagree: today free, a fresh busy record')
        busy = unknown[0]['candidateInputs']['busy']
        self.assertEqual(busy['delivery'], dict.fromkeys(('open', 'run_id', 'started_at', 'touched_at', 'owner'),
                                                         'unknown'))
        closed = next(h for h in busy['holders'] if h['publisher'] == ['m', 'i1'])
        self.assertEqual((closed['expiry'], closed['advancedAt']), ('unknown', 'unknown'), 'a closed turn has neither')
        self.fold._turns[('m', 'a')] = dt.CLOSED
        self.observe()
        known, _ = self.items()
        self.assertEqual(known[0]['candidateInputs']['busy']['delivery'],
                         dict(open=False, run_id='', started_at=None, touched_at=None, owner=''))

    def test_d1_a_nan_is_an_encode_error_gap_and_the_count_stands(self):
        self.fold._turns[('m', 'a')] = dt.DeliveryTurn(False, '', math.nan, None, '')
        self.observe()
        items, gaps = self.items()
        self.assertEqual((items, gaps), ([], {('busy', law, 'claim', 'encode-error'): 1 for law in cv.LAWS}))
        self.assertEqual(self.disagreed('busy'), 2)

    def test_d2_each_holder_keeps_its_own_publisher_and_lifetime_whatever_the_insertion_order(self):
        self.observe()
        busy = self.items()[0][0]['candidateInputs']['busy']
        self.assertEqual({tuple(h['publisher']): (h['lifetime'], h['busyIf'], h['clock']) for h in busy['holders']},
                         {('m', 'i1'): ('L1', [False, False], [False, 0, 1]),
                          ('m', 'i2'): ('L2', [True, True], [True, 0, 1])})

    def test_the_item_holds_todays_receipt_the_frame_and_the_census_of_the_frames_own_binding(self):
        self.observe()
        item = self.items()[0][0]
        self.assertEqual((item['today'], item['candidate'], item['comms']['busy']),
                         (False, True, dict(holds=False, owner=None, renewal=None, receipt=dict(read='no-row'))))
        self.assertEqual((item['candidateInputs']['busy']['renewalSource'], item['evalAt'], item['incomplete']),
                         ('not-needed', NOW, []))
        self.assertEqual(sorted(r['instance'] for r in item['env']['records']), ['i1', 'i2'])

    def test_d4_a_decision_without_its_receipt_is_kept_incomplete(self):
        self.observe(BusyDecision(False))
        self.assertEqual(self.items()[0][0]['incomplete'], ['comms-receipt'])

    def test_an_agreeing_evaluation_keeps_nothing(self):
        self.observe(BusyDecision(True, receipt={}))
        self.assertEqual(self.items(), ([], {}))


class ConsumerItemTest(_Busy):
    def basis(self, item):
        return item['comms']['busy'], item['candidateInputs']['busy'], item['env']

    def test_d2_queue_and_claim_items_each_carry_busys_whole_basis_as_encoded(self):
        observed = self.observe()
        busy_item = self.items()[0][0]
        bc.observe_queue(False, False, TODAY_FREE, observed, (0, 0))

        async def unsteerable():
            return False
        asyncio.run(bc.observe_claim(None, ROW, False, None, TODAY_FREE, observed, unsteerable))
        queue, claim = self.items()[0]
        self.assertEqual((queue['owner'], claim['owner']), (bc.QUEUE, bc.CLAIM))
        self.assertEqual(self.basis(queue), self.basis(busy_item))
        self.assertEqual(self.basis(claim), self.basis(busy_item))
        self.assertEqual({k: queue['comms'][k] for k in ('occupied', 'hasActiveRun', 'queuedRuns')},
                         dict(occupied=False, hasActiveRun=0, queuedRuns=0))
        self.assertEqual((claim['comms']['todaySteerable'], claim['candidateInputs']['shadowSteerable'],
                          claim['candidate']), ('not-asked', False, True),
                         'a real disagreeing claim: today asked nothing, the shadow read unsteerable once')

    def test_an_agreeing_queue_keeps_nothing(self):
        observed = self.observe()
        self.items()
        bc.observe_queue(True, True, TODAY_FREE, observed, (True, 0))
        self.assertEqual(self.items(), ([], {}))

    def test_steerability_keeps_who_asked_and_what_came_back(self):
        self.assertEqual([bc._steerability(v, 'not-asked') for v in (None, dt.NOT_KNOWN, True, False)],
                         ['not-asked', 'error', True, False])


class ReadinessItemTest(tvo._Shadow, unittest.TestCase):
    def observe(self, receipt):
        async def probe(db, agent_id):
            return True
        liveness = cd._WorkerLiveness(False, False, False, ('wrapper-child', 'unread'), receipt)
        with mock.patch.dict(vo.PROBES, {'wrapper-child': probe}):
            asyncio.run(vo.observe_readiness(None, tvo.ReadinessObserverTest.ROW, liveness, 'legacy'))
        return [json.loads(i.payload) for i in self.recorder.evidence.take()[0]]

    def test_the_item_keeps_todays_receipt_and_unread_tag_apart_from_the_shadows_probe(self):
        self.publish(replace(tvo.rec(process=('running', 'yes')), raw=b'{}', origin=(1, 'boot', 1, tvo.FRESH)))
        [item] = self.observe(dict(branch='unread'))
        self.assertEqual((item['today'], item['candidate'], item['incomplete']), (False, True, []))
        self.assertEqual(item['comms'], dict(hasLiveWorker=False, delivery=['wrapper-child', 'unread'],
                                             receipt=dict(branch='unread')))
        self.assertEqual({k: item['candidateInputs'][k] for k in ('component', 'state', 'shadowProbe')},
                         dict(component='wrapper-child', state=True, shadowProbe=True))
        self.assertEqual(len(item['env']['records']), 1, 'the census comes from the observer\'s own binding')
        [missing] = self.observe(None)
        self.assertEqual(missing['incomplete'], ['comms-receipt'])


class TodaysBusyReceiptTest(unittest.TestCase):
    """turn_busy_decision's receipt on every return path, and the one clock read only where today already made it."""

    def decide(self, rows=(), table=True, bridges=True):
        clocks = []
        real = cg.datetime

        class Counted:
            @staticmethod
            def now(tz=None):
                clocks.append(1)
                return real.now(tz)

        async def run():
            db = await aiosqlite.connect(':memory:')
            db.row_factory = aiosqlite.Row
            try:
                if table:
                    await db.execute('CREATE TABLE agent_turn_state (agent_id TEXT, turn_busy INTEGER, '
                                     'turn_updated_at TEXT, turn_started_at TEXT, turn_bridge_id TEXT)')
                for busy in rows:
                    await db.execute("INSERT INTO agent_turn_state VALUES ('a', ?, "
                                     "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 seconds'), "
                                     "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3000 seconds'), 'bridge-1')", (busy,))
                if bridges:
                    await db.execute('CREATE TABLE bridge_instances (id TEXT, agent_id TEXT, superseded_by TEXT, '
                                     'last_seen TEXT)')
                    await db.execute("INSERT INTO bridge_instances VALUES ('bridge-1', 'a', '', datetime('now'))")
                with mock.patch.object(cg, 'datetime', Counted):
                    return await cg.turn_busy_decision(db, 'a')
            finally:
                await db.close()
        return asyncio.run(run()), len(clocks)

    def test_d4_no_row_read_error_and_a_real_zero_are_three_receipts_with_no_clock(self):
        (no_row, c1), (error, c2), (zero, c3) = self.decide(), self.decide(table=False), self.decide(rows=(0,))
        self.assertEqual((no_row.receipt, error.receipt), (dict(read='no-row'), dict(read='read-error')))
        self.assertEqual(zero.receipt['read'], 'row')
        self.assertEqual(zero.receipt['turn_busy'], 0)
        self.assertNotIn('nowEpoch', zero.receipt)
        self.assertEqual((c1, c2, c3), (0, 0, 0), 'an early return gains no clock read')

    def test_the_full_path_keeps_the_row_its_one_clock_and_the_renewal_it_learned(self):
        (verified, clocks), (failed, _) = self.decide(rows=(1,)), self.decide(rows=(1,), bridges=False)
        self.assertEqual(clocks, 1)
        self.assertEqual({k: verified.receipt[k] for k in ('read', 'turn_busy', 'turn_bridge_id', 'owner', 'renewal')},
                         dict(read='row', turn_busy=1, turn_bridge_id='bridge-1', owner='bridge-1', renewal='verified'))
        self.assertIsInstance(verified.receipt['nowEpoch'], float)
        self.assertEqual(failed.receipt['renewal'], 'error')

    def test_d4_a_receipt_that_cannot_be_built_leaves_todays_decision_exactly(self):
        healthy, _ = self.decide(rows=(1,))

        class Unreadable:
            def __iter__(self):
                raise RuntimeError('receipt builder broken')
        with mock.patch.object(cg, 'TURN_COLUMNS', Unreadable()):
            broken, _ = self.decide(rows=(1,))
        self.assertIsNone(broken.receipt)
        self.assertEqual(replace(broken, receipt=None), replace(healthy, receipt=None))


class TodaysLivenessReceiptTest(tvo._Db):
    def receipt(self, agent_id, **kwargs):
        return self._liveness(agent_id, receipt=True, **kwargs).receipt

    def test_d4_the_channel_selector_is_kept_as_read_or_not_reached(self):
        self._register('cl', runtime='claude-code', sessionMode='managed')
        self._register('cx', runtime='codex', sessionMode='managed')
        self._register('gx', runtime='generic', sessionMode='managed', runtimeConfig={'channelEnabled': True})
        self.assertEqual([(r['branch'], r['channelEnabled']) for r in map(self.receipt, ('cl', 'cx', 'gx'))],
                         [('channel-console', vo.NOT_REACHED), ('codex-child', False), ('channel-flag', True)])

    def test_each_branch_names_the_states_it_read(self):
        self._register('cl', runtime='claude-code', sessionMode='managed')
        self._bridge('cl', 'channel-sidecar')
        cl = self.receipt('cl')
        self.assertEqual((cl['sidecar'], cl['console'], cl['wrapperChild']), (True, False, vo.NOT_REACHED))
        self._register('rs', runtime='claude-code', sessionMode='resident')
        self._terminal('rs', 'claude-aify')
        rs = self.receipt('rs', mode='resident', live_session=True)
        self.assertEqual((rs['branch'], rs['liveSession'], rs['terminalRow']),
                         ('none', True, dict(status='running', command='claude-aify')))

    def test_d4_a_receipt_that_cannot_be_built_is_none(self):
        class Row:
            def __getitem__(self, key):
                raise KeyError(key)
        self.assertIsNone(vo.liveness_receipt('managed', True, Row(), 'codex', False, 'none', 1, 2, 3))


class CoverageTest(unittest.TestCase):
    def test_d3_counts_only_and_unknown_are_never_complete(self):
        totals = {('busy', 'strict', 'claim'): dict(disagreed=1, completeRows=1, incompleteRows=0, missing={})}
        for owners, level in ((frozenset({'busy'}), 'evidence'), (frozenset(), 'counts-only'),
                              (store.UNKNOWN, 'unknown'), (store.NOT_INSTRUMENTED, 'not-instrumented')):
            with self.subTest(level=level):
                row = next(r for r in store.coverage(totals, owners, True) if r['variant'] == 'strict')
                self.assertEqual((row['instrumentation'], row['sites']['claim']['complete']),
                                 (level, level == 'evidence'))

    def test_d3_a_stored_set_is_read_exactly_and_bad_metadata_is_unknown(self):
        cases = ((True, ev.owners_json(ev.EVIDENCE_OWNERS), ev.EVIDENCE_OWNERS), (True, None, store.UNKNOWN),
                 (True, '{"busy": 1}', store.UNKNOWN), (True, '["no such owner"]', store.UNKNOWN),
                 (True, 'not json', store.UNKNOWN), (False, None, store.NOT_INSTRUMENTED))
        for instrumented, stored, expected in cases:
            with self.subTest(stored=str(stored)):
                self.assertEqual(store.window_owners(instrumented, stored), expected)

    def test_d3_the_migration_backfills_e1_once_and_never_resets_or_repairs(self):
        async def run():
            db = await aiosqlite.connect(':memory:')
            try:
                await db.execute('CREATE TABLE status_shadow_windows (id INTEGER PRIMARY KEY, comms_build TEXT, '
                                 'opened_at TEXT, flushed_at TEXT, closed_at TEXT, close_reason TEXT, boot_id TEXT)')
                await db.execute("INSERT INTO status_shadow_windows (id, boot_id) VALUES (1, 'e1'), (2, NULL)")
                await _migrate_status_shadow_windows(db)
                first = await (await db.execute('SELECT id, evidence_owners FROM status_shadow_windows')).fetchall()
                await db.execute("INSERT INTO status_shadow_windows (id, boot_id, evidence_owners) VALUES (3, 'e2', ?), "
                                 "(4, 'e2', NULL)", (ev.owners_json(ev.EVIDENCE_OWNERS),))
                await _migrate_status_shadow_windows(db)
                return first, await (await db.execute('SELECT id, evidence_owners FROM status_shadow_windows')).fetchall()
            finally:
                await db.close()
        first, again = asyncio.run(run())
        self.assertEqual(first, [(1, E1_EVIDENCE_OWNERS), (2, None)])
        self.assertEqual(again, [(1, E1_EVIDENCE_OWNERS), (2, None), (3, ev.owners_json(ev.EVIDENCE_OWNERS)), (4, None)],
                         'a repeated init keeps an E2 set, and a later NULL stays NULL: unknown, not E1')
        self.assertEqual(json.loads(E1_EVIDENCE_OWNERS), sorted(['status word', 'live counts', 'running-run promotion']))

    def test_d1_a_value_plain_cannot_name_is_an_encode_error_gap_never_a_string(self):
        with self.assertRaises(TypeError):
            ev.plain(object())
        buffer = ev.EvidenceBuffer()
        buffer.capture(('busy', 'strict', 'claim'), lambda: dict(agentId='a', evalAt=None, incomplete=[], x=object()))
        self.assertEqual(buffer.take(), ((), {('busy', 'strict', 'claim', 'encode-error'): 1}))

    def test_the_word_item_bytes_are_unchanged_by_reading_fields_instead_of_asdict(self):
        inputs = e1.inputs(config_defect='bad config')
        self.assertEqual(ev.encode(dict(inputs=inputs)), ev.encode(dict(inputs=asdict(inputs))))


class WindowOwnersTest(e1._Evidence):
    def instrumentation(self, page, owner):
        return {r['instrumentation'] for r in page['coverage'] if r['owner'] == owner}

    def test_d3_an_e2_window_keeps_its_set_through_expiry_and_an_old_tombstone_reads_e1(self):
        owner = windows.ShadowWindows(self.recorder)
        self.wait(owner.start(e1.V, e1.at(0)))
        self.wait(owner.tick(e1.V2, e1.at(10)))
        self.assertEqual(self.wait(windows.expire_windows('2026-11-03T10:00:00Z')), 1)
        page = self.page(1)
        self.assertEqual((page['window']['expired'], json.loads(page['window']['evidenceOwners'])),
                         (True, E2_OWNERS))
        self.assertEqual(self.instrumentation(page, 'busy'), {'evidence'})
        self.sql_write("INSERT INTO status_shadow_window_tombstones (window_id, comms_build, wrapper_build, opened_at, "
                       "closed_at, close_reason, summary, deleted_at) VALUES (90, 'old', NULL, 'x', 'y', 'rotation', ?, "
                       "'z')", (json.dumps(dict(flushedSeq=0, evidenceRows=0, evidenceBytes=0, bootId='b', envBuild=None,
                                                flushedAt='y', instrumented=True, totals=[])),))
        old = self.page(90)
        self.assertEqual((old['window']['evidenceOwners'], self.instrumentation(old, 'busy'),
                          self.instrumentation(old, 'status word')), (E1_EVIDENCE_OWNERS, {'counts-only'}, {'evidence'}))

    def sql_write(self, query, params=()):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute(query, params)
            db.commit()
