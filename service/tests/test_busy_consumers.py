"""C6's four busy consumers beside today's (status-switch S2c slice 4).

Four kinds of witness, kept apart: the pure candidates and their masks; the fold's record freshness, a baseline of its
own; the observers' inputs and their read budget (at most one association read, at most one steerability attempt); and
the four real sites, where today's answer, events and commit/return do not move whatever the observer does.
"""
import asyncio
import unittest
from unittest import mock

import aiosqlite

from service.api_core import busy_consumers as bc, comms_view as cv, delivery_turns as dt
from service.api_core import partial_status_shadow as shadow
from service.clock import iso_to_epoch
from service.tests import test_p7_witness_delivery_hold as p7, test_reply_reminders as rr
from service.tests import test_stranded_reply_fail as sr
from service.tests._base import FastApiTestCase
from service.tests.delivery_witnesses import TurnSpecimen
from service.tests.test_delivery_turns import iso, publish, record

NK = dt.NOT_KNOWN
AT = 100_000.0
U = lambda reason: (NK, reason)  # noqa: E731
HOLD_TRUE, HOLD_FALSE, HOLD_UNKNOWN = (True, None), (False, None), U('delivery-turn')


def turn(open_=True, run='r', touched=AT - 10):
    return dt.DeliveryTurn(open_, run, AT - 100, touched, 'bridge-1')


def holder(open_=True, advanced=AT - 10, freshness='fresh'):
    return cv.Holder(freshness, (False, False), (open_, 5, 5) if open_ is not None else None, NK, advanced)


def frame(*holders, delivery=dt.CLOSED, others_unknown=False):
    return cv.Frame(AT, tuple(holders), others_unknown, delivery)


class CandidateTest(unittest.TestCase):
    def test_an_occupied_or_queued_run_queues_whatever_happened_to_the_hold(self):
        for hold in (U('busy-unread'), U('shadow-error'), HOLD_FALSE):
            self.assertEqual(bc.queue_candidate(True, hold), (True, None))
        for hold in (HOLD_TRUE, HOLD_FALSE, U('busy-unread')):
            self.assertEqual(bc.queue_candidate(False, hold), hold, 'with nothing occupied the hold decides')

    def test_the_claim_gate_reads_steerability_only_where_it_can_change_the_answer(self):
        self.assertEqual(bc.claim_candidate(HOLD_FALSE, NK), (False, None), 'a false hold needs no steerability')
        self.assertEqual(bc.claim_candidate(HOLD_UNKNOWN, True), (False, None), 'a steerable run opens the gate')
        self.assertEqual(bc.claim_candidate(HOLD_TRUE, False), (True, None))
        self.assertEqual(bc.claim_candidate(HOLD_UNKNOWN, False), HOLD_UNKNOWN, 'the hold names the reason')
        self.assertEqual(bc.claim_candidate(HOLD_TRUE, NK), U('steerable'))

    def test_the_reminders_other_work_is_c6s_expression(self):
        cases = (
            ('nothing open, every publisher read', frame(), (False, None)),
            ('a closed record', frame(holder(open_=False)), (False, None)),
            ('a record advanced 10 s ago, no delivery turn: run is \'\'', frame(holder()), (True, None)),
            ('a record advanced 200 s ago', frame(holder(advanced=AT - 200)), (False, None)),
            ('another run\'s delivery turn touched 10 s ago', frame(delivery=turn(run='other')), (True, None)),
            ('this run\'s own delivery turn masks an unknown record', frame(holder(advanced=NK), delivery=turn()),
             (False, None)),
            ('another run, touched 200 s ago', frame(delivery=turn(run='other', touched=AT - 200)), (False, None)),
            ('an open record whose last advance is unknown', frame(holder(advanced=NK)), U('record-freshness')),
            ('an unreadable publisher', frame(others_unknown=True), U('record-knowledge')),
            ('an unreadable holder', frame(holder(freshness='unknown')), U('record-knowledge')),
            ('an unknown delivery turn', frame(delivery=dt.NOTHING_KNOWN), U('delivery-turn')),
            ('a fresh record beside an unknown delivery run', frame(holder(), delivery=turn(run=NK)), U('delivery-turn')),
        )
        for name, f, expected in cases:
            with self.subTest(name):
                self.assertEqual(bc.other_work(f, 'r'), expected)

    def test_the_stranded_guard_skips_only_while_the_delivery_turn_is_open_on_this_run(self):
        self.assertEqual(bc.stranded_candidate(dt.CLOSED, 'r'), (False, None))
        self.assertEqual(bc.stranded_candidate(turn(), 'r'), (True, None))
        self.assertEqual(bc.stranded_candidate(turn(run='other'), 'r'), (False, None))
        self.assertEqual(bc.stranded_candidate(dt.NOTHING_KNOWN, 'r'), U('delivery-turn'))
        self.assertEqual(bc.stranded_candidate(turn(run=NK), 'r'), U('delivery-turn'))


