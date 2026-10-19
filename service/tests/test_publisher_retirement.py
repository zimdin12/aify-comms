"""A publisher that retires its key holds nothing from then on (B5, comms receiver half).

aify-env sends `retired` under its own key, numbered after every body it published, at a clean shutdown that proved
every lifetime it published ended. The receiver refuses any later body of that generation, deletes the key's rows,
drops it from the mirror, and makes the fold forget its lineages. It writes no delivery turn: proof that a publisher's
workers ended clears no reporter's run. Each witness keeps a sibling key K2 alive beside the retired K, so a retirement
that dropped everything could not pass for one that dropped K.
"""
import asyncio
from unittest import mock

from service.api_core import agent_state_shadow as receiver, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core.shadow_provenance import UNKNOWN_BUILD, builds_of
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import AT, body, row, wire

MACHINE = ' Host '
SHADOW_NOW = '2026-10-07T09:00:30Z'  # within the freshness window of AT


def snapshot(instance, publication=1, lifetime='real-old', open_=True, build=None, generation=10):
    record = row(lifetime=lifetime)
    record['turn']['open'] = open_
    value = body(publication=publication, instance=instance, generation=generation, agents=[record])
    if build:
        value['build'] = build
    return value


def retired(instance, publication, generation=10, **extra):
    value = dict(kind='retired', machineId=MACHINE, instance=instance, generation=generation, incarnationId='boot',
                 publication=publication, inputs={'operatorStop': 'not-tracked'}, reason='shutdown')
    value.update(extra)
    return value


class PublisherRetirementTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        self.mirror, self.fold = shadow.Mirror(), dt.DeliveryTurns()
        for p in (mock.patch.object(shadow, 'mirror', self.mirror), mock.patch.object(dt, 'FOLD', self.fold),
                  mock.patch.object(shadow, 'clock', return_value=SHADOW_NOW)):
            p.start(); self.addCleanup(p.stop)

    def apply(self, value, at=AT):
        return asyncio.run(receiver.AgentStateShadowStore().apply(wire(value), applied_at=at))

    def record(self):
        return shadow.record_for(shadow.bind(), (MACHINE, 'alpha'))

    def test_retirement_removes_the_key_and_ends_the_ambiguity(self):
        self.assertTrue(self.apply(snapshot('K')).applied)
        self.assertTrue(self.apply(snapshot('K2', build='b2')).applied)
        self.assertEqual(self.record(), (None, 'ambiguous-association'), 'CONTROL: two keys hold alpha')
        self.assertTrue(self.apply(retired('K', 2)).applied)
        record, reason = self.record()
        self.assertIsNone(reason)
        self.assertEqual(record.origin[4], 'b2', "alpha now reads K2's record")
        self.assertNotIn((MACHINE, 'K'), shadow.mirror.view())

    def test_a_retired_key_contributes_no_build(self):
        self.apply(snapshot('K', build='old-build'))
        self.apply(snapshot('K2', build='b2'))
        self.assertIn('old-build', builds_of(shadow.bind()), 'CONTROL: the key counts while it publishes')
        self.apply(retired('K', 2))
        self.assertEqual(builds_of(shadow.bind()), frozenset({'b2'}))
        self.assertNotIn(UNKNOWN_BUILD, builds_of(shadow.bind()))

    def test_a_late_body_of_the_retired_generation_is_refused_and_a_successor_is_admitted(self):
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

    def test_the_cursor_and_rows_record_the_retirement(self):
        self.apply(snapshot('K'))
        self.apply(snapshot('K2'))
        self.apply(retired('K', 2), at='2026-10-07T09:00:10Z')
        [cursor] = self.sql("SELECT kind, publication, data_publication, data_applied_at FROM "
                            "agent_state_shadow_publishers WHERE instance = 'K'")
        self.assertEqual(cursor, dict(kind='retired', publication=2, data_publication=2,
                                      data_applied_at='2026-10-07T09:00:10Z'))
        rows = self.sql("SELECT instance FROM agent_state_shadow_rows ORDER BY instance")
        self.assertEqual(rows, [dict(instance='K2')], "K's rows go; K2's stay")

    def test_a_retirement_carries_nothing_else(self):
        for bad in (retired('K', 2, agents=[row()]), retired('K', 2, removed=[]), retired('K', 2, reason='crash'),
                    {k: v for k, v in retired('K', 2).items() if k != 'reason'}):
            answer = self.client.post('/api/v1/agent-state', content=wire(bad), headers={'content-type': 'application/json'})
            self.assertEqual(answer.status_code, 422, bad)
        answer = self.client.post('/api/v1/agent-state', content=wire(retired('K', 2, build='b1')),
                                  headers={'content-type': 'application/json'})
        self.assertEqual(answer.status_code, 204, 'CONTROL: a well-formed retirement, a build included, is applied')

    def test_the_fold_forgets_the_retired_lineage(self):
        # K consumed (alpha, L) open; K2 holds it closed, so the fold withholds; K retires; K2 publishes again.
        self.apply(snapshot('K', open_=True))
        self.apply(snapshot('K2', open_=False))
        self.assertEqual(self.fold.turn(MACHINE, 'alpha'), dt.NOTHING_KNOWN, 'CONTROL: two holders withhold')
        self.apply(retired('K', 2))
        self.apply(snapshot('K2', publication=2, open_=False))
        self.assertNotEqual(self.fold.turn(MACHINE, 'alpha'), dt.CLOSED,
                            "K2's closed turn read as the end of K's open one, which the fold never saw K2 hold")

    def test_a_retirement_writes_no_turn_and_no_origin(self):
        self.apply(snapshot('K', open_=True))
        turns, origins = dict(self.fold._turns), dict(self.fold._origin)  # frozen values; NOT_KNOWN by identity
        self.apply(retired('K', 2))
        self.assertEqual((self.fold._turns, self.fold._origin), (turns, origins))

    def test_a_failed_retire_trusts_neither_the_mirror_nor_the_fold(self):
        self.apply(snapshot('K', open_=True))
        self.apply(snapshot('K2'))
        with mock.patch.object(shadow.Mirror, 'retire', side_effect=RuntimeError('mirror broken')):
            self.assertTrue(self.apply(retired('K', 2)).applied, 'the committed retirement stands')
        self.assertEqual(dict(shadow.mirror.view()), {}, 'no copy that may still hold K survives')
        self.assertEqual(self.fold._lineages, {})
        self.assertEqual(self.sql("SELECT kind FROM agent_state_shadow_publishers WHERE instance = 'K'"),
                         [dict(kind='retired')])

    def sql(self, query, params=()):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(query, params).fetchall()]

    def sql_state(self):
        return (self.sql('SELECT * FROM agent_state_shadow_publishers ORDER BY instance'),
                self.sql('SELECT * FROM agent_state_shadow_rows ORDER BY instance, agent_id'))
