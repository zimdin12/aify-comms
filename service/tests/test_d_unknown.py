"""D-UNKNOWN (s2c-owners step 6b): where an unknown delivery turn came from, counted beside busy's unknowns.

Three kinds of witness: the fold's origin through joined histories (a later quiet or first sample must not hide a
withhold or a loss, and the healthy bootstrap is the contrast); the wiring from a real capture through the real
observer into the recorder (one explanation per counted 'delivery-turn', per variant, frozen with the frame); and the
cohort reader over closed windows.
"""
import asyncio
import sqlite3
import unittest
from contextlib import closing
from unittest import mock

from service.api_core import comms_view as cv, delivery_turns as dt, delivery_unknown_report as rep
from service.api_core import partial_status_shadow as shadow
from service.api_core.claim_gating import BusyDecision
from service.tests.test_delivery_turns import A, B, T0, publish, record

OPEN, CLOSED_5, CLOSED_9 = (True, 1, 5), (False, 0, 5), (False, 0, 9)


def tag(fold, lifetimes=('L',), at=T0):
    return fold.diagnostic('m', 'x', set(lifetimes), at)


def established_close():
    """A's open-to-closed pair: the delivery turn is known closed, and A's lineage baseline is closed."""
    fold = dt.DeliveryTurns()
    publish(fold, A, '1970-01-01T00:16:40Z', record(clock=OPEN))
    publish(fold, A, '1970-01-01T00:16:41Z', record(clock=CLOSED_5))
    assert fold.turn('m', 'x') == dt.CLOSED
    return fold


