"""C6's holdsDelivery beside today's busy gate (status-switch S2c slice 3).

Four kinds of witness, kept apart: the pure three-valued OR on frames; the frame's capture from the mirror and the
fold; the real observer (its one ownership read, reuse, the frozen frame, containment); and the two real sites, where
today's answer and the queue/claim outcome must not move whatever the observer does.
"""
import asyncio
import unittest
from unittest import mock

import aiosqlite

from service.api_core import comms_view as cv, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core.claim_gating import BusyDecision
from service.tests import test_p7_witness_delivery_hold as p7
from service.tests.delivery_witnesses import TurnSpecimen

AT = 100_000.0
NK = dt.NOT_KNOWN
LIVE = dt.DeliveryTurn(open=True, run_id='r', started_at=AT - 100, touched_at=AT - 50, owner='bridge-1')
EXPIRED = dt.DeliveryTurn(open=True, run_id='r', started_at=AT - 5000, touched_at=AT - 10, owner='bridge-1')


def frame(*holders, others_unknown=False, delivery=dt.CLOSED):
    return cv.Frame(AT, tuple(holders), others_unknown, delivery)


def both(f, renewal=None):
    return {v: cv.holds_delivery(f, renewal, v) for v in cv.LAWS}


class HoldsDeliveryTest(unittest.TestCase):
    def test_a_stale_open_turn_needs_its_expiry(self):
        stale = lambda expiry: cv.Holder('stale', (True, True), (True, 5, 5), expiry)
        self.assertEqual(both(frame(stale(NK)))['strict'], (NK, 'strict-expiry'))
        self.assertEqual(both(frame(stale(AT + 1)))['strict'], (True, None))
        self.assertEqual(both(frame(stale(AT - 1)))['strict'], (False, None))

    def test_a_known_true_decides_whatever_is_unknown(self):
        unknown_stale = cv.Holder('stale', None, (True, 5, 5), NK)
        fresh_busy = cv.Holder('fresh', (True, True), (True, 5, 5))
        self.assertEqual(both(frame(unknown_stale, fresh_busy))['strict'], (True, None))
        self.assertEqual(both(frame(unknown_stale, delivery=LIVE))['P-1'], (True, None))
        self.assertEqual(both(frame(cv.Holder('unknown'), cv.Holder('fresh', (True, True))))['strict'], (True, None))

    def test_false_needs_every_disjunct_known_false(self):
        idle = cv.Holder('fresh', (False, False), (False, 0, 1))
        self.assertEqual(both(frame(idle))['strict'], (False, None))
        self.assertEqual(both(frame(idle, cv.Holder('unknown')))['strict'], (NK, 'record-knowledge'))
        self.assertEqual(both(frame(idle, delivery=dt.NOTHING_KNOWN))['strict'], (NK, 'delivery-turn'))

    def test_no_record_on_a_readable_machine_holds_nothing_but_only_beside_a_known_delivery_turn(self):
        self.assertEqual(both(frame())['strict'], (False, None))
        self.assertEqual(both(frame(delivery=dt.NOTHING_KNOWN))['strict'], (NK, 'delivery-turn'))
        self.assertEqual(both(frame(others_unknown=True))['strict'], (NK, 'record-knowledge'))

    def test_the_delivery_turn_holds_by_todays_timer_law(self):
        self.assertEqual(both(frame(delivery=LIVE))['strict'], (True, None))
        self.assertEqual(both(frame(delivery=EXPIRED), 'negative')['strict'], (False, None))
        self.assertEqual(both(frame(delivery=EXPIRED), 'verified')['strict'], (True, None))
        self.assertEqual(both(frame(delivery=EXPIRED), NK)['strict'], (NK, 'renewal'))
        self.assertTrue(cv.needs_renewal(frame(delivery=EXPIRED)))
        self.assertFalse(cv.needs_renewal(frame(delivery=LIVE)))

    def test_strict_and_p1_read_their_own_law_off_one_record(self):
        verified_only = cv.Holder('fresh', (False, True), (True, 5, 5))
        self.assertEqual(both(frame(verified_only)), {'strict': (False, None), 'P-1': (True, None)})

    def test_the_first_unknown_input_names_the_reason(self):
        f = frame(cv.Holder('stale', None, (True, 5, 5), NK), others_unknown=True, delivery=EXPIRED)
        self.assertEqual(both(f, NK)['strict'], (NK, 'record-knowledge'))
        f = frame(cv.Holder('stale', None, (True, 5, 5), NK), delivery=EXPIRED)
        self.assertEqual(both(f, NK)['strict'], (NK, 'strict-expiry'))