class RecordFreshnessTest(unittest.TestCase):
    """A lineage's last advance on the application clock, bound to its turn, with a baseline of its own."""

    def setUp(self):
        self.fold = dt.DeliveryTurns()

    def see(self, at, started, last, open_=True, lifetime='L', holders=None):
        publish(self.fold, ('m', 'i'), iso(at), record('x', lifetime, (open_, started, last)), holders=holders)

    def fresh(self, started, lifetime='L'):
        return self.fold.advanced_at('m', 'x', lifetime, started)

    def test_a_first_sight_knows_nothing_and_a_genuine_advance_sets_the_application_time(self):
        self.see(10, 1, 1)
        self.assertIs(self.fresh(1), NK, 'a turn already open at first sight: when it last moved is unknown')
        self.see(20, 1, 2)
        self.assertEqual(self.fresh(1), 20)

    def test_a_quiet_republication_never_refreshes(self):
        self.see(10, 1, 1); self.see(20, 1, 2); self.see(50, 1, 2)
        self.assertEqual(self.fresh(1), 20)

    def test_a_new_start_while_open_is_an_advance_of_the_new_turn_only(self):
        self.see(10, 1, 1); self.see(20, 1, 2); self.see(30, 7, 7)
        self.assertEqual((self.fresh(7), self.fresh(1)), (30, NK), 'no turn inherits another\'s time')
        self.see(40, 7, 9); self.see(50, 9, 9)
        self.assertEqual(self.fresh(9), 50, 'a new start tied with the last event is still a start, not quiet')

    def test_closed_or_null_then_open_is_a_start_seen_now(self):
        self.see(10, 0, 5, open_=False); self.see(20, 9, 9)
        self.assertEqual(self.fresh(9), 20)
        publish(self.fold, ('m', 'i'), iso(30), record('x', 'L', None))
        self.see(40, 11, 11)
        self.assertEqual(self.fresh(11), 40, 'a null turn is a known closed baseline, not an absent one')

    def test_a_withheld_clock_that_reappears_unchanged_is_not_an_advance(self):
        self.see(10, 1, 1); self.see(20, 1, 2)
        self.see(30, 1, 3, holders=[('m', 'i'), ('m', 'j')])  # withheld: two publishers hold it
        self.assertIs(self.fresh(1), NK)
        self.see(40, 1, 3)
        self.assertIs(self.fresh(1), NK, 'the recovery is a new baseline, not an advance against the old one')
        self.see(50, 1, 4)
        self.assertEqual(self.fresh(1), 50, 'a genuinely later advance after the recovery')

    def test_a_replaced_identity_and_a_loss_start_again_from_unknown(self):
        self.see(10, 1, 1); self.see(20, 1, 2)
        self.see(30, 1, 2, lifetime='L2')
        self.assertIs(self.fresh(1, 'L2'), NK)
        self.fold.lose_everything()
        self.see(40, 1, 3)
        self.assertIs(self.fresh(1), NK)

    def test_delivery_touches_and_record_freshness_stay_apart(self):
        self.fold.reporter('m', 'x', True, 'r1', 'b', 15)
        self.assertIs(self.fresh(1), NK, 'a reporter touch is not a record advance')
        fold = dt.DeliveryTurns()
        publish(fold, ('m', 'i'), iso(10), record('x', 'L', (False, 0, 5)))
        publish(fold, ('m', 'i'), iso(20), record('x', 'L', (True, 9, 9)))
        self.assertEqual(fold.advanced_at('m', 'x', 'L', 9), 20)
        self.assertEqual(fold.turn('m', 'x'), dt.NOTHING_KNOWN, 'a record\'s start opens no delivery turn')


