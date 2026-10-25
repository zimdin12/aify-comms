"""SW2: the operational view (plan 2026-10-20-s4-hard-switch, page 1's truth table and readers).

Each test names the obligation it holds. Fixtures are built from the mirror's own dataclasses and a frozen receipt
clock; nothing here reads the live mirror, the database or the wall clock.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

from service.api_core import operational_view as ov
from service.api_core import partial_status_shadow as shadow

T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
MACHINE, AGENT = 'win32:test', 'agent-a'


def iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def record(busy, state='idle', lifetime='L1', turn_open=False, started_us=0, busy_if=(False, False), agent=AGENT):
    """The turn's clock and busy laws are accepted and dropped: since 0.9.9 S1 the mirror holds no turn timing at all,
    so no test here can hand the view one, which is the no-comms-timer property by construction."""
    return shadow.Record(agent, lifetime, state, busy)


def publisher(*rows, applied=T0, complete=True, unavailable=False, loss=False, every=60_000):
    """A publisher whose last report, received at `applied`, announced 60 s (decision 2): fresh for 180 s."""
    return shadow.Publisher(1, iso(applied), complete, unavailable, tuple(rows), loss, report=(iso(applied), every))


def binding(*publishers, at=T0, machine=MACHINE):
    view = {(machine, f'i{n}'): p for n, p in enumerate(publishers)}
    return shadow.Binding(MappingProxyType(view), iso(at))


def view_of(bind, messaging=ov.Messaging()):
    return ov.comms_view(ov.holders_of(bind, MACHINE, AGENT), messaging)


class RecordCarriesTopLevelBusy(unittest.TestCase):
    """Obligation: Record.busy is the validated top-level field, never busyIf, clock or raw evidence."""

    def _prepared(self, busy, busy_if):
        body = dict(machineId=MACHINE, instance='default', kind='snapshot', generation=1, incarnationId='inc',
                    publication=1, agents=[dict(
                        agentId=AGENT, lifetime='L1', state='working' if busy else 'idle', stateCause='x', busy=busy,
                        process=dict(state='running', verified=True),
                        turn=dict(open=busy, startedAtUs=1, lastEventAtUs=1, awaitingInput=False,
                                  busyIf=dict(strict=busy_if[0], verifiedRenewal=busy_if[1])))])
        return shadow.prepare(body, iso(T0)).rows[0]

    def test_busy_comes_from_the_top_level_field_against_a_contrary_busy_if(self):
        self.assertIs(self._prepared(True, (False, False)).busy, True)
        self.assertIs(self._prepared(False, (True, True)).busy, False)


class TheTruthTable(unittest.TestCase):
    """Obligation: page 1's table. Eligibility is known only when every holder is fresh and nothing is unreadable."""

    def test_no_holder_is_unknown(self):
        v = view_of(binding(publisher()))
        self.assertEqual((v.eligibility, v.busy, v.holds_delivery), ('unknown', None, None))

    def test_all_fresh_idle_is_the_only_known_false(self):
        v = view_of(binding(publisher(record(False))))
        self.assertEqual((v.eligibility, v.busy, v.holds_delivery), ('known', False, False))

    def test_a_fresh_idle_holder_beside_a_not_reporting_holder_is_unknown_never_false(self):
        unknown = publisher(record(False, lifetime='L0'), applied=T0 - timedelta(seconds=600), every=None)
        v = view_of(binding(publisher(record(False)), unknown))
        self.assertEqual((v.eligibility, v.busy, v.holds_delivery), ('unknown', None, None))

    def test_an_unreadable_publisher_with_no_rows_makes_a_fresh_idle_holder_unknown(self):
        for label, other in (('unavailable', publisher(unavailable=True)), ('lost', publisher(loss=True)),
                             ('incomplete', publisher(complete=False))):
            with self.subTest(label):
                v = view_of(binding(publisher(record(False)), other))
                self.assertEqual((v.eligibility, v.holds_delivery), ('unknown', None))

    def test_a_fresh_busy_holder_holds_even_beside_an_unknown_one(self):
        unknown = publisher(record(False, lifetime='L0'), applied=T0 - timedelta(seconds=600), every=None)
        v = view_of(binding(publisher(record(True, state='working')), unknown))
        self.assertEqual((v.eligibility, v.busy, v.holds_delivery), ('unknown', True, True))

    def test_any_busy_among_fresh_holders_holds_a_conflict_included(self):
        v = view_of(binding(publisher(record(False, lifetime='L1')), publisher(record(True, lifetime='L2'))))
        self.assertEqual((v.eligibility, v.busy, v.holds_delivery), ('known', True, True))

    def test_a_failed_binding_is_all_unknown(self):
        v = ov.comms_view(ov.holders_of(shadow.Binding(None, None, True), MACHINE, AGENT), ov.Messaging())
        self.assertEqual((v.eligibility, v.holds_delivery, v.status), ('unknown', None, 'unknown'))

    def test_a_missing_busy_on_a_fresh_record_is_not_known_idle(self):
        v = view_of(binding(publisher(record(None))))
        self.assertEqual((v.eligibility, v.holds_delivery), ('unknown', None))