class CaptureTest(unittest.TestCase):
    """The frame from a real Mirror and a real fold."""

    def setUp(self):
        self.mirror, self.fold = shadow.Mirror(), dt.DeliveryTurns()
        for p in (mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'clock', return_value='2026-10-19T00:10:00Z')):
            p.start(); self.addCleanup(p.stop)

    def feed(self, key, applied, kind='snapshot', *records):
        self.mirror.feed(shadow.Projection(key, kind, 1, applied, records, ()))

    def test_fresh_stale_and_unreadable_publishers(self):
        busy = shadow.Record('a', 'L', 'working', (), (True, 5, 5), (True, True))
        self.feed(('m', 'i1'), '2026-10-19T00:09:30Z', 'snapshot', busy)
        f = cv.capture('m', 'a')
        self.assertEqual((f.holders[0].freshness, f.others_unknown), ('fresh', False))
        self.feed(('m', 'i1'), '2026-10-19T00:00:00Z', 'snapshot', busy)
        self.assertEqual(cv.capture('m', 'a').holders[0].freshness, 'stale')
        self.mirror.invalidate(('m', 'i1'))
        self.assertEqual(cv.capture('m', 'a').holders[0].freshness, 'unknown')

    def test_a_machine_with_no_publisher_or_an_unreadable_one_is_unknown(self):
        self.assertTrue(cv.capture('m', 'a').others_unknown)
        self.feed(('m', 'i1'), '2026-10-19T00:09:30Z', 'delta')  # a first delta: incomplete
        self.assertTrue(cv.capture('m', 'a').others_unknown)
        self.feed(('m', 'i2'), '2026-10-19T00:09:30Z', 'snapshot')
        self.mirror.feed(shadow.Projection(('m', 'i1'), 'snapshot', 1, '2026-10-19T00:09:30Z', (), ()))
        f = cv.capture('m', 'a')
        self.assertEqual((f.holders, f.others_unknown), ((), False), 'every publisher readable and none holds it')


class TodaysDecisionTest(unittest.TestCase):
    """turn_busy_decision is today's law with the ownership question it asked kept: a failed read is 'error'."""

    def decide(self, bridges):
        from service.api_core.claim_gating import turn_busy_decision

        async def run():
            db = await aiosqlite.connect(':memory:')
            db.row_factory = aiosqlite.Row
            try:
                await db.execute('CREATE TABLE agent_turn_state (agent_id TEXT, turn_busy INTEGER, turn_updated_at TEXT, '
                                 'turn_started_at TEXT, turn_bridge_id TEXT)')
                await db.execute("INSERT INTO agent_turn_state VALUES ('a', 1, "
                                 "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-10 seconds'), "
                                 "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3000 seconds'), ' bridge-1 ')")
                if bridges:
                    await db.execute('CREATE TABLE bridge_instances (id TEXT, agent_id TEXT, superseded_by TEXT, '
                                     'last_seen TEXT)')
                    await db.execute("INSERT INTO bridge_instances VALUES ('bridge-1', 'a', '', datetime('now'))")
                return await turn_busy_decision(db, 'a')
            finally:
                await db.close()
        return asyncio.run(run())

    def test_the_owner_and_outcome_it_asked_are_kept(self):
        self.assertEqual(self.decide(bridges=True), BusyDecision(True, 'bridge-1', 'verified'))
        self.assertEqual(self.decide(bridges=False), BusyDecision(False, 'bridge-1', 'error'),
                         "an unreadable bridge table is today's strict anchor, and tagged as the error it was")