class CaptureFreshnessTest(unittest.TestCase):
    """The frame carries each open record's freshness from the real fold, bound to that record's turn."""

    def test_an_advance_the_fold_applied_reaches_the_frame(self):
        mirror, fold = shadow.Mirror(), dt.DeliveryTurns()
        with mock.patch.object(shadow, 'mirror', mirror), mock.patch.object(dt, 'FOLD', fold), \
                mock.patch.object(shadow, 'clock', return_value='2026-10-19T00:10:00Z'):
            for applied, last in (('2026-10-19T00:09:00Z', 1), ('2026-10-19T00:09:30Z', 2)):
                rec = shadow.Record('a', 'L', 'working', (), (True, 1, last), (True, True))
                projection = shadow.Projection(('m', 'i1'), 'snapshot', 1, applied, (rec,), ())
                mirror.feed(projection)
                fold.env_applied(projection, mirror.view())
            self.assertEqual(cv.capture('m', 'a').holders[0].advanced_at, iso_to_epoch('2026-10-19T00:09:30Z'))


class _Db:
    """A real sqlite agents table, so the association read is the shipping SELECT; `execute` is counted."""

    async def open(self, machine='m', fail=False, missing=False):
        self.db = await aiosqlite.connect(':memory:')
        self.db.row_factory = aiosqlite.Row
        await self.db.execute('CREATE TABLE agents (id TEXT, machine_id TEXT)')
        if not missing:
            await self.db.execute('INSERT INTO agents VALUES (?, ?)', ('a', machine))
        self.calls, real = 0, self.db.execute

        def counted(*args, **kw):
            self.calls += 1
            if fail:
                raise RuntimeError('agents unreadable')
            return real(*args, **kw)
        self.db.execute = counted
        return self