class OriginTest(unittest.TestCase):
    def test_the_healthy_bootstrap_is_never_known(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, '1970-01-01T00:16:40Z', record(clock=CLOSED_5))
        self.assertEqual(tag(fold), 'deliveryUnknown:never-known:closed:no')

    def test_a_withhold_survives_the_quiet_sample_that_follows_it(self):
        fold = established_close()
        publish(fold, B, '1970-01-01T00:16:42Z', record(clock=CLOSED_5), holders=[A, B])  # another stream holds x
        publish(fold, B, '1970-01-01T00:16:43Z', holders=[B])  # B's empty snapshot
        publish(fold, A, '1970-01-01T00:16:44Z', record(clock=CLOSED_5))  # A repeats: quiet
        self.assertEqual(fold.turn('m', 'x'), dt.NOTHING_KNOWN)
        self.assertEqual(tag(fold), 'deliveryUnknown:withheld:closed:no', 'the quiet sample rewrote the cause')

    def test_a_loss_survives_the_first_sample_after_it_and_differs_from_the_healthy_bootstrap(self):
        fold = established_close()
        fold.lose_everything()
        publish(fold, A, '1970-01-01T00:16:45Z', record(clock=CLOSED_5))
        healthy = dt.DeliveryTurns()
        publish(healthy, A, '1970-01-01T00:16:45Z', record(clock=CLOSED_5))
        self.assertEqual(fold.turn('m', 'x'), healthy.turn('m', 'x'), 'the same turn either way')
        self.assertEqual((tag(fold), tag(healthy)),
                         ('deliveryUnknown:lost:closed:no', 'deliveryUnknown:never-known:closed:no'))

    def test_a_loss_while_already_unknown_keeps_never_known_so_the_category_claims_no_first_sight(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, '1970-01-01T00:16:40Z', record(clock=OPEN))  # first seen OPEN
        fold.lose_everything()
        publish(fold, A, '1970-01-01T00:16:45Z', record(clock=CLOSED_5))
        self.assertEqual(tag(fold), 'deliveryUnknown:never-known:closed:no', 'indistinguishable, as the report says')

    def test_an_ambiguous_close_of_a_known_open_turn_is_its_own_origin(self):
        fold = established_close()
        fold.reporter('m', 'x', True, 'r1', 'bridge', T0)  # known open, with a known start
        self.assertTrue(dt.delivery_known(fold.turn('m', 'x')))
        self.assertEqual(tag(fold), 'deliveryUnknown:not-eligible:closed:yes')
        publish(fold, A, '1970-01-01T00:16:46Z', record(clock=CLOSED_9))  # an unblock or a late end
        self.assertEqual(tag(fold), 'deliveryUnknown:ambiguous-closed:closed:yes')

    def test_a_busy_on_an_unknown_turn_then_an_advance_keeps_the_origin_it_had(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, '1970-01-01T00:16:40Z', record(clock=OPEN))
        fold.reporter('m', 'x', True, 'r1', 'bridge', T0)
        publish(fold, A, '1970-01-01T00:16:41Z', record(clock=(True, 1, 9)))  # advanced
        self.assertIs(fold.turn('m', 'x').started_at, dt.NOT_KNOWN)
        self.assertEqual(tag(fold), 'deliveryUnknown:never-known:open:yes')

    def test_a_wrong_run_clear_rewrites_neither_a_known_turn_nor_an_unknown_origin(self):
        fold = established_close()
        fold.reporter('m', 'x', True, 'r1', 'bridge', T0)
        fold.reporter('m', 'x', False, 'r9', 'bridge', T0 + 1)
        self.assertEqual(tag(fold), 'deliveryUnknown:not-eligible:closed:yes')
        fold = dt.DeliveryTurns()
        fold.reporter('m', 'x', True, 'r1', 'bridge', T0)
        fold.reporter('m', 'x', False, 'r9', 'bridge', T0 + 1)
        self.assertEqual(tag(fold, ()), 'deliveryUnknown:never-known:no-record:yes')

    def test_the_lineage_strata(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, '1970-01-01T00:16:40Z', record(clock=None))  # a null clock: no turn write
        self.assertEqual(tag(fold), 'deliveryUnknown:never-known:null:no')
        self.assertEqual(tag(fold, ('M',)), 'deliveryUnknown:never-known:absent:no')
        self.assertEqual(tag(fold, ('L', 'M')), 'deliveryUnknown:never-known:multiple:no', 'none picked')
        self.assertEqual(tag(fold, ()), 'deliveryUnknown:never-known:no-record:no')
        self.assertEqual(tag(fold, at=None), 'deliveryUnknown:binding-lost:null:no')

    def test_a_diagnostic_that_fails_changes_no_fold_state(self):
        def history(fold):
            publish(fold, A, '1970-01-01T00:16:40Z', record(clock=OPEN), ages={'x': 10})
            publish(fold, A, '1970-01-01T00:16:41Z', record(clock=CLOSED_5))
            fold.reporter('m', 'x', True, 'r1', 'bridge', T0)
            publish(fold, B, '1970-01-01T00:16:42Z', record(clock=CLOSED_5), holders=[A, B])
            fold.lose_everything()
            return (fold._turns, fold._lineages, fold._estimates, fold._fresh)
        healthy = history(dt.DeliveryTurns())
        faulty_fold = dt.DeliveryTurns()
        with mock.patch.object(dt, 'origin_after', side_effect=RuntimeError('diagnostic broken')), \
                mock.patch.object(dt, 'delivery_known', side_effect=RuntimeError('diagnostic broken')):
            faulty = history(faulty_fold)
        self.assertEqual(faulty, healthy)
        self.assertEqual(tag(faulty_fold), 'deliveryUnknown:unclassified:diagnostic-fault:absent:yes')