class FrozenThroughRealCaptureTest(unittest.TestCase):
    """The frame comes from a real Mirror and a real fold; the world changes while the one read is pending."""

    def setUp(self):
        self.mirror, self.fold, self.recorder = shadow.Mirror(), dt.DeliveryTurns(), shadow.Recorder()
        for p in (mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'recorder', self.recorder),
                  mock.patch.object(shadow, 'clock', return_value='2026-10-19T00:10:00Z')):
            p.start(); self.addCleanup(p.stop)
        at = 1792368600.0  # 2026-10-19T00:10:00Z
        self.feed(busy_if=(False, False))  # a fresh idle record: the record disjunct is false
        self.fold._turns[('m', 'a')] = dt.DeliveryTurn(True, 'r', at - 5000, at - 10, 'bridge-1')  # expired start

    def feed(self, busy_if):
        record = shadow.Record('a', 'L', 'idle', (), (False, 0, 1), busy_if)
        self.mirror.feed(shadow.Projection(('m', 'i1'), 'snapshot', 1, '2026-10-19T00:09:50Z', (record,), ()))

    def test_a_record_and_a_turn_that_change_during_the_read_are_not_read(self):
        asked = []

        async def read_while_the_world_moves(db, agent_id, owner):
            asked.append((agent_id, owner))
            self.feed(busy_if=(True, True))  # the mirror now says busy
            self.fold.reporter('m', 'a', True, 'r2', 'bridge-2', 1792368600.0)  # and a new owner opens a turn
            return 'negative'
        with mock.patch.object(cv, 'turn_lease_renewal', side_effect=read_while_the_world_moves):
            asyncio.run(cv.observe_busy(None, {'id': 'a', 'machine_id': 'm'}, BusyDecision(False), 'claim'))
        self.assertEqual(asked, [('a', 'bridge-1')])
        busy = self.recorder.report()['byPurpose']['busy']['variants']
        self.assertEqual({v: busy[v]['counts']['partialAgreed'] for v in cv.LAWS}, {'strict': 1, 'P-1': 1},
                         'frozen: an idle record and an expired, unrenewed turn hold nothing')
        # Contrast: the source really did change, so a capture after the read would have said busy.
        self.assertEqual(cv.holds_delivery(cv.capture('m', 'a'), 'negative', 'strict'), (True, None))


class _Bridges:
    """A real sqlite bridge table, so the ownership read is the shipping SELECT; `execute` is counted."""

    async def open(self, fail=False):
        self.db = await aiosqlite.connect(':memory:')
        await self.db.execute('CREATE TABLE bridge_instances (id TEXT, agent_id TEXT, superseded_by TEXT, last_seen TEXT)')
        await self.db.execute("INSERT INTO bridge_instances VALUES ('bridge-1', 'a', '', datetime('now'))")
        self.calls, real = 0, self.db.execute

        def counted(*args, **kw):
            self.calls += 1
            if fail:
                raise RuntimeError('bridge table unreadable')
            return real(*args, **kw)
        self.db.execute = counted
        return self


class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.recorder, self.fold = shadow.Recorder(), dt.DeliveryTurns()
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(cv, 'capture', side_effect=self.frame_for)):
            p.start(); self.addCleanup(p.stop)
        self.delivery = EXPIRED

    def frame_for(self, machine, agent_id):
        return frame(delivery=self.delivery)

    def observe(self, decision=BusyDecision(False), fail=False, agent_id='a'):
        async def run():
            bridges = await _Bridges().open(fail=fail)
            try:
                await cv.observe_busy(bridges.db, {'id': agent_id, 'machine_id': 'm'}, decision, 'claim')
            finally:
                await bridges.db.close()
            return bridges.calls
        calls = asyncio.run(run())
        busy = self.recorder.report()['byPurpose']['busy']['variants']
        return calls, busy

    def test_a_verified_owner_holds_a_negative_one_does_not_and_a_failed_read_is_unknown(self):
        calls, busy = self.observe(agent_id='a')
        self.assertEqual((calls, busy['strict']['disagreementsByKey']),
                         (1, [dict(site='claim', today='false', candidate='true', count=1)]))
        self.recorder = shadow.Recorder()
        with mock.patch.object(shadow, 'recorder', self.recorder):
            calls, busy = self.observe(agent_id='someone-else')  # the bridge is not this agent's: negative
        self.assertEqual((calls, busy['strict']['counts']['partialAgreed']), (1, 1))
        self.recorder = shadow.Recorder()
        with mock.patch.object(shadow, 'recorder', self.recorder):
            calls, busy = self.observe(fail=True)
        self.assertEqual((calls, busy['strict']['unavailableByReason']), (1, {'claim': {'renewal': 1}}))

    def test_todays_answer_for_the_same_owner_is_reused_without_a_read(self):
        for renewal in ('verified', 'negative'):
            with self.subTest(renewal=renewal):
                calls, _ = self.observe(BusyDecision(False, 'bridge-1', renewal))
                self.assertEqual(calls, 0)
        for decision in (BusyDecision(False, 'bridge-1', 'error'), BusyDecision(False, 'other', 'verified')):
            with self.subTest(decision=repr(decision)):
                calls, _ = self.observe(decision)
                self.assertEqual(calls, 1, 'an error is no known negative; another owner is another question')

    def test_no_read_when_the_delivery_turn_does_not_need_one(self):
        for name, delivery in (('live', LIVE), ('closed', dt.CLOSED), ('unknown', dt.NOTHING_KNOWN)):
            with self.subTest(delivery=name):
                self.delivery = delivery
                self.assertEqual(self.observe()[0], 0)

    def test_the_frame_is_frozen_before_the_ownership_read(self):
        asked = []

        async def read_while_the_world_moves(db, agent_id, owner):
            asked.append((agent_id, owner))
            self.delivery = dt.CLOSED  # anything read after this await would see a closed turn
            self.fold.reporter('m', agent_id, True, 'r2', 'bridge-2', AT)
            return 'verified'
        with mock.patch.object(cv, 'turn_lease_renewal', side_effect=read_while_the_world_moves):
            _, busy = self.observe()
        self.assertEqual(asked, [('a', 'bridge-1')], 'the verdict is for the frozen turn\'s own owner')
        self.assertEqual(busy['P-1']['disagreementsByKey'], [dict(site='claim', today='false', candidate='true', count=1)],
                         'the frozen open turn, verified, holds; a read after the await would see it closed')

    def test_a_recording_failure_never_replays_and_never_raises(self):
        calls = []

        def record(owner, variant, site, today, candidate, reason=None):
            calls.append((variant, reason))
            if variant == 'P-1':
                raise RuntimeError('recorder broken for P-1')
        with mock.patch.object(self.recorder, 'record_owner', side_effect=record):
            self.observe(BusyDecision(False, 'bridge-1', 'verified'))
        self.assertEqual(calls, [('strict', None), ('P-1', None), ('P-1', 'shadow-error')])

    def test_a_capture_failure_records_both_variants_unavailable(self):
        with mock.patch.object(cv, 'capture', side_effect=RuntimeError('mirror unreadable')):
            _, busy = self.observe()
        self.assertEqual({v: busy[v]['unavailableByReason'] for v in cv.LAWS},
                         {v: {'claim': {'shadow-error': 1}} for v in cv.LAWS})

    def test_cancellation_still_propagates(self):
        async def cancelled(*args):
            raise asyncio.CancelledError()
        with mock.patch.object(cv, 'turn_lease_renewal', side_effect=cancelled), \
                self.assertRaises(asyncio.CancelledError):
            self.observe()