class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.recorder, self.fold = shadow.Recorder(), dt.DeliveryTurns()
        self.frames = []
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(cv, 'capture', side_effect=self.capture)):
            p.start(); self.addCleanup(p.stop)

    def capture(self, machine, agent_id):
        self.frames.append(machine)
        return frame(delivery=self.fold.turn(machine, agent_id))

    def run_with_db(self, observe, **db):
        async def run():
            d = await _Db().open(**db)
            try:
                await observe(d.db)
            finally:
                await d.db.close()
            return d.calls
        return asyncio.run(run())

    def owner(self, name):
        return self.recorder.report()['byPurpose'][name]['variants']['-']

    def test_the_reminder_masks_read_nothing(self):
        for kw in (dict(blocked=True, occupied=True), dict(blocked=False, occupied=True)):
            calls = self.run_with_db(lambda db: bc.observe_reminder(db, 'a', 'r', False, **kw))
            self.assertEqual((calls, self.frames), (0, []))
        self.assertEqual(self.owner(bc.REMINDER)['disagreementsByKey'],
                         [dict(site='reminder', today='false', candidate='true', count=1)])

    def test_the_reminder_associates_by_registration_once_or_by_this_evaluations_row(self):
        self.fold._turns[('m', 'a')] = turn(run='other')
        calls = self.run_with_db(lambda db: bc.observe_reminder(db, 'a', 'r', False, blocked=False, occupied=False))
        self.assertEqual((calls, self.frames), (1, ['m']))
        calls = self.run_with_db(lambda db: bc.observe_reminder(db, 'a', 'r', False, blocked=False, occupied=False,
                                                                agent_row={'machine_id': 'm'}))
        self.assertEqual((calls, self.frames), (0, ['m', 'm']), 'the row this evaluation read is reused')
        self.assertEqual(self.owner(bc.REMINDER)['counts']['partialDisagreed'], 2)

    def test_another_machines_turn_is_another_agents_evidence(self):
        self.fold._turns[('m2', 'a')] = turn(run='other')  # same agent id, adjacent machine
        self.fold._turns[('m', 'a')] = dt.CLOSED
        self.run_with_db(lambda db: bc.observe_reminder(db, 'a', 'r', False, blocked=False, occupied=False))
        self.assertEqual(self.owner(bc.REMINDER)['counts']['partialAgreed'], 1, 'm\'s own turn is closed')

    def test_a_missing_or_unreadable_association_is_unknown(self):
        for db in (dict(missing=True), dict(fail=True), dict(machine='')):
            self.run_with_db(lambda d: bc.observe_reminder(d, 'a', 'r', False, blocked=False, occupied=False), **db)
            self.run_with_db(lambda d: bc.observe_stranded(d, 'a', 'r', False), **db)
        self.run_with_db(lambda d: bc.observe_reminder(d, 'a', 'r', False, blocked=False, occupied=False,
                                                       agent_row=None))
        self.assertEqual(self.owner(bc.REMINDER)['unavailableByReason'], {'reminder': {'association': 4}})
        self.assertEqual(self.owner(bc.STRANDED)['unavailableByReason'], {'stranded': {'association': 3}})
        self.assertEqual(self.frames, [], 'nothing is captured for an agent with no known machine')

    def test_the_stranded_guard_reads_the_registered_machines_delivery_turn(self):
        self.fold._turns[('m', 'a')] = turn(run='r')
        self.fold._turns[('m2', 'a')] = dt.CLOSED
        calls = self.run_with_db(lambda db: bc.observe_stranded(db, 'a', 'r', False))
        self.assertEqual(calls, 1)
        self.assertEqual(self.owner(bc.STRANDED)['disagreementsByKey'],
                         [dict(site='stranded', today='false', candidate='true', count=1)])

    def claim(self, steerable, decision, busy, answer=False, fail=False):
        attempts = []

        async def read():
            attempts.append(1)
            if fail:
                raise RuntimeError('dispatch_runs unreadable')
            return answer
        asyncio.run(bc.observe_claim(None, {}, False, steerable, decision, busy, read))
        return len(attempts)

    def test_the_claim_reuses_todays_steerability_and_asks_at_most_once(self):
        decided = object()
        busy = lambda hold: {'P-1': hold, 'strict': hold}  # noqa: E731
        self.assertEqual(self.claim(True, decided, busy(HOLD_TRUE)), 0, 'today\'s True is reused')
        self.assertEqual(self.claim(False, decided, busy(HOLD_TRUE)), 0, 'today\'s False is reused')
        self.assertEqual(self.claim(NK, decided, busy(HOLD_TRUE)), 0, 'today\'s failed read is unknown, not retried')
        self.assertEqual(self.claim(None, decided, busy(HOLD_FALSE)), 0, 'a false hold needs no steerability')
        self.assertEqual(self.claim(None, decided, busy(HOLD_TRUE)), 1)
        self.assertEqual(self.claim(None, decided, busy(HOLD_UNKNOWN), answer=True), 1, 'True decides an unknown hold')
        self.assertEqual(self.claim(None, decided, busy(HOLD_TRUE), fail=True), 1)
        self.assertEqual(self.claim(None, None, None), 1, 'no hold at all: steerability may still decide it')
        claim = self.owner(bc.CLAIM)
        self.assertEqual(claim['counts']['partialDisagreed'], 2, 'today\'s False beside a held, unsteerable gate')
        self.assertEqual(claim['unavailableByReason'], {'claim': {'steerable': 2, 'busy-unread': 1}})

    def test_a_queue_with_no_hold_is_masked_by_an_occupied_run_and_unknown_without_one(self):
        bc.observe_queue(True, True, None, None)
        bc.observe_queue(False, False, None, None)
        bc.observe_queue(False, False, object(), None)
        queue = self.owner(bc.QUEUE)
        self.assertEqual(queue['counts']['partialAgreed'], 1)
        self.assertEqual(queue['unavailableByReason'], {'send-time': {'busy-unread': 1, 'shadow-error': 1}})

    def test_a_broken_recorder_never_raises(self):
        with mock.patch.object(self.recorder, 'record_owner', side_effect=RuntimeError('recorder broken')):
            bc.observe_queue(True, True, None, None)
            self.run_with_db(lambda db: bc.observe_stranded(db, 'a', 'r', False))

    def test_cancellation_still_propagates(self):
        async def cancelled():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(bc.observe_claim(None, {}, False, None, None, None, cancelled))


