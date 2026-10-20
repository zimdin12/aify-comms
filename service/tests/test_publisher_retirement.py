"""A publisher that retires its key holds nothing from then on (B5, comms receiver half; plan rev3).

aify-env sends `retired` under its own key, numbered after every body it published, at a clean shutdown that proved
every lifetime it published ended. The receiver refuses any later body of that generation, deletes the key's rows,
drops it from the mirror, and makes the fold forget every lineage baseline on that machine. It writes no delivery
turn: proof that a publisher's workers ended clears no reporter's run. If dropping the key fails after the commit, K
stays absent and every other publisher becomes a known loss that ends on its own next snapshot. Each witness keeps a
sibling key alive beside the retired K, so a retirement that dropped everything could not pass for one that dropped K.
"""
import asyncio
import sqlite3
from contextlib import closing
from unittest import mock

from service.api_core import agent_state_shadow as receiver, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core.shadow_provenance import UNKNOWN_BUILD, builds_of
from service.api_core.shadow_windows import next_reason
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import AT, body, row, wire

MACHINE = ' Host '
OTHER = ' Other '
SHADOW_NOW = '2026-10-07T09:00:30Z'  # within the freshness window of AT
STARTED = 1700000000000000


def record(agent='alpha', lifetime='real-old', open_=True, started=STARTED, age_ms=None):
    value = row(agent=agent, lifetime=lifetime)
    value['turn'].update(open=open_, startedAtUs=started, lastEventAtUs=started)
    if age_ms is not None:
        value['turn']['ageMs'] = age_ms
    return value


def snapshot(instance, publication=1, *, agents=None, build=None, generation=10, machine=MACHINE, **one):
    value = body(publication=publication, instance=instance, generation=generation, machineId=machine,
                 agents=agents if agents is not None else [record(**one)])
    if build:
        value['build'] = build
    return value


def changes(instance, publication, *, agents=(), removed=(), generation=10):
    return body(kind='changes', publication=publication, instance=instance, generation=generation,
                machineId=MACHINE, agents=list(agents), removed=[dict(agentId=a, lifetime=l) for a, l in removed])


def retired(instance, publication, generation=10, machine=MACHINE, **extra):
    value = dict(kind='retired', machineId=machine, instance=instance, generation=generation, incarnationId='boot',
                 publication=publication, inputs={'operatorStop': 'not-tracked'}, reason='shutdown')
    value.update(extra)
    return value


class Receiver:
    """One comms receiver: its own database, mirror, fold and recorder. Two of them can disagree."""

    def __init__(self, db_path):
        self.db_path, self.mirror, self.fold, self.recorder = db_path, shadow.Mirror(), dt.DeliveryTurns(), shadow.Recorder()

    def __enter__(self):
        import aiosqlite

        async def connect():
            db = await aiosqlite.connect(self.db_path)
            db.row_factory = aiosqlite.Row
            return db
        self._patches = [mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                         mock.patch.object(shadow, 'recorder', self.recorder)]
        for p in self._patches:
            p.start()
        self.store = receiver.AgentStateShadowStore(connect)
        return self

    def __exit__(self, *exc):
        self.mirror = shadow.mirror  # a fallback may have replaced the module mirror; keep what it installed
        for p in reversed(self._patches):
            p.stop()


class PublisherRetirementTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.mirror, self.fold, self.recorder = shadow.Mirror(), dt.DeliveryTurns(), shadow.Recorder()
        for p in (mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'recorder', self.recorder),
                  mock.patch.object(shadow, 'clock', return_value=SHADOW_NOW)):
            p.start(); self.addCleanup(p.stop)

    def apply(self, value, at=AT, store=None):
        return asyncio.run((store or receiver.AgentStateShadowStore()).apply(wire(value), applied_at=at))

    def read(self, agent='alpha', machine=MACHINE):
        return shadow.record_for(shadow.bind(), (machine, agent))

    def sql(self, query, params=(), path=None):
        with closing(sqlite3.connect(path or self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(query, params).fetchall()]

    def sql_state(self):
        return (self.sql('SELECT * FROM agent_state_shadow_publishers ORDER BY instance'),
                self.sql('SELECT * FROM agent_state_shadow_rows ORDER BY instance, agent_id'))

    def cursor_kind(self, instance, path=None):
        return [r['kind'] for r in self.sql('SELECT kind FROM agent_state_shadow_publishers WHERE instance = ?',
                                            (instance,), path)]

    # ---- the retirement itself

    def test_retired_removes_key_and_resolves_ambiguity(self):
        self.assertTrue(self.apply(snapshot('K')).applied)
        self.assertTrue(self.apply(snapshot('K2', build='b2')).applied)
        self.assertEqual(self.read(), (None, 'ambiguous-association'), 'CONTROL: two keys hold alpha')
        self.assertTrue(self.apply(retired('K', 2)).applied)
        found, reason = self.read()
        self.assertIsNone(reason)
        self.assertEqual(found.origin[4], 'b2', "alpha now reads K2's record")
        self.assertNotIn((MACHINE, 'K'), shadow.mirror.view())

    def test_retired_key_contributes_no_build(self):
        self.apply(snapshot('K', build='old-build'))
        self.apply(snapshot('K2', build='b2'))
        self.assertIn('old-build', builds_of(shadow.bind()), 'CONTROL: the key counts while it publishes')
        shadow.recorder.provenance.take(shadow.bind())  # the open window's builds start from the mirror now
        self.apply(retired('K', 2))
        self.assertEqual(builds_of(shadow.bind()), frozenset({'b2'}))
        builds, _, seed = shadow.recorder.provenance.take(shadow.bind())
        self.assertEqual(builds, frozenset({'old-build', 'b2'}), 'a retirement applies no provenance to the open window')
        self.assertEqual(seed, frozenset({'b2'}), 'the next window reseeds without K')

    def test_delayed_old_body_refused_after_retirement_and_refusal_renews_nothing(self):
        self.apply(snapshot('K', publication=1))
        self.apply(retired('K', 3))
        stored = self.sql_state()
        self.assertEqual(self.apply(snapshot('K', publication=2)).reason, 'stale', 'numbered before the retirement')
        self.assertEqual(self.apply(snapshot('K', publication=4)).reason, 'retired', 'numbered after it')
        self.assertEqual(self.apply(retired('K', 3)).reason, 'duplicate')
        self.assertEqual(self.sql_state(), stored, 'a refusal renews nothing')
        self.assertNotIn((MACHINE, 'K'), shadow.mirror.view(), 'a refusal feeds nothing')
        self.assertTrue(self.apply(snapshot('K', publication=1, generation=11)).applied, 'a higher generation reuses K')
        self.assertIn((MACHINE, 'K'), shadow.mirror.view())

    def test_retired_cursor_survives_a_new_store(self):
        self.apply(snapshot('K', publication=1))
        self.apply(retired('K', 2))
        with mock.patch.object(shadow, 'mirror', shadow.Mirror()), mock.patch.object(dt, 'FOLD', dt.DeliveryTurns()):
            # A comms restart: a new store, an empty mirror and fold. The refusal is the database cursor's.
            self.assertEqual(self.apply(snapshot('K', publication=5), store=receiver.AgentStateShadowStore()).reason,
                             'retired')
            self.assertNotIn((MACHINE, 'K'), shadow.mirror.view())

    def test_the_cursor_and_rows_record_the_retirement(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))
        self.apply(retired('K', 2), at='2026-10-07T09:00:10Z')
        [cursor] = self.sql("SELECT kind, publication, data_publication, data_applied_at FROM "
                            "agent_state_shadow_publishers WHERE instance = 'K'")
        self.assertEqual(cursor, dict(kind='retired', publication=2, data_publication=2,
                                      data_applied_at='2026-10-07T09:00:10Z'))
        self.assertEqual(self.sql("SELECT instance FROM agent_state_shadow_rows ORDER BY instance"),
                         [dict(instance='K2')], "K's rows go; K2's stay")

    def test_retired_validation_422(self):
        for bad in (retired('K', 2, agents=[row()]), retired('K', 2, removed=[]), retired('K', 2, reason='crash'),
                    {k: v for k, v in retired('K', 2).items() if k != 'reason'}):
            answer = self.client.post('/api/v1/agent-state', content=wire(bad), headers={'content-type': 'application/json'})
            self.assertEqual(answer.status_code, 422, bad)
        answer = self.client.post('/api/v1/agent-state', content=wire(retired('K', 2, build='b1')),
                                  headers={'content-type': 'application/json'})
        self.assertEqual(answer.status_code, 204, 'CONTROL: a well-formed retirement, a build included, is applied')

    def test_retirement_writes_no_turn_or_origin(self):
        self.apply(snapshot('K', open_=True))
        turns, origins = dict(self.fold._turns), dict(self.fold._origin)
        self.apply(retired('K', 2))
        self.assertEqual((self.fold._turns, self.fold._origin), (turns, origins))

    # ---- the fold forgets the whole machine [B5-1]

    def test_retirement_forgets_withheld_lineage(self):
        self.apply(snapshot('K', open_=True))
        self.apply(snapshot('K2', open_=False))
        self.assertEqual(self.fold.turn(MACHINE, 'alpha'), dt.NOTHING_KNOWN, 'CONTROL: two holders withhold')
        self.apply(retired('K', 2))
        self.apply(snapshot('K2', publication=2, open_=False))
        self.assertNotEqual(self.fold.turn(MACHINE, 'alpha'), dt.CLOSED,
                            "K2's closed turn read as the end of K's open one, which the fold never saw K2 hold")

    def test_retirement_forgets_a_replaced_lineage(self):
        self.apply(snapshot('K', lifetime='L1', open_=True))       # consumed: K alone holds alpha
        self.apply(snapshot('K2', lifetime='L1', open_=False))     # withheld: two holders
        self.apply(snapshot('K', publication=2, lifetime='L2', open_=True))  # K replaces A/L1 with A/L2
        self.assertIn((MACHINE, 'alpha', 'L1'), self.fold._lineages, "CONTROL: K's L1 baseline is still held")
        self.apply(retired('K', 3))
        self.apply(snapshot('K2', publication=2, lifetime='L1', open_=False))
        self.assertNotEqual(self.fold.turn(MACHINE, 'alpha'), dt.CLOSED, "an L1 baseline K replaced survived")

    def test_retirement_forgets_a_removed_lineage(self):
        self.apply(snapshot('K', lifetime='L1', open_=True))
        self.apply(snapshot('K2', lifetime='L1', open_=False))
        self.apply(changes('K', 2, removed=[('alpha', 'L1')]))     # K removes A/L1; K2 has not republished
        self.assertIn((MACHINE, 'alpha', 'L1'), self.fold._lineages, "CONTROL: K's L1 baseline is still held")
        self.apply(retired('K', 3))
        self.apply(snapshot('K2', publication=2, lifetime='L1', open_=False))
        self.assertNotEqual(self.fold.turn(MACHINE, 'alpha'), dt.CLOSED, "an L1 baseline K removed survived")

    def test_retirement_keeps_an_unrelated_publisher_healthy(self):
        # K3 on the same machine: beta/M closed, then open with an age, so its estimate is authoritative; a reporter
        # opened the turn too. K4 on another machine holds gamma/N.
        self.apply(snapshot('K3', agent='beta', lifetime='M', open_=False, started=STARTED - 10))
        self.apply(snapshot('K3', publication=2, agent='beta', lifetime='M', open_=True, age_ms=1000))
        self.fold.reporter(MACHINE, 'beta', True, 'run-b', 'owner', 1759827600.0)
        self.apply(snapshot('K4', agent='gamma', lifetime='N', machine=OTHER))
        self.apply(snapshot('K', agent='alpha'))
        turn, origin = self.fold.turn(MACHINE, 'beta'), self.fold._origin.get((MACHINE, 'beta'))
        expiry = self.fold.strict_expiry(MACHINE, 'beta', 'M', STARTED)
        self.assertIsNot(expiry, dt.NOT_KNOWN, 'CONTROL: the estimate is authoritative before the retirement')

        self.apply(retired('K', 2))

        self.assertEqual(self.fold.turn(MACHINE, 'beta'), turn)
        self.assertEqual(self.fold._origin.get((MACHINE, 'beta')), origin)
        self.assertEqual(self.fold.strict_expiry(MACHINE, 'beta', 'M', STARTED), expiry)
        self.assertIn((OTHER, 'gamma', 'N'), self.fold._lineages, "another machine's baseline survives")
        self.assertNotIn((MACHINE, 'beta', 'M'), self.fold._lineages, "this machine's baselines are forgotten")
        self.apply(snapshot('K3', publication=3, agent='beta', lifetime='M', open_=True))
        self.assertIn((MACHINE, 'beta', 'M'), self.fold._lineages, 'the next publication of M is a first sight')
        self.apply(snapshot('K3', publication=4, agent='beta', lifetime='M', open_=False))
        self.assertEqual(self.fold.turn(MACHINE, 'beta'), dt.CLOSED, 'and the one after it, closed, writes CLOSED')

    # ---- a retire that fails after the commit [B5-3]

    def three_publishers(self):
        self.apply(snapshot('K', build='bk'))
        self.apply(snapshot('K2', agent='beta', build='b2'))
        self.apply(snapshot('K3', agent='gamma', machine=OTHER, build='b3'))
        self.recorder.take()  # start the loss accounting clean

    def test_failed_retire_leaves_k_absent_and_others_lost(self):
        self.three_publishers()
        with mock.patch.object(shadow.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.assertTrue(self.apply(retired('K', 2)).applied, 'the committed retirement stands')
        view = shadow.mirror.view()
        self.assertNotIn((MACHINE, 'K'), view)
        for key in ((MACHINE, 'K2'), (OTHER, 'K3')):
            self.assertTrue(view[key].loss, key)
            self.assertEqual(view[key].rows, (), key)
        self.assertIn(UNKNOWN_BUILD, builds_of(shadow.bind()))
        self.assertEqual(self.read('beta'), (None, 'unknown-loss'))
        builds, _, _ = shadow.recorder.provenance.take(shadow.bind())
        self.assertIn(UNKNOWN_BUILD, builds)
        _, lost, _ = shadow.recorder.take()
        self.assertEqual(lost, frozenset({(MACHINE, 'K2'), (OTHER, 'K3')}))
        self.assertEqual(self.cursor_kind('K'), ['retired'])

    def test_one_healthy_publisher_does_not_recover_the_rest(self):
        self.three_publishers()
        with mock.patch.object(shadow.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.apply(retired('K', 2))
        _, lost, _ = shadow.recorder.take()
        self.apply(snapshot('K3', publication=2, agent='gamma', machine=OTHER, build='b3'))
        self.assertIsNone(self.read('gamma', OTHER)[1], 'CONTROL: K3 compares again')
        _, still = next_reason(flush_lost=False, versions_changed=False, in_loss=lost, lost_seen=frozenset(),
                               healthy_seen=frozenset({(OTHER, 'K3')}))
        self.assertEqual(still, frozenset({(MACHINE, 'K2')}), "K3's comparison recovered K2")
        self.assertEqual(self.read('beta')[1], 'unknown-loss')
        self.apply(changes('K2', 2, agents=[record(agent='beta')]))
        self.assertEqual(self.read('beta')[1], 'unknown-knowledge', 'a changes body cannot complete a lost entry')
        self.apply(snapshot('K2', publication=3, agent='beta', build='b2'))
        self.assertIsNone(self.read('beta')[1], "K2's own snapshot recovers it")

    def test_failed_retire_with_unreadable_view_is_sticky(self):
        self.three_publishers()
        with mock.patch.object(shadow.Mirror, 'retire', side_effect=RuntimeError('mirror broken')), \
                mock.patch.object(self.mirror, 'view', side_effect=RuntimeError('view lost')):
            self.assertTrue(self.apply(retired('K', 2)).applied)
        self.assertTrue(shadow.bind().loss)
        self.assertTrue(self.apply(snapshot('K2', publication=2, agent='beta', build='b2')).applied,
                        'a later publication still commits')
        self.assertTrue(shadow.bind().loss, "one publisher's snapshot cannot rebuild a view with no key list")

    # ---- rev3: the receiver's state, not the sender's outcome, says what happened

    def test_precommit_failure_leaves_k_unchanged(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))
        stored = self.sql_state()
        with mock.patch.object(receiver.AgentStateShadowStore, '_write_retirement', side_effect=RuntimeError('disk')):
            with self.assertRaises(RuntimeError):
                self.apply(retired('K', 2))
        self.assertEqual(self.sql_state(), stored, "K's cursor and rows are as they were")
        self.assertIn((MACHINE, 'K'), shadow.mirror.view())
        self.assertEqual(self.read(), (None, 'ambiguous-association'))

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
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM agent_state_shadow_rows WHERE instance = 'K'"), [dict(n=0)])
        self.assertNotIn((MACHINE, 'K'), shadow.mirror.view())
        self.assertEqual(self.apply(retired('K', 2)).reason, 'duplicate', "the sender's retry changes nothing")
        self.assertEqual(self.apply(snapshot('K', publication=3)).reason, 'retired', 'K is not re-admitted')
        self.assertNotIn((MACHINE, 'K'), shadow.mirror.view())

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
        with two, mock.patch.object(receiver.AgentStateShadowStore, '_write_retirement', side_effect=RuntimeError('x')):
            with self.assertRaises(RuntimeError):
                self.apply(retired('K', 2), store=two.store)
        self.assertEqual(self.cursor_kind('K', str(self._db_path)), ['retired'])
        self.assertEqual(self.cursor_kind('K', second), ['snapshot'])
        self.assertNotIn((MACHINE, 'K'), one.mirror.view())
        self.assertIn((MACHINE, 'K'), two.mirror.view())
        self.assertEqual(self.sql("SELECT COUNT(*) AS n FROM agent_state_shadow_rows WHERE instance='K'",
                                  path=second), [dict(n=1)])
