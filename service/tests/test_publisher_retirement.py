"""A publisher that retires its key holds nothing from then on (B5, comms receiver half; plan rev3).

aify-env sends `retired` under its own key, numbered after every body it published, at a clean shutdown that proved
every lifetime it published ended. The receiver refuses any later body of that generation, deletes the key's rows and
drops it from the mirror. If dropping the key fails after the commit, K stays absent and every other publisher becomes a
known loss that ends on its own next snapshot. Each witness keeps a sibling key alive beside the retired K, so a
retirement that dropped everything could not pass for one that dropped K.

What each agent reads is asked of the operational view (0.9.9 S1 removed the comparison these tests used to read):
its holders' availability and the word they make.
"""
import asyncio
import sqlite3
from contextlib import closing
from unittest import mock

from service.api_core import agent_state_ingest as receiver, operational_view as ov, agent_state_mirror as state_mirror
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import AT, body, row, wire

MACHINE = ' Host '
OTHER = ' Other '
SHADOW_NOW = '2026-10-07T09:00:30Z'  # within the freshness window of AT
EVERY_MS = 60_000  # the full-report interval each publication announces, so its key reads fresh at SHADOW_NOW


def snapshot(instance, publication=1, *, agent='alpha', lifetime='real-old', generation=10, machine=MACHINE):
    return body(publication=publication, instance=instance, generation=generation, machineId=machine,
                agents=[row(agent=agent, lifetime=lifetime)], reportEveryMs=EVERY_MS)


def changes(instance, publication, *, agents=(), removed=(), generation=10):
    return body(kind='changes', publication=publication, instance=instance, generation=generation,
                machineId=MACHINE, agents=list(agents), removed=[dict(agentId=a, lifetime=l) for a, l in removed],
                reportEveryMs=EVERY_MS)


def retired(instance, publication, generation=10, machine=MACHINE, **extra):
    value = dict(kind='retired', machineId=machine, instance=instance, generation=generation, incarnationId='boot',
                 publication=publication, inputs={'operatorStop': 'not-tracked'}, reason='shutdown')
    value.update(extra)
    return value


class Receiver:
    """One comms receiver: its own database and mirror. Two of them can disagree."""

    def __init__(self, db_path):
        self.db_path, self.mirror = db_path, state_mirror.Mirror()

    def __enter__(self):
        import aiosqlite

        async def connect():
            db = await aiosqlite.connect(self.db_path)
            db.row_factory = aiosqlite.Row
            return db
        self._patch = mock.patch.object(state_mirror, 'mirror', self.mirror)
        self._patch.start()
        self.store = receiver.AgentStateStore(connect)
        return self

    def __exit__(self, *exc):
        self.mirror = state_mirror.mirror  # a fallback may have replaced the module mirror; keep what it installed
        self._patch.stop()


class PublisherRetirementTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.mirror = state_mirror.Mirror()
        for p in (mock.patch.object(state_mirror, 'mirror', self.mirror), mock.patch.object(state_mirror, '_quarantined', False),
                  mock.patch.object(state_mirror, 'clock', return_value=SHADOW_NOW)):
            p.start(); self.addCleanup(p.stop)

    def apply(self, value, at=AT, store=None):
        return asyncio.run((store or receiver.AgentStateStore()).apply(wire(value), applied_at=at))

    def read(self, agent='alpha', machine=MACHINE):
        """(each holder's availability, sorted; whether a publisher there is unreadable; the word the view serves)."""
        holders = ov.holders_of(state_mirror.bind(), machine, agent)
        word = ov.comms_view(holders, ov.Messaging()).status
        return tuple(sorted(h.availability for h in holders.holders)), holders.unreadable, word

    def sql(self, query, params=(), path=None):
        with closing(sqlite3.connect(path or self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(query, params).fetchall()]

    def sql_state(self):
        return (self.sql('SELECT * FROM agent_state_publishers ORDER BY instance'),
                self.sql('SELECT * FROM agent_state_rows ORDER BY instance, agent_id'))

    def cursor_kind(self, instance, path=None):
        return [r['kind'] for r in self.sql('SELECT kind FROM agent_state_publishers WHERE instance = ?',
                                            (instance,), path)]

    # ---- the retirement itself

    def test_retired_removes_key_and_resolves_ambiguity(self):
        self.assertTrue(self.apply(snapshot('K')).applied)
        self.assertTrue(self.apply(snapshot('K2')).applied)
        self.assertEqual(self.read(), (('fresh', 'fresh'), False, 'unknown'), 'CONTROL: two keys hold alpha: ambiguous')
        self.assertTrue(self.apply(retired('K', 2)).applied)
        self.assertEqual(self.read(), (('fresh',), False, 'working'), "alpha now reads K2's record alone")
        self.assertNotIn((MACHINE, 'K'), state_mirror.mirror.view())

    def test_delayed_old_body_refused_after_retirement_and_refusal_renews_nothing(self):
        self.apply(snapshot('K', publication=1))
        self.apply(retired('K', 3))
        stored = self.sql_state()
        self.assertEqual(self.apply(snapshot('K', publication=2)).reason, 'stale', 'numbered before the retirement')
        self.assertEqual(self.apply(snapshot('K', publication=4)).reason, 'retired', 'numbered after it')
        self.assertEqual(self.apply(retired('K', 3)).reason, 'duplicate')
        self.assertEqual(self.sql_state(), stored, 'a refusal renews nothing')
        self.assertNotIn((MACHINE, 'K'), state_mirror.mirror.view(), 'a refusal feeds nothing')
        self.assertTrue(self.apply(snapshot('K', publication=1, generation=11)).applied, 'a higher generation reuses K')
        self.assertIn((MACHINE, 'K'), state_mirror.mirror.view())

    def test_retired_cursor_survives_a_new_store(self):
        self.apply(snapshot('K', publication=1))
        self.apply(retired('K', 2))
        with mock.patch.object(state_mirror, 'mirror', state_mirror.Mirror()):
            # A comms restart: a new store and an empty mirror. The refusal is the database cursor's.
            self.assertEqual(self.apply(snapshot('K', publication=5), store=receiver.AgentStateStore()).reason,
                             'retired')
            self.assertNotIn((MACHINE, 'K'), state_mirror.mirror.view())

    def test_the_cursor_and_rows_record_the_retirement(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))
        self.apply(retired('K', 2), at='2026-10-07T09:00:10Z')
        [cursor] = self.sql("SELECT kind, publication, data_publication, data_applied_at FROM "
                            "agent_state_publishers WHERE instance = 'K'")
        self.assertEqual(cursor, dict(kind='retired', publication=2, data_publication=2,
                                      data_applied_at='2026-10-07T09:00:10Z'))
        self.assertEqual(self.sql("SELECT instance FROM agent_state_rows ORDER BY instance"),
                         [dict(instance='K2')], "K's rows go; K2's stay")

    def test_retired_validation_422(self):
        for bad in (retired('K', 2, agents=[row()]), retired('K', 2, removed=[]), retired('K', 2, reason='crash'),
                    {k: v for k, v in retired('K', 2).items() if k != 'reason'}):
            answer = self.client.post('/api/v1/agent-state', content=wire(bad), headers={'content-type': 'application/json'})
            self.assertEqual(answer.status_code, 422, bad)
        answer = self.client.post('/api/v1/agent-state', content=wire(retired('K', 2, build='b1')),
                                  headers={'content-type': 'application/json'})
        self.assertEqual(answer.status_code, 204, 'CONTROL: a well-formed retirement, a build included, is applied')

    # ---- a retire that fails after the commit [B5-3]

    def three_publishers(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2', agent='beta'))
        self.apply(snapshot('K3', agent='gamma', machine=OTHER))

    def test_failed_retire_leaves_k_absent_and_others_lost(self):
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.assertTrue(self.apply(retired('K', 2)).applied, 'the committed retirement stands')
        view = state_mirror.mirror.view()
        self.assertNotIn((MACHINE, 'K'), view)
        for key in ((MACHINE, 'K2'), (OTHER, 'K3')):
            self.assertTrue(view[key].loss, key)
            self.assertEqual(view[key].rows, (), key)
        self.assertEqual(self.read('beta'), ((), True, 'unknown'), "K2's agent is unknown: its publisher's rows are lost")
        self.assertEqual(self.cursor_kind('K'), ['retired'])

    def test_one_healthy_publisher_does_not_recover_the_rest(self):
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.apply(retired('K', 2))
        self.apply(snapshot('K3', publication=2, agent='gamma', machine=OTHER))
        self.assertEqual(self.read('gamma', OTHER), (('fresh',), False, 'working'), 'CONTROL: K3 is read again')
        self.assertEqual(self.read('beta')[2], 'unknown', "K3's snapshot recovered K2")
        self.apply(changes('K2', 2, agents=[row(agent='beta')]))
        self.assertEqual(self.read('beta'), (('incomplete',), False, 'unknown'), 'a changes body cannot complete a lost entry')
        self.apply(snapshot('K2', publication=3, agent='beta'))
        self.assertEqual(self.read('beta'), (('fresh',), False, 'working'), "K2's own snapshot recovers it")

    def test_failed_retire_with_unreadable_view_is_sticky(self):
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')), \
                mock.patch.object(self.mirror, 'view', side_effect=RuntimeError('view lost')):
            self.assertTrue(self.apply(retired('K', 2)).applied)
        self.assertTrue(state_mirror.bind().loss)
        self.assertTrue(self.apply(snapshot('K2', publication=2, agent='beta')).applied, 'a later publication still commits')
        self.assertTrue(state_mirror.bind().loss, "one publisher's snapshot cannot rebuild a view with no key list")

    def reset_by_a_failed_invalidation(self, publication):
        """A committed K2 ingest whose projection cannot be prepared, on a mirror whose own invalidate raises: the
        existing `invalidate_safe` fallback replaces the mirror (review 2f9480fd's path)."""
        poisoned = state_mirror.mirror
        with mock.patch.object(state_mirror, 'prepare', side_effect=RuntimeError('unpreparable')), \
                mock.patch.object(poisoned, 'invalidate', side_effect=RuntimeError('invalidate broken')):
            self.assertTrue(self.apply(snapshot('K2', publication=publication, agent='beta')).applied,
                            'the ingest still commits')
        self.assertIsNot(state_mirror.mirror, poisoned, 'CONTROL: the fallback replaced the mirror')

    def test_quarantine_survives_a_later_invalidation_reset(self):
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')), \
                mock.patch.object(self.mirror, 'view', side_effect=RuntimeError('view lost')):
            self.apply(retired('K', 2))
        self.reset_by_a_failed_invalidation(2)
        self.assertTrue(self.apply(snapshot('K2', publication=3, agent='beta')).applied)
        self.assertTrue(state_mirror.bind().loss, 'a reset handed back a readable view: the quarantine escaped')
        self.assertEqual(self.read('beta'), ((), True, 'unknown'))

    def test_a_reset_keeps_the_losses_already_known(self):
        """Review 9fc557b3 (R1 extension): an ordinary reset drops live knowledge but not a loss already recorded; K3's
        ends only on K3's own publication."""
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.apply(retired('K', 2))
        self.assertTrue(state_mirror.mirror.view()[(OTHER, 'K3')].loss, 'CONTROL: the failed retirement left K3 lost')
        self.reset_by_a_failed_invalidation(2)
        self.assertTrue(state_mirror.mirror.view()[(OTHER, 'K3')].loss, "the reset dropped K3's known loss")
        self.assertEqual(self.read('gamma', OTHER)[2], 'unknown')
        self.apply(snapshot('K3', publication=2, agent='gamma', machine=OTHER))
        self.assertEqual(self.read('gamma', OTHER), (('fresh',), False, 'working'), "K3's own snapshot ends it")

    def test_a_reset_that_cannot_list_the_known_losses_fails_closed(self):
        """Review 07997643: when the reset cannot even read which losses are known, it must not treat that as none."""
        self.three_publishers()
        with mock.patch.object(state_mirror.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.apply(retired('K', 2))
        self.assertFalse(state_mirror._quarantined, 'CONTROL: a known-before failure does not quarantine')
        poisoned = state_mirror.mirror
        with mock.patch.object(state_mirror, 'prepare', side_effect=RuntimeError('unpreparable')), \
                mock.patch.object(poisoned, 'invalidate', side_effect=RuntimeError('invalidate broken')), \
                mock.patch.object(poisoned, 'view', side_effect=RuntimeError('carry read broken')):
            self.assertTrue(self.apply(snapshot('K2', publication=2, agent='beta')).applied)
        self.assertTrue(state_mirror._quarantined, 'the unknown population is the whole view')
        self.apply(snapshot('K2', publication=3, agent='beta'))
        self.assertEqual(self.read('gamma', OTHER)[2], 'unknown', 'K3 stays lost until its own publication')

    def test_without_quarantine_the_same_reset_recovers_on_a_snapshot(self):
        """CONTROL for the test above: the same reset with no quarantine, and K2's next snapshot recovers it."""
        self.three_publishers()
        self.reset_by_a_failed_invalidation(2)
        self.assertEqual(self.read('beta')[2], 'unknown', 'the reset left K2 lost')
        self.assertTrue(self.apply(snapshot('K2', publication=3, agent='beta')).applied)
        self.assertFalse(state_mirror.bind().loss)
        self.assertEqual(self.read('beta'), (('fresh',), False, 'working'))

    # ---- rev3: the receiver's state, not the sender's outcome, says what happened

    def test_precommit_failure_leaves_k_unchanged(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))
        stored = self.sql_state()
        with mock.patch.object(receiver.AgentStateStore, '_write_retirement', side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.apply(retired('K', 2))
        self.assertEqual(self.sql_state(), stored, "K's cursor and rows are as they were")
        self.assertIn((MACHINE, 'K'), state_mirror.mirror.view())
        self.assertEqual(self.read(), (('fresh', 'fresh'), False, 'unknown'), 'both keys still hold alpha')

    def test_committed_retirement_stands_without_its_ack(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))

        def lost_ack(*args, **kwargs):
            raise ConnectionResetError('the response never reached the sender')

        with mock.patch('service.routers.agent_state.Response', side_effect=lost_ack):
            answer = self.client.post('/api/v1/agent-state', content=wire(retired('K', 2)),
                                      headers={'content-type': 'application/json'})
        self.assertEqual(answer.status_code, 500, 'the sender got no 204: its outcome is unconfirmed')
        self.assertEqual(self.cursor_kind('K'), ['retired'])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM agent_state_rows WHERE instance = 'K'"), [dict(n=0)])
        self.assertNotIn((MACHINE, 'K'), state_mirror.mirror.view())
        self.assertEqual(self.apply(retired('K', 2)).reason, 'duplicate', "the sender's retry changes nothing")
        self.assertEqual(self.apply(snapshot('K', publication=3)).reason, 'retired', 'K is not re-admitted')
        self.assertNotIn((MACHINE, 'K'), state_mirror.mirror.view())

    def test_two_receivers_can_disagree(self):
        second = f'{self._db_path}.second'
        import shutil
        shutil.copyfile(self._db_path, second)
        one, two = Receiver(str(self._db_path)), Receiver(second)
        for r in (one, two):
            with r:
                self.apply(snapshot('K'), store=r.store)
                self.apply(snapshot('K2'), store=r.store)
        with one:
            self.assertTrue(self.apply(retired('K', 2), store=one.store).applied)
        with two, mock.patch.object(receiver.AgentStateStore, '_write_retirement', side_effect=RuntimeError('x')):
            with self.assertRaises(RuntimeError):
                self.apply(retired('K', 2), store=two.store)
        self.assertEqual(self.cursor_kind('K', str(self._db_path)), ['retired'])
        self.assertEqual(self.cursor_kind('K', second), ['snapshot'])
        self.assertNotIn((MACHINE, 'K'), one.mirror.view())
        self.assertIn((MACHINE, 'K'), two.mirror.view())
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM agent_state_rows WHERE instance='K'",
                                  path=second), [dict(n=1)])
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM agent_state_rows WHERE instance='K'",
                                  path=str(self._db_path)), [dict(n=0)])