class BusyCountTest(unittest.TestCase):
    """A real Mirror, fold and recorder; the real capture and observer."""

    def setUp(self):
        self.mirror, self.fold, self.recorder = shadow.Mirror(), dt.DeliveryTurns(), shadow.Recorder()
        for p in (mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'recorder', self.recorder),
                  mock.patch.object(shadow, 'clock', return_value='2026-10-19T00:10:00Z')):
            p.start(); self.addCleanup(p.stop)

    def feed(self, busy_if, instance='i1'):
        record = shadow.Record('a', 'L', 'idle', (), (False, 0, 1), busy_if)
        self.mirror.feed(shadow.Projection(('m', instance), 'snapshot', 1, '2026-10-19T00:09:50Z', (record,), ()))

    def observe(self):
        asyncio.run(cv.observe_busy(None, {'id': 'a', 'machine_id': 'm'}, BusyDecision(False), 'claim'))
        counts, _, _ = self.recorder.take()
        return {(variant, name): n for (_, variant, site, name), n in counts.items()
                if name == 'unavailable:delivery-turn' or name.startswith('deliveryUnknown')}

    def test_each_counted_delivery_turn_unknown_has_one_explanation_per_variant(self):
        self.feed((False, False))  # a fresh idle record: only the unknown delivery turn is left
        expected = {}
        for variant in cv.LAWS:
            expected[(variant, 'unavailable:delivery-turn')] = 1
            expected[(variant, 'deliveryUnknown:never-known:absent:no')] = 1
        self.assertEqual(self.observe(), expected)

    def test_strict_and_p1_are_counted_on_their_own_answers(self):
        self.feed((False, True))  # P-1 holds on the record; strict is left with the unknown turn
        self.assertEqual(self.observe(), {('strict', 'unavailable:delivery-turn'): 1,
                                          ('strict', 'deliveryUnknown:never-known:absent:no'): 1})

    def test_only_the_selected_reason_is_explained(self):
        self.feed((False, False))
        self.mirror.invalidate(('m', 'i1'))  # record-knowledge is selected before delivery-turn
        self.assertEqual(self.observe(), {})

    def test_a_failing_diagnostic_is_a_fault_counted_with_no_strata_and_the_answers_do_not_move(self):
        self.feed((False, False))
        with mock.patch.object(self.fold, 'diagnostic', side_effect=RuntimeError('diagnostic broken')):
            faulty = self.observe()
        self.assertEqual(faulty, {(v, n): 1 for v in cv.LAWS for n in ('unavailable:delivery-turn', dt.DIAGNOSTIC_FAULT)})

    def test_the_explanation_is_frozen_with_the_frame(self):
        self.feed((False, False))
        frame = cv.capture('m', 'a')
        publish(self.fold, ('m', 'i1'), '2026-10-19T00:09:55Z', record('a', clock=(False, 0, 1)))
        self.assertEqual((frame.delivery, frame.delivery_tag),
                         (dt.NOTHING_KNOWN, 'deliveryUnknown:never-known:absent:no'), 'read with the turn, not later')
        self.assertEqual(cv.capture('m', 'a').delivery_tag, 'deliveryUnknown:never-known:closed:no', 'CONTROL')

    def test_a_capture_failure_explains_nothing(self):
        with mock.patch.object(cv, 'capture', side_effect=RuntimeError('mirror unreadable')):
            self.assertEqual(self.observe(), {})

    def test_the_recorder_counts_an_explanation_only_beside_its_reason(self):
        self.recorder.record_owner('busy', 'strict', 'claim', False, None, 'renewal', explain=('delivery-turn', 'X'))
        self.recorder.record_owner('busy', 'strict', 'claim', False, None, 'delivery-turn', explain=('delivery-turn', 'Y'))
        names = {name for (_, _, _, name) in self.recorder.take()[0]}
        self.assertEqual((('X' in names), ('Y' in names)), (False, True))


def window(opened, closed, reason=None):
    return dict(openedAt=opened, closedAt=closed, closeReason=reason)


H = lambda hours: f'2026-10-20T{hours:02d}:00:00Z' if hours < 24 else f'2026-10-21T{hours - 24:02d}:00:00Z'