class _Sites:
    """A fresh recorder, fold and mirror at a real site, a fixed shadow clock, and the observer's own SQL counted."""

    NOW = '2026-10-19T00:10:00Z'

    def setUp(self):
        super().setUp()
        self.recorder, self.fold, self.mirror, self.observer_sql = (shadow.Recorder(), dt.DeliveryTurns(),
                                                                    shadow.Mirror(), [])
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'mirror', self.mirror),
                  mock.patch.object(shadow, 'clock', return_value=self.NOW)):
            p.start(); self.addCleanup(p.stop)
        for name in ('observe_reminder', 'observe_stranded'):
            self._count_sql(name)

    def _count_sql(self, name):
        real = getattr(bc, name)

        async def counted(db, *args, **kw):
            sql = self.observer_sql

            class Counting:
                def __getattr__(_, attr):
                    return getattr(db, attr)

                def execute(_, *a, **k):
                    sql.append(a[0])
                    return db.execute(*a, **k)
            return await real(Counting(), *args, **kw)
        p = mock.patch.object(bc, name, new=counted)
        p.start(); self.addCleanup(p.stop)

    def readable(self, machine):
        """Every publisher on the machine read, none holding the agent: record knowledge complete."""
        self.mirror.feed(shadow.Projection((machine, 'i1'), 'snapshot', 1, '2026-10-19T00:09:30Z', (), ()))

    def at(self, seconds_ago):
        return iso_to_epoch(self.NOW) - seconds_ago

    def owner(self, name):
        return self.recorder.report()['byPurpose'][name]['variants']['-']

    def broken_recorder(self):
        return mock.patch.object(self.recorder, 'record_owner', side_effect=RuntimeError('recorder broken'))


FRESH = TurnSpecimen('fresh-hook-turn')
CLOSED = TurnSpecimen('closed', open=False)