class ReadersCheckEligibilityFirst(unittest.TestCase):
    """Obligation: unknown withholds every claim before busy and before steer; R3's readers pause on busy or unknown."""

    def test_claim_withholds_unknown_even_with_a_steerable_run(self):
        unknown = publisher(record(False, lifetime='L0'), applied=T0 - timedelta(seconds=600), every=None)
        v = view_of(binding(publisher(record(True, state='working')), unknown))
        self.assertEqual(ov.claim_decision(v, steerable_run=True), 'withhold')

    def test_claim_on_known_busy_steers_only_a_steerable_run(self):
        v = view_of(binding(publisher(record(True, state='working'))))
        self.assertEqual((ov.claim_decision(v, True), ov.claim_decision(v, False)), ('steer', 'hold'))

    def test_claim_on_known_idle_proceeds(self):
        self.assertEqual(ov.claim_decision(view_of(binding(publisher(record(False)))), False), 'proceed')

    def test_reminders_and_stranded_runs_wait_on_busy_or_unknown_and_go_on_known_idle(self):
        idle = view_of(binding(publisher(record(False))))
        busy = view_of(binding(publisher(record(True, state='working'))))
        unknown = view_of(binding(publisher()))
        for label, v, waits in (('idle', idle, False), ('busy', busy, True), ('unknown', unknown, True)):
            with self.subTest(label):
                self.assertEqual((ov.reminder_paused(v), ov.stranded_protected(v)), (waits, waits))


class TheWord(unittest.TestCase):
    """Obligation: C6 rows 1-6 with Q1, Q3 and the D8 rows; unknown shows a stale last-known note, never as current."""

    def test_an_operator_stop_reads_stopped_over_a_record(self):
        v = view_of(binding(publisher(record(False))), ov.Messaging(operator_stopped=True))
        self.assertEqual(v.status, 'stopped')

    def test_no_record_reads_unknown(self):
        self.assertEqual(view_of(binding(publisher())).status, 'unknown')

    def test_a_proven_absent_wake_handle_reads_offline(self):
        self.assertEqual(view_of(binding(publisher()), ov.Messaging(wake_handle_absent=True)).status, 'offline')

    def test_a_stale_record_reads_unknown_with_its_word_as_last_known(self):
        applied = T0 - timedelta(seconds=600)
        v = view_of(binding(publisher(record(True, state='working'), applied=applied)))
        self.assertEqual((v.status, v.last_known), ('unknown', ('working', iso(applied))))

    def test_a_fresh_record_reads_its_state_idle_as_online(self):
        self.assertEqual(view_of(binding(publisher(record(False)))).status, 'online')

    def test_a_running_run_promotes_an_idle_record_to_working(self):
        v = view_of(binding(publisher(record(False))), ov.Messaging(run_running=True, run_occupied=True))
        self.assertEqual((v.status, v.run_occupied), ('working', True))

    def test_a_conflict_of_fresh_holders_reads_unknown(self):
        v = view_of(binding(publisher(record(False, lifetime='L1')), publisher(record(False, lifetime='L2'))))
        self.assertEqual(v.status, 'unknown')


class NoCommsTimer(unittest.TestCase):
    """Obligation: comms never times turns. Two observables kept apart (page 2): turn age with receipt fixed, and
    receipt age with the turn fixed."""

    def test_turn_age_and_evaluation_time_change_nothing_while_receipt_age_is_held_fresh(self):
        answers = set()
        for hours in ('0', '1', '6', '48'):
            with self.subTest(hours):
                at = T0 + timedelta(hours=int(hours))
                started_us = int((at - timedelta(hours=int(hours) + 1)).timestamp() * 1_000_000)
                busy = publisher(record(True, state='working', turn_open=True, started_us=started_us,
                                        busy_if=(True, True)), applied=at)
                v = view_of(binding(busy, at=at))
                answers.add((v.status, v.eligibility, v.busy, v.holds_delivery))
        self.assertEqual(answers, {('working', 'known', True, True)})

    def test_receipt_age_crossing_180_seconds_moves_known_to_unknown(self):
        held = publisher(record(False))
        before = view_of(binding(held, at=T0 + timedelta(seconds=179)))
        after = view_of(binding(held, at=T0 + timedelta(seconds=181)))
        self.assertEqual((before.eligibility, after.eligibility), ('known', 'unknown'))


if __name__ == '__main__':
    unittest.main()