class CohortTest(unittest.TestCase):
    def test_complete_pending_and_every_void(self):
        ok = {1: window(H(0), H(4), 'rotation'), 2: window(H(4), H(7), 'rotation')}
        self.assertEqual(rep.cohort(ok, 1), ('complete', None, [1, 2]))
        self.assertEqual(rep.cohort({1: window(H(0), H(6), 'rotation')}, 1), ('complete', None, [1]), 'at 6 h exactly')
        self.assertEqual(rep.cohort({1: window(H(0), H(4)), 2: window(H(4), None)}, 1)[0], 'pending')
        for reason in rep.VOID_CLOSES:
            with self.subTest(reason=reason):
                self.assertEqual(rep.cohort({1: window(H(0), H(4), reason), 2: window(H(4), H(7))}, 1)[0], 'void')
        self.assertEqual(rep.cohort({1: window(H(0), H(4), 'rotation'), 3: window(H(4), H(7))}, 1)[0], 'void')
        self.assertEqual(rep.cohort({1: window(H(0), H(4), 'rotation'), 2: window(H(5), H(7))}, 1)[0], 'void')
        self.assertEqual(rep.cohort({}, 1)[0], 'void')
        loss = {1: window(H(0), H(4), 'publication-loss'), 2: window(H(4), H(7), 'publication-recovered')}
        self.assertEqual(rep.cohort(loss, 1)[0], 'complete', 'investigated states stay in')

    def test_no_total_bound(self):
        long = {1: window(H(0), H(5), 'rotation'), 2: window(H(5), H(29), 'rotation')}
        self.assertEqual(rep.cohort(long, 1), ('complete', None, [1, 2]))

    def test_the_sum_and_the_decision(self):
        rows = [(1, 'strict', 'claim', 'unavailable:delivery-turn', 10),
                (1, 'strict', 'claim', 'deliveryUnknown:never-known:closed:no', 6),
                (1, 'strict', 'claim', 'deliveryUnknown:withheld:multiple:yes', 4)]
        totals, broken = rep.explain(rows)
        self.assertEqual((broken, rep.decide(totals[('strict', 'claim')])[1]), ([], rep.BRANCHES['never-known']))
        _, broken = rep.explain(rows[:2])
        self.assertEqual(len(broken), 1, 'four unknowns unexplained')
        _, broken = rep.explain([*rows[:2], (1, 'strict', 'claim', rep.FAULT, 4)])
        self.assertEqual(broken, [], 'a fault explains its unknown in the sum')
        self.assertEqual(rep.decide({rep.UNKNOWN: 100, 'deliveryUnknown:never-known:closed:no': 45,
                                     'deliveryUnknown:withheld:closed:no': 40, rep.FAULT: 15})[1], 'no-majority')
        self.assertTrue(rep.decide({rep.UNKNOWN: 2, rep.FAULT: 2})[1].startswith('no-causal-choice'))
        self.assertTrue(rep.decide({rep.UNKNOWN: 2, 'deliveryUnknown:unclassified:x:none:no': 2})[1]
                        .startswith('no-causal-choice'))
        self.assertEqual(rep.decide({})[1], 'no-denominator')

    def test_the_report_reads_closed_windows_only(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE status_shadow_windows (id INTEGER PRIMARY KEY, comms_build TEXT, opened_at TEXT, '
                       'closed_at TEXT, close_reason TEXT)')
            db.execute('CREATE TABLE status_shadow_window_counts (window_id INTEGER, owner TEXT, variant TEXT, '
                       'source TEXT, name TEXT, count INTEGER)')
            db.executemany('INSERT INTO status_shadow_windows VALUES (?, ?, ?, ?, ?)',
                           [(7, 'b', H(0), H(5), 'rotation'), (8, 'b', H(5), H(9), 'rotation'), (9, 'b', H(9), None, None)])
            db.executemany("INSERT INTO status_shadow_window_counts VALUES (?, 'busy', 'strict', 'claim', ?, ?)",
                           [(7, 'unavailable:delivery-turn', 3), (7, 'deliveryUnknown:lost:closed:no', 3),
                            (8, 'unavailable:delivery-turn', 1), (8, 'deliveryUnknown:lost:closed:no', 1),
                            (9, 'unavailable:delivery-turn', 50), (9, 'deliveryUnknown:withheld:closed:no', 50)])
            out = rep.report(db, 7, 'read-time')
        self.assertEqual((out['status'], [w['id'] for w in out['windows']], out['hours']), ('complete', [7, 8], 9.0))
        self.assertEqual(out['decisions']['strict']['decision'], rep.BRANCHES['lost'], 'the open window is not read')
        with closing(sqlite3.connect(':memory:')) as db:
            db.execute('CREATE TABLE status_shadow_windows (id INTEGER PRIMARY KEY, comms_build TEXT, opened_at TEXT, '
                       'closed_at TEXT, close_reason TEXT)')
            db.execute('CREATE TABLE status_shadow_window_counts (window_id INTEGER, owner TEXT, variant TEXT, '
                       'source TEXT, name TEXT, count INTEGER)')
            db.execute("INSERT INTO status_shadow_windows VALUES (7, 'b', ?, ?, 'rotation')", (H(0), H(6)))
            db.execute("INSERT INTO status_shadow_window_counts VALUES (7, 'busy', 'strict', 'claim', ?, 5)",
                       ('unavailable:delivery-turn',))
            out = rep.report(db, 7, 'read-time')
        self.assertEqual((len(out['sumBroken']), out['decisions']), (1, {}), 'a broken sum decides nothing')
