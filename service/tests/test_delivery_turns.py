"""The delivery-turn fold (status-switch S2c slice 2): C6's merge rules from their own inputs, knowledge per field.

Each test names the history it witnesses. The pure transitions, the per-lineage env evidence, the start estimate's
authority, and the two capture points (the receiver's committed publication, the heartbeat's committed statement).
"""
import asyncio
import sqlite3
import unittest
from unittest.mock import patch

from service.api_core import delivery_turns as dt
from service.api_core.partial_status_shadow import Projection, Publisher, Record
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import AT, body, row, wire

A, B = ('m', 'a'), ('m', 'b')  # two publishers (instances) on one machine
T0 = 1_000.0


def record(agent='x', lifetime='L', clock=None):
    return Record(agent, lifetime, 'working', (), clock)


def publish(fold, key, at, *records, holders=None, ages=None):
    """One committed publication by `key`, with the view it produced: every key in `holders` holds the records."""
    view = {k: Publisher(1, at, True, False, records) for k in (holders or [key])}
    fold.env_applied(Projection(key, 'delta', 1, at, records, ()), view, ages)


def iso(seconds):
    return f'1970-01-01T00:{int(seconds) // 60:02d}:{int(seconds) % 60:02d}Z'


class TransitionTest(unittest.TestCase):
    def test_a_reporter_reopen_after_a_known_close_has_a_known_start(self):
        turn = dt.reporter_busy(dt.CLOSED, 'r2', 'bridge', T0)
        self.assertEqual((turn.open, turn.run_id, turn.started_at, turn.touched_at), (True, 'r2', T0, T0))

    def test_a_healthy_reporter_close_then_reopen(self):
        turn = dt.reporter_clear(dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0), 'r1')
        self.assertEqual(turn, dt.CLOSED)
        self.assertEqual(dt.reporter_busy(turn, 'r2', 'b', T0 + 5).started_at, T0 + 5)

    def test_a_busy_on_an_unknown_turn_knows_the_run_but_not_the_start(self):
        turn = dt.reporter_busy(dt.NOTHING_KNOWN, 'r1', 'b', T0)
        self.assertEqual((turn.open, turn.run_id), (True, 'r1'))
        self.assertIs(turn.started_at, dt.UNKNOWN)

    def test_a_wrong_run_clear_on_a_seeded_turn_establishes_nothing(self):
        self.assertEqual(dt.reporter_clear(dt.NOTHING_KNOWN, 'r9'), dt.NOTHING_KNOWN)
        known = dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0)
        self.assertEqual(dt.reporter_clear(known, 'r9'), known, 'a clear for another run leaves the turn open')

    def test_the_env_changes_one_lineage_can_prove(self):
        o = lambda started, last: dt.EnvClock(True, started, last)
        c = lambda last: dt.EnvClock(False, 0, last)
        self.assertEqual(dt.env_change(None, c(5)), 'first')
        self.assertEqual(dt.env_change(o(1, 5), c(5)), 'ended', 'an end tied with the last event still ends it')
        self.assertEqual(dt.env_change(o(1, 5), o(7, 7)), 'ended', 'a new start while open proves an end between')
        self.assertEqual(dt.env_change(o(1, 5), o(1, 9)), 'advanced')
        self.assertEqual(dt.env_change(o(1, 5), o(1, 5)), 'quiet')
        self.assertEqual(dt.env_change(c(5), o(9, 9)), 'started')
        self.assertEqual(dt.env_change(c(5), c(9)), 'ambiguous-closed')
        self.assertEqual(dt.env_change(c(5), c(5)), 'quiet')

    def test_an_unblock_and_a_late_end_on_a_closed_turn_are_unknown_unless_known_closed(self):
        open_turn = dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0)
        self.assertEqual(dt.env_effect(open_turn, 'ambiguous-closed', T0), dt.NOTHING_KNOWN)
        self.assertEqual(dt.env_effect(dt.CLOSED, 'ambiguous-closed', T0), dt.CLOSED)


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.fold = dt.DeliveryTurns()
        self.fold.reporter('x', True, 'r1', 'b', T0)  # an unknown turn the reporter opened: run known, start not

    def opened(self):
        fold = dt.DeliveryTurns()
        fold.reporter('x', False, 'r0', 'b', T0)  # unknown run: establishes nothing
        fold._turns['x'] = dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0)  # a known-begun delivery turn
        return fold

    def test_a_first_sighting_establishes_nothing(self):
        fold = self.opened()
        before = fold.turn('x')
        publish(fold, A, iso(T0 + 1), record(clock=(False, 0, 5)))
        self.assertEqual(fold.turn('x'), before)

    def test_an_advance_on_a_seeded_turn_stays_unknown(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, iso(T0), record(clock=(True, 1, 1)))
        publish(fold, A, iso(T0 + 5), record(clock=(True, 1, 9)))
        self.assertEqual(fold.turn('x'), dt.NOTHING_KNOWN)

    def test_a_true_advance_touches_and_a_quiet_snapshot_does_not(self):
        fold = self.opened()
        publish(fold, A, iso(T0 + 1), record(clock=(True, 1, 1)))
        publish(fold, A, iso(T0 + 2), record(clock=(True, 1, 1)))
        self.assertEqual(fold.turn('x').touched_at, T0)
        publish(fold, A, iso(T0 + 3), record(clock=(True, 1, 4)))
        self.assertEqual(fold.turn('x').touched_at, T0 + 3)

    def test_a_tied_end_closes_and_a_new_start_while_open_closes(self):
        for later in ((False, 0, 1), (True, 7, 7)):
            with self.subTest(later=later):
                fold = self.opened()
                publish(fold, A, iso(T0 + 1), record(clock=(True, 1, 1)))
                publish(fold, A, iso(T0 + 2), record(clock=later))
                self.assertEqual(fold.turn('x'), dt.CLOSED)

    def test_another_stream_holding_the_agent_is_withheld_and_degrades(self):
        fold = self.opened()
        publish(fold, A, iso(T0 + 1), record(clock=(True, 1, 1)))
        # B's FIRST observation is closed. It must not close A's turn, and while both hold it nothing is known.
        publish(fold, B, iso(T0 + 2), record(clock=(False, 0, 9)), holders=[A, B])
        self.assertEqual(fold.turn('x'), dt.NOTHING_KNOWN)
        fold._turns['x'] = dt.reporter_busy(dt.CLOSED, 'r2', 'b', T0 + 3)
        publish(fold, A, iso(T0 + 4), record(clock=(True, 1, 1)), holders=[A])  # A unchanged: quiet, not a move
        self.assertEqual(fold.turn('x').open, True)

    def test_a_replacement_lifetime_and_a_null_turn_are_a_baseline_only(self):
        fold = self.opened()
        publish(fold, A, iso(T0 + 1), record(clock=(True, 1, 1)))
        publish(fold, A, iso(T0 + 2), record(lifetime='L2', clock=(False, 0, 9)))
        publish(fold, A, iso(T0 + 3), record(lifetime='L3', clock=None))
        publish(fold, A, iso(T0 + 4), record(lifetime='L3', clock=(False, 0, 9)))
        self.assertEqual(fold.turn('x').open, True, 'no lineage of these proved an end')