FRESH = TurnSpecimen('fresh-hook-turn')


class _SiteObservation:
    """At a real site: a fresh recorder and fold, and the SQL the observer itself issues, counted."""

    def setUp(self):
        super().setUp()
        self.recorder, self.fold, self.observer_sql = shadow.Recorder(), dt.DeliveryTurns(), []
        real = cv.observe_busy

        async def counted(db, agent_row, decision, site):
            class Counting:
                def __getattr__(_, name):
                    return getattr(db, name)

                def execute(_, *args, **kw):
                    self.observer_sql.append(args[0])
                    return db.execute(*args, **kw)
            return await real(Counting(), agent_row, decision, site)
        for patcher in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(dt, 'FOLD', self.fold),
                        mock.patch.object(cv, 'observe_busy', new=counted)):
            patcher.start(); self.addCleanup(patcher.stop)

    def strict_records_p1_fails(self):
        """The real recorder for strict; P-1's record and its fallback both raise."""
        real = self.recorder.record_owner

        def record(owner, variant, site, today, candidate, reason=None):
            if variant == 'P-1':
                raise RuntimeError('recorder broken for P-1')
            return real(owner, variant, site, today, candidate, reason)
        return mock.patch.object(self.recorder, 'record_owner', side_effect=record)

    def assert_one_strict_and_no_p1(self, site):
        busy = self.recorder.report()['byPurpose']['busy']['variants']
        self.assertEqual(busy['strict']['bySite'][site]['observed'], 1, 'the successful prefix, counted once')
        self.assertFalse(busy['P-1']['instrumented'])

    def open_expired_turn(self, agent_id, owner):
        from service.clock import iso_to_epoch, now
        at = iso_to_epoch(now())
        self.fold._turns[(p7.MACHINE, agent_id)] = dt.DeliveryTurn(True, 'r', at - 5000, at - 10, owner)


