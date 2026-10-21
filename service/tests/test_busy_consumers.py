"""C6's four busy consumers beside today's (status-switch S2c slice 4).

Three kinds of witness, kept apart: the pure candidates and their masks; the fold's record freshness, a baseline of its
own; the observers' inputs and their read budget (at most one association read, at most one steerability attempt).

THE FOUR SITE WITNESSES RETIRED WITH THE 0.9.8 SWITCH (SW5). They held that the queue, claim, reminder and stranded
sites kept their own answer whatever the observer did. After the switch those sites read the operational view itself
and call no observer; the plan withdraws this slice's formulas (page 4, item 11), and its module goes in SW7. The sites'
obligations are held by the P7 witnesses and the switched-reader tests instead.
"""
import asyncio
import unittest
from unittest import mock

import aiosqlite

from service.api_core import busy_consumers as bc, comms_view as cv, delivery_turns as dt
from service.api_core import partial_status_shadow as shadow
from service.clock import iso_to_epoch
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
            ('an open record whose last advance is unknown', frame(holder(advanced=NK)), U('record-knowledge')),
            ('no readable publisher and an unknown delivery turn', frame(others_unknown=True, delivery=dt.NOTHING_KNOWN),
             U('record-knowledge')),
            ('records known false and an unknown delivery turn', frame(holder(open_=False), delivery=dt.NOTHING_KNOWN),
             U('delivery-turn')),
            ('unknown records beside another run touched 10 s ago', frame(others_unknown=True, delivery=turn(run='o')),
             (True, None)),
            ('unknown records beside another run\'s untouched turn', frame(others_unknown=True,
                                                                        delivery=turn(run='o', touched=NK)),
             U('record-knowledge')),
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


class LostBindingTest(unittest.TestCase):
    """A binding the mirror could not give: capture keeps the fold's turn but has no clock (Frame.at is None)."""

    def setUp(self):
        self.fold = dt.DeliveryTurns()
        self.fold._turns[('m', 'a')] = turn(run='r')
        for p in (mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'clock', side_effect=RuntimeError('clock unreadable'))):
            p.start(); self.addCleanup(p.stop)
        self.lost = cv.capture('m', 'a')

    def test_the_capture_is_a_real_lost_binding(self):
        self.assertEqual((self.lost.at, self.lost.holders, self.lost.others_unknown), (None, (), True))
        self.assertEqual(self.lost.delivery, turn(run='r'))

    def test_a_known_turn_on_this_run_still_decides_the_reminder(self):
        self.assertEqual(bc.other_work(self.lost, 'r'), (False, None), 'same run: not other work, clock or no clock')
        self.assertEqual(bc.other_work(self.lost, 'other'), U('record-knowledge'), 'another run: unknown, not False')
        recorder = shadow.Recorder()
        with mock.patch.object(shadow, 'recorder', recorder):
            for run in ('r', 'other'):
                asyncio.run(bc.observe_reminder(None, 'a', run, False, blocked=False, occupied=False,
                                                agent_row={'machine_id': 'm'}))
        reminder = recorder.report()['byPurpose'][bc.REMINDER]['variants']['-']
        self.assertEqual((reminder['counts']['partialAgreed'], reminder['unavailableByReason']),
                         (1, {'reminder': {'record-knowledge': 1}}), 'compared, never shadow-error')

    def test_busy_is_unknown_with_a_reason_and_asks_no_owner(self):
        self.assertFalse(cv.needs_renewal(self.lost))
        self.assertEqual({law: cv.holds_delivery(self.lost, None, law) for law in cv.LAWS},
                         {law: U('record-knowledge') for law in cv.LAWS})


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
        busy = lambda hold: cv.BusyObservation({'P-1': hold, 'strict': hold})  # noqa: E731
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