class EstimateTest(unittest.TestCase):
    """strictExpiry = the earliest appliedAt - ageMs of a turn seen to begin, while every publication was consumed."""

    def begun(self, fold, age=1000):
        publish(fold, A, iso(T0), record(clock=(False, 0, 1)))
        publish(fold, A, iso(T0 + 10), record(clock=(True, 5, 5)), ages={'x': age})

    def test_a_turn_seen_to_begin_with_every_age_has_its_minimum(self):
        fold = dt.DeliveryTurns()
        self.begun(fold, age=1000)
        publish(fold, A, iso(T0 + 20), record(clock=(True, 5, 6)), ages={'x': 15_000})
        self.assertEqual(fold.strict_expiry('m', 'x', 'L', 5), T0 + 5 + dt.STRICT_SECONDS)

    def test_a_turn_running_at_the_first_sighting_has_no_expiry(self):
        fold = dt.DeliveryTurns()
        publish(fold, A, iso(T0), record(clock=(True, 5, 5)), ages={'x': 1000})
        self.assertIs(fold.strict_expiry('m', 'x', 'L', 5), dt.UNKNOWN)

    def test_a_publication_without_an_age_makes_its_turn_unknown_for_good(self):
        fold = dt.DeliveryTurns()
        self.begun(fold)
        publish(fold, A, iso(T0 + 20), record(clock=(True, 5, 6)))
        publish(fold, A, iso(T0 + 30), record(clock=(True, 5, 7)), ages={'x': 30_000})
        self.assertIs(fold.strict_expiry('m', 'x', 'L', 5), dt.UNKNOWN)

    def test_a_withheld_same_turn_sample_is_never_recovered_by_uniqueness(self):
        for ages in (None, {'x': 9_000}):  # no age, or one that would have made an earlier minimum
            with self.subTest(ages=ages):
                fold = dt.DeliveryTurns()
                self.begun(fold, age=1000)
                publish(fold, B, iso(T0 + 12), record(clock=(True, 5, 6)), holders=[A, B], ages=ages)
                publish(fold, A, iso(T0 + 14), record(clock=(True, 5, 7)), ages={'x': 1000})
                self.assertIs(fold.strict_expiry('m', 'x', 'L', 5), dt.UNKNOWN)
                self.assertEqual(fold._estimates[('m', 'x', 'L', 5)].minimum, T0 + 9, 'M kept for diagnostics')

    def test_a_same_durable_turn_restart_keeps_minimum_and_authority(self):
        fold = dt.DeliveryTurns()
        self.begun(fold, age=1000)
        # aify-env restarted: instance B now publishes the same lifetime and turn, and A has left the view.
        publish(fold, B, iso(T0 + 40), record(clock=(True, 5, 9)), holders=[B], ages={'x': 31_000})
        self.assertEqual(fold.strict_expiry('m', 'x', 'L', 5), T0 + 9 + dt.STRICT_SECONDS)