class SendTimeSiteTest(_SiteObservation, p7.SendTimeQueueWitness):
    """The real send-time site: today's answer and the queue outcome do not move, whatever the observer does."""

    test_each_specimen_holds_or_releases_the_queue_as_its_law_says = None  # the witness's own matrix runs there
    test_an_active_run_or_an_earlier_queued_run_also_holds_the_queue = None

    def test_the_observer_adds_no_sql_unless_a_turn_needs_its_owner_asked(self):
        self._queue_send('sql-none', FRESH)
        self.assertEqual(self.observer_sql, [])
        self.open_expired_turn('sql-one', 'bridge-elsewhere')
        self._queue_send('sql-one', FRESH)
        self.assertEqual(len(self.observer_sql), 1)
        self.assertIn('FROM bridge_instances', self.observer_sql[0])

    def busy(self):
        return self.recorder.report()['byPurpose']['busy']['variants']

    def test_a_held_send_is_observed_and_still_waits(self):
        self._assert_waits('obs-held', self._queue_send('obs-held', FRESH))
        self.assertEqual(self.busy()['strict']['bySite']['send-time']['observed'], 1)

    def test_a_failing_observer_changes_nothing_and_records_the_failure(self):
        with mock.patch.object(cv, 'capture', side_effect=RuntimeError('broken')):
            self._assert_waits('obs-broken', self._queue_send('obs-broken', FRESH))
        self.assertEqual(self.busy()['P-1']['unavailableByReason'], {'send-time': {'shadow-error': 1}})

    def test_a_compute_failure_changes_nothing_and_records_the_failure(self):
        with mock.patch.object(cv, 'holds_delivery', side_effect=RuntimeError('broken')):
            self._assert_waits('obs-compute', self._queue_send('obs-compute', FRESH))
        busy = self.busy()
        self.assertEqual({v: busy[v]['unavailableByReason'] for v in cv.LAWS},
                         {v: {'send-time': {'shadow-error': 1}} for v in cv.LAWS})

    def test_a_failing_fallback_record_changes_nothing_and_never_replays(self):
        with self.strict_records_p1_fails():
            self._assert_waits('obs-record', self._queue_send('obs-record', FRESH))
        self.assert_one_strict_and_no_p1('send-time')

    def test_an_unexpected_acquisition_failure_releases_as_today_and_observes_nothing(self):
        from service.api_core import dispatch_launch
        with mock.patch.object(dispatch_launch, 'turn_busy_decision', side_effect=RuntimeError('unreadable')):
            self._assert_woken('obs-unread', self._queue_send('obs-unread', FRESH))
        self.assertFalse(self.recorder.report()['byPurpose']['busy']['instrumented'])


class ClaimSiteTest(_SiteObservation, p7.ClaimHoldWitness):
    test_each_specimen_holds_or_releases_the_claim_as_its_law_says = None  # the witness's own matrix runs there

    def test_the_observer_adds_no_sql_unless_a_turn_needs_its_owner_asked(self):
        self._claim('sql-none', FRESH)
        self.assertEqual(self.observer_sql, [])
        self.open_expired_turn('sql-one', 'bridge-elsewhere')
        self._claim('sql-one', FRESH)
        self.assertEqual(len(self.observer_sql), 1)

    def test_a_held_claim_is_observed_and_still_held(self):
        self._assert_held('obs-held', self._claim('obs-held', FRESH))
        busy = self.recorder.report()['byPurpose']['busy']['variants']
        self.assertEqual(busy['strict']['bySite']['claim']['observed'], 1)

    def test_a_failing_observer_changes_nothing_and_records_the_failure(self):
        with mock.patch.object(cv, 'capture', side_effect=RuntimeError('broken')):
            self._assert_held('obs-broken', self._claim('obs-broken', FRESH))

    def test_a_compute_failure_changes_nothing_and_records_the_failure(self):
        with mock.patch.object(cv, 'holds_delivery', side_effect=RuntimeError('broken')):
            self._assert_held('obs-compute', self._claim('obs-compute', FRESH))
        busy = self.recorder.report()['byPurpose']['busy']['variants']
        self.assertEqual({v: busy[v]['unavailableByReason'] for v in cv.LAWS},
                         {v: {'claim': {'shadow-error': 1}} for v in cv.LAWS})

    def test_a_failing_fallback_record_changes_nothing_and_never_replays(self):
        with self.strict_records_p1_fails():
            self._assert_held('obs-record', self._claim('obs-record', FRESH))
        self.assert_one_strict_and_no_p1('claim')

    def test_an_unexpected_acquisition_failure_continues_the_claim_as_today(self):
        from service import dispatch_claim
        with mock.patch.object(dispatch_claim, 'turn_busy_decision', side_effect=RuntimeError('unreadable')):
            self._assert_claimed('obs-unread', self._claim('obs-unread', FRESH))
        self.assertFalse(self.recorder.report()['byPurpose']['busy']['instrumented'])