class SendTimeQueueSiteTest(_Sites, p7.SendTimeQueueWitness):
    test_each_specimen_holds_or_releases_the_queue_as_its_law_says = None  # the witness's own matrix runs there
    test_an_active_run_or_an_earlier_queued_run_also_holds_the_queue = None

    def occupied_send(self, agent):
        self._dead_managed_claude(agent)
        p7.INPUT_ADAPTER.apply(self._db_path, agent, CLOSED)
        self._exec(
            "INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, message_type, "
            "subject, body, priority, status, requested_at, started_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'running',datetime('now'),datetime('now'))",
            (f"run-prior-{agent}", "dashboard", agent, "start_if_possible", "channel", "info", "p", "p", "normal"))
        answer = self.client.post("/api/v1/messages/send", json={
            "from_agent": "dashboard", "to": agent, "trigger": True, "queueIfBusy": True,
            "type": "info", "subject": "behind", "body": "wait"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertNotIn(agent, self.cold_starts, 'an occupied target\'s send waits')

    def test_held_and_released_sends_are_observed_beside_todays_queue(self):
        self._assert_waits('q-held', self._queue_send('q-held', FRESH))
        self._assert_woken('q-open', self._queue_send('q-open', CLOSED))
        self.assertEqual(self.owner(bc.QUEUE)['bySite']['send-time']['observed'], 2)

    def test_an_occupied_run_compares_the_queue_whatever_happened_to_the_hold(self):
        from service.api_core import dispatch_launch
        faults = (mock.patch.object(dispatch_launch, 'turn_busy_decision', side_effect=RuntimeError('unreadable')),
                  mock.patch.object(cv, 'capture', side_effect=RuntimeError('mirror broken')),
                  mock.patch.object(cv, 'holds_delivery', side_effect=RuntimeError('compute broken')))
        for i, fault in enumerate(faults):
            with fault:
                self.occupied_send(f'q-occ-{i}')
        queue = self.owner(bc.QUEUE)
        self.assertEqual((queue['counts']['partialAgreed'], queue['counts']['unavailable']), (3, 0))

    def test_a_busy_recording_failure_leaves_the_queue_its_computed_hold(self):
        real = self.recorder.record_owner

        def busy_broken(owner, *args):
            if owner == 'busy':
                raise RuntimeError('busy recorder broken')
            return real(owner, *args)
        self.fold._turns[(p7.MACHINE, 'q-rec')] = dt.DeliveryTurn(True, 'r', self.at(100), self.at(50), 'b')
        with mock.patch.object(self.recorder, 'record_owner', side_effect=busy_broken):
            self._assert_waits('q-rec', self._queue_send('q-rec', FRESH))
        self.assertEqual(self.owner(bc.QUEUE)['counts']['partialAgreed'], 1, 'the computed P-1 hold, not unavailable')

    def test_with_nothing_occupied_a_missing_hold_is_unavailable(self):
        from service.api_core import dispatch_launch
        with mock.patch.object(dispatch_launch, 'turn_busy_decision', side_effect=RuntimeError('unreadable')):
            self._assert_woken('q-unread', self._queue_send('q-unread', FRESH))
        self.assertEqual(self.owner(bc.QUEUE)['unavailableByReason'], {'send-time': {'busy-unread': 1}})

    def test_a_failing_observer_changes_nothing(self):
        with mock.patch.object(bc, 'queue_candidate', side_effect=RuntimeError('broken')):
            self._assert_waits('q-broken', self._queue_send('q-broken', FRESH))
        with self.broken_recorder():
            self._assert_woken('q-broken-2', self._queue_send('q-broken-2', CLOSED))
        self.assertEqual(self.owner(bc.QUEUE)['unavailableByReason'], {'send-time': {'shadow-error': 1}})


class ClaimSiteTest(_Sites, p7.ClaimHoldWitness):
    test_each_specimen_holds_or_releases_the_claim_as_its_law_says = None  # the witness's own matrix runs there

    def setUp(self):
        super().setUp()
        from service import dispatch_claim
        real_observe, real_helper = bc.observe_claim, dispatch_claim._has_claimable_steerable_run
        self.attempts, self.steer_sql, self.observed = [], [], []

        async def counting_helper(db, **kw):
            class Counting:
                def __getattr__(_, attr):
                    return getattr(db, attr)

                def execute(_, *a, **k):
                    self.steer_sql.append(a[0])
                    return db.execute(*a, **k)
            return await real_helper(Counting(), **kw)

        async def observe(db, agent_row, withheld, steerable, decision, busy, read_steerable):
            self.observed.append(withheld)

            async def counted_read():
                self.attempts.append(1)
                with mock.patch.object(dispatch_claim, '_has_claimable_steerable_run', new=counting_helper):
                    return await read_steerable()
            return await real_observe(db, agent_row, withheld, steerable, decision, busy, counted_read)
        p = mock.patch.object(bc, 'observe_claim', new=observe)
        p.start(); self.addCleanup(p.stop)

    def live_delivery_turn(self, agent):
        self.fold._turns[(p7.MACHINE, agent)] = dt.DeliveryTurn(True, 'r', self.at(100), self.at(50), 'b')

    def test_a_withheld_claim_is_observed_once_and_still_withheld(self):
        self.live_delivery_turn('c-held')
        self._assert_held('c-held', self._claim('c-held', FRESH))
        self.assertEqual((self.observed, self.attempts), ([True], []), 'today\'s steerability is reused')
        self.assertEqual(self.owner(bc.CLAIM)['counts']['partialAgreed'], 1, 'held and unsteerable: compared, agreed')
        self.readable(p7.MACHINE)
        self.fold._turns[(p7.MACHINE, 'c-open')] = dt.CLOSED
        self._assert_claimed('c-open', self._claim('c-open', CLOSED))
        self.assertEqual((self.observed, self.attempts), ([True, False], []), 'a known-false hold asks nothing')
        self._assert_claimed('c-unknown', self._claim('c-unknown', CLOSED))
        self.assertEqual(self.attempts, [1], 'an unknown hold asks once: a steerable run would decide it')

    def test_a_held_candidate_today_released_asks_steerability_once(self):
        self.live_delivery_turn('c-live')
        self._assert_claimed('c-live', self._claim('c-live', CLOSED))
        self.assertEqual((self.attempts, self.steer_sql), ([1], []), 'a codex without steer returns before its SELECT')
        self._resident_codex('c-steer')
        self._exec("UPDATE agents SET capabilities = ? WHERE id = ?",
                   ('["resident-run","resume","interrupt","steer"]', 'c-steer'))
        self.live_delivery_turn('c-steer')
        p7.INPUT_ADAPTER.apply(self._db_path, 'c-steer', CLOSED)
        answer = self.client.post("/api/v1/dispatch/claim", json={
            "agentId": 'c-steer', "bridgeId": "bridge-c-steer", "machineId": p7.MACHINE,
            "executionModes": ["resident", "managed"]})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual((len(self.attempts), len(self.steer_sql)), (2, 1), 'a steer-capable target costs one SELECT')
        self.assertEqual(self.owner(bc.CLAIM)['disagreementsByKey'],
                         [dict(site='claim', today='false', candidate='true', count=2)])

    def test_a_commit_failure_after_a_withhold_is_still_one_observation(self):
        real_commit, failed = aiosqlite.Connection.commit, []

        async def commit(conn):
            if self.observed and not failed:
                failed.append(1)
                raise RuntimeError('commit failed')
            return await real_commit(conn)
        with mock.patch.object(aiosqlite.Connection, 'commit', new=commit):
            self._claim('c-commit', FRESH)
        self.assertEqual((failed, self.observed), ([1], [True]), 'the gate falls through as today, observed once')

    def test_a_failing_observer_changes_nothing(self):
        with mock.patch.object(bc, 'claim_candidate', side_effect=RuntimeError('broken')):
            self._assert_held('c-broken', self._claim('c-broken', FRESH))
        with self.broken_recorder():
            self._assert_claimed('c-broken-2', self._claim('c-broken-2', CLOSED))
        self.assertEqual(self.owner(bc.CLAIM)['unavailableByReason'], {'claim': {'shadow-error': 1}})


class ReminderSiteTest(_Sites, FastApiTestCase):
    _register_live_resident = rr.ReplyReminderTests._register_live_resident
    _dispatch = rr.ReplyReminderTests._dispatch
    _execute = rr.ReplyReminderTests._execute
    _fetchall = rr.ReplyReminderTests._fetchall
    _make_overdue_required_run = rr.ReplyReminderTests._make_overdue_required_run
    _reminder_events = rr.ReplyReminderTests._reminder_events
    _run_reminders = rr.ReplyReminderTests._run_reminders

    def overdue(self):
        run_id = self._make_overdue_required_run('codex')
        self.readable('linux:test-host')
        return run_id

    def assert_reminded(self, run_id, result):
        self.assertEqual([r['runId'] for r in result['reminded']], [run_id])
        self.assertEqual(len(self._reminder_events(run_id)), 1)

    def test_an_ordinary_row_is_associated_by_one_read_and_compared(self):
        run_id = self.overdue()
        self.fold._turns[('linux:test-host', 'coder')] = dt.DeliveryTurn(True, 'other', self.at(100), self.at(10), 'b')
        self.assert_reminded(run_id, self._run_reminders())
        self.assertEqual(len(self.observer_sql), 1)
        self.assertIn('FROM agents', self.observer_sql[0])
        self.assertEqual(self.owner(bc.REMINDER)['disagreementsByKey'],
                         [dict(site='reminder', today='false', candidate='true', count=1)])

    def test_an_adjacent_machines_turn_is_not_this_agents(self):
        run_id = self.overdue()
        self.fold._turns[('linux:other-host', 'coder')] = dt.DeliveryTurn(True, 'other', self.at(100), self.at(10), 'b')
        self.fold._turns[('linux:test-host', 'coder')] = dt.CLOSED
        self.assert_reminded(run_id, self._run_reminders())
        self.assertEqual(self.owner(bc.REMINDER)['counts']['partialAgreed'], 1)

    def test_the_terminal_branch_row_is_handed_to_the_observer(self):
        from service.api_core import dispatch_sweeps
        run_id = self.overdue()
        self._execute("UPDATE dispatch_runs SET dispatch_mode = 'terminal', status = 'running' WHERE id = ?", (run_id,))
        seen = []
        real = bc.observe_reminder

        async def spy(db, agent_id, run, today, **kw):
            seen.append(kw['agent_row'])
            return await real(db, agent_id, run, today, **kw)
        with mock.patch.object(dispatch_sweeps.busy_consumers, 'observe_reminder', new=spy):
            self._run_reminders()
        self.assertEqual(len(seen), 1)
        self.assertIsNot(seen[0], bc.UNREAD)
        self.assertEqual(seen[0]['id'], 'coder', 'this iteration\'s row for this target')

    def test_a_failing_observer_changes_nothing(self):
        run_id = self.overdue()
        with mock.patch.object(bc, 'other_work', side_effect=RuntimeError('broken')):
            self.assert_reminded(run_id, self._run_reminders())
        self.assertEqual(self.owner(bc.REMINDER)['unavailableByReason'], {'reminder': {'shadow-error': 1}})
        run_2 = self._dispatch(from_agent='lead', to='coder', type='request', subject='again', body='b',
                               mode='start_if_possible', createMessage=True)['runs'][0]['runId']
        self._execute("UPDATE dispatch_runs SET status = 'delivered', requested_at = '2026-01-01T00:00:00Z' "
                      "WHERE id = ?", (run_2,))
        with self.broken_recorder():
            result = self._run_reminders(run_id=run_2)
        self.assert_reminded(run_2, result)


class StrandedSiteTest(_Sites, FastApiTestCase):
    _register = sr.StrandedReplyFailTests._register
    _execute = sr.StrandedReplyFailTests._execute
    _fetchone = sr.StrandedReplyFailTests._fetchone
    _run_reaper = sr.StrandedReplyFailTests._run_reaper
    _seed_run = sr.StrandedReplyFailTests._seed_run

    def setUp(self):
        super().setUp()
        self._register('sc-manager')
        self._register('coder', machineId='linux:test-host')

    def status(self, run_id):
        return self._fetchone('SELECT status FROM dispatch_runs WHERE id = ?', (run_id,))['status']

    def test_a_run_with_no_one_left_is_failed_and_observed_by_one_read(self):
        self._seed_run('run-s1', target='coder')
        self.fold._turns[('linux:test-host', 'coder')] = dt.DeliveryTurn(True, 'run-s1', self.at(100), self.at(10), 'b')
        self._run_reaper()
        self.assertEqual(self.status('run-s1'), 'failed')
        self.assertEqual(len(self.observer_sql), 1)
        self.assertEqual(self.owner(bc.STRANDED)['disagreementsByKey'],
                         [dict(site='stranded', today='false', candidate='true', count=1)])

    def test_a_run_its_turn_is_working_is_skipped_and_observed(self):
        self._seed_run('run-s2', target='coder')
        self._execute("INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_updated_at) "
                      "VALUES ('coder', 1, 'run-s2', '2026-10-19T00:00:00Z')")
        self.fold._turns[('linux:test-host', 'coder')] = dt.CLOSED
        self._run_reaper()
        self.assertEqual(self.status('run-s2'), 'delivered')
        self.assertEqual(self.owner(bc.STRANDED)['disagreementsByKey'],
                         [dict(site='stranded', today='true', candidate='false', count=1)])

    def test_a_failing_observer_changes_nothing(self):
        self._seed_run('run-s3', target='coder')
        with mock.patch.object(bc, 'stranded_candidate', side_effect=RuntimeError('broken')):
            self._run_reaper()
        self.assertEqual(self.status('run-s3'), 'failed')
        self._seed_run('run-s4', target='coder')
        with self.broken_recorder():
            self._run_reaper()
        self.assertEqual(self.status('run-s4'), 'failed')
        self.assertEqual(self.owner(bc.STRANDED)['unavailableByReason'], {'stranded': {'shadow-error': 1}})