class CapturePointsTest(FastApiTestCase):
    """The fold reads committed facts only: an applied publication, a committed heartbeat statement."""

    def setUp(self):
        super().setUp()
        self.fold = dt.DeliveryTurns()
        patcher = patch.object(dt, 'FOLD', self.fold)
        patcher.start(); self.addCleanup(patcher.stop)

    def apply(self, value):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        return asyncio.run(AgentStateShadowStore().apply(wire(value), AT))

    def test_the_receiver_feeds_the_end_of_a_turn_and_a_refused_duplicate_feeds_nothing(self):
        self.fold._turns['alpha'] = dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0)
        self.assertTrue(self.apply(body(publication=1)).applied)
        ended = row(); ended['turn'] = dict(ended['turn'], open=False)
        ended.update(state='idle', stateCause='at-prompt', busy=False)
        duplicate = body(publication=2, agents=[ended])
        self.assertTrue(self.apply(duplicate).applied)
        self.assertEqual(self.fold.turn('alpha'), dt.CLOSED)
        self.fold._turns['alpha'] = dt.reporter_busy(dt.CLOSED, 'r2', 'b', T0)
        self.assertFalse(self.apply(duplicate).applied, 'the same publication again is refused')
        self.assertEqual(self.fold.turn('alpha').open, True, 'and a refusal feeds the fold nothing')

    def test_a_publication_the_fold_cannot_read_loses_everything(self):
        self.fold._turns['alpha'] = dt.reporter_busy(dt.CLOSED, 'r1', 'b', T0)
        from service.api_core import partial_status_shadow as shadow
        with patch.object(shadow, 'prepare', side_effect=ValueError('unreadable')):
            self.assertTrue(self.apply(body(publication=1)).applied)
        self.assertEqual(self.fold.turn('alpha'), dt.NOTHING_KNOWN)

    def legacy_busy(self, agent):
        legacy = sqlite3.connect(self._db_path)
        try:
            found = legacy.execute('SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?', (agent,)).fetchone()
        finally:
            legacy.close()
        return 0 if found is None else found[0]

    def register(self, agent):
        self.assertEqual(self.client.post('/api/v1/agents', json={
            'agentId': agent, 'role': 'coder', 'runtime': 'hermes', 'sessionMode': 'managed',
            'machineId': 'linux:test', 'bridgeId': 'loop-1'}).status_code, 200)
        return lambda path='heartbeat', **extra: self.client.post(f'/api/v1/agents/{agent}/{path}',
                                                                   json={'bridgeId': 'loop-1', **extra})

    def test_the_statement_reaches_the_fold_where_the_legacy_helper_refuses_it_and_a_hook_end_never_does(self):
        post = self.register('fold-order')
        self.assertEqual(post('turn-end', turnRuntime='hermes', firedAtUs=2000, machineId='linux:test').status_code, 200)
        # Fired before the last applied end: today's helper refuses this start. C6 has no such rule, so the fold opens.
        self.assertEqual(post(turnBusy=True, turnRunId='r1', firedAtUs=1500, machineId='linux:test').status_code, 200)
        self.assertEqual((self.fold.turn('fold-order').open, self.fold.turn('fold-order').run_id), (True, 'r1'))
        self.assertEqual(self.legacy_busy('fold-order'), 0, "control: today's helper did refuse the start")
        self.assertEqual(post('turn-end', turnRuntime='hermes', firedAtUs=3000, machineId='linux:test').status_code, 200)
        self.assertEqual(self.fold.turn('fold-order').open, True, 'a harness hook reaches the fold only as an env record')
        self.assertEqual(post(turnBusy=True, turnRunId='r2').status_code, 200)
        self.assertEqual(self.legacy_busy('fold-order'), 1, 'control: the legacy read can see a start it applied')

    def test_a_committed_heartbeat_reaches_the_fold_and_one_that_fails_before_commit_does_not(self):
        agent = 'fold-agent'
        beat = self.register(agent)
        self.assertEqual(beat(turnBusy=True, turnRunId='r1').status_code, 200)
        self.assertEqual((self.fold.turn(agent).open, self.fold.turn(agent).run_id), (True, 'r1'))
        with patch('service.routers.agents.liveness._apply_turn_busy_signal', side_effect=RuntimeError('rolled back')):
            try:
                status = beat(turnBusy=False, turnRunId='r1').status_code
            except RuntimeError:
                status = 500  # the test client re-raised the server error
        self.assertEqual(status, 500)
        self.assertEqual(self.fold.turn(agent).open, True, 'a beat that never committed publishes nothing')
        self.assertEqual(beat(turnBusy=False, turnRunId='r1').status_code, 200)
        self.assertEqual(self.fold.turn(agent), dt.CLOSED)
