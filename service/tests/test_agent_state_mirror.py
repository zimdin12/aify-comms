"""The mirror through accepted ingest: what the operational view reads after each publication, offline only.

0.9.9 S1 deleted the comparison observer these tests used to count. What stays is the ingest's own obligation: the
mirror changes only after a commit, in commit order, and a failure after the commit leaves the publisher a known loss
rather than a fresh lie. Each read is `availability_of` on the publisher the view builds every word from.
"""
import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body as receiver_body, row, wire, AT

KEY = (' Host ', 'CaseSensitive')


def body(kind='snapshot', **extra):
    """A publication that announces its interval, so its key reads `fresh` at AT."""
    return receiver_body(kind, **{'reportEveryMs': 60_000, **extra})


class MirrorIngestTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import agent_state_mirror as state_mirror
        self.state_mirror = state_mirror
        self.mirror = state_mirror.Mirror()
        for p in (patch.object(state_mirror, 'mirror', self.mirror), patch.object(state_mirror, '_quarantined', False),
                  patch.object(state_mirror, 'clock', return_value=AT)):
            p.start(); self.addCleanup(p.stop)

    def apply(self, value, at=AT, factory=None):
        from service.api_core.agent_state_ingest import AgentStateStore
        return asyncio.run(AgentStateStore(factory).apply(wire(value), at))

    def availability(self):
        from service.api_core.operational_view import availability_of
        publisher = self.state_mirror.mirror.view().get(KEY)
        return availability_of(publisher, AT) if publisher else None

    def stored_publication(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            return db.execute('SELECT publication FROM agent_state_publishers').fetchone()[0]

    def test_prepare_failure_preserves_acceptance_and_invalidates_only_after_commit(self):
        self.apply(body())
        old = self.mirror.view()
        with patch.object(self.state_mirror, 'prepare', side_effect=RuntimeError('private prepare')):
            self.assertFalse(self.apply(body()).applied)
            self.assertIs(self.mirror.view(), old, 'a refused body touched the mirror')
            self.assertTrue(self.apply(body(publication=2)).applied)
        self.assertEqual(self.availability(), 'lost')
        self.assertEqual(self.stored_publication(), 2)

    def test_invalidation_failure_preserves_committed_result_without_fresh_lie(self):
        self.apply(body())
        with patch.object(self.mirror, 'feed', side_effect=RuntimeError('feed private')), \
             patch.object(self.mirror, 'invalidate', side_effect=RuntimeError('invalidate private')):
            self.assertTrue(self.apply(body(publication=2)).applied)
        self.assertEqual(self.availability(), 'lost', 'the replacement mirror must not read fresh')
        self.assertEqual(self.stored_publication(), 2)

    def test_concurrent_ingests_publish_in_actual_commit_order_before_close(self):
        from service.db import get_db
        from service.api_core.agent_state_ingest import AgentStateStore
        events = []
        async def scenario():
            committed = asyncio.Event()
            release = asyncio.Event()
            class Connection:
                def __init__(inner, db, label): inner.db, inner.label = db, label
                async def execute(inner, *args): return await inner.db.execute(*args)
                async def rollback(inner): await inner.db.rollback()
                async def commit(inner):
                    await inner.db.commit()
                    events.append(('commit', inner.label))
                    if inner.label == 1:
                        committed.set()
                        await release.wait()
                async def close(inner):
                    events.append(('close', inner.label))
                    await inner.db.close()
            async def first(): return Connection(await get_db(), 1)
            async def second():
                events.append(('connect', 2))
                return Connection(await get_db(), 2)
            real_feed = self.mirror.feed
            def feed(p): events.append(('feed', p.rows[0].state)); real_feed(p)
            with patch.object(self.mirror, 'feed', side_effect=feed):
                a = asyncio.create_task(AgentStateStore(first).apply(wire(body()), AT))
                await committed.wait()
                b = asyncio.create_task(AgentStateStore(second).apply(wire(body(publication=2, agents=[dict(row(), state='idle')])), AT))
                await asyncio.sleep(0)
                self.assertNotIn(('connect', 2), events)
                release.set()
                results = await asyncio.gather(a, b)
                self.assertTrue(all(r.applied for r in results))
        asyncio.run(scenario())
        self.assertEqual(events, [('commit', 1), ('feed', 'working'), ('close', 1), ('connect', 2), ('commit', 2), ('feed', 'idle'), ('close', 2)])
        self.assertEqual(self.mirror.view()[KEY].rows[0].state, 'idle')

    def test_pure_two_age_rows(self):
        from service.api_core.report_age import legacy_receipt_age
        rows = [('2026-10-07T09:02:59Z', 'fresh'), ('2026-10-07T09:03:00Z', 'stale')]
        for at, expected in rows:
            with self.subTest(at=at): self.assertEqual(legacy_receipt_age(AT, at), expected)

    def test_accepted_feed_is_immutable_to_an_earlier_binding(self):
        self.assertIsNone(self.availability(), 'CONTROL: nothing published yet')
        self.assertTrue(self.apply(body()).applied)
        bound = self.state_mirror.bind()
        self.apply(body(publication=2, agents=[dict(row(), state='idle')]))
        self.assertEqual(bound.view[KEY].rows[0].state, 'working', 'a binding taken before the feed changed under it')
        self.assertEqual(self.mirror.view()[KEY].rows[0].state, 'idle')
        with self.assertRaises((AttributeError, TypeError)):
            bound.view[KEY] = None

    def test_generation_completeness_unavailable_retention_removal_and_recovery(self):
        self.apply(body('changes'))
        self.assertEqual(self.availability(), 'incomplete', 'a changes body alone completes nothing')
        self.apply(body(publication=2))
        self.assertEqual(self.availability(), 'fresh')
        self.apply(body('unavailable', generation=20))
        self.assertEqual(self.availability(), 'unavailable')
        retained = self.mirror.view()[KEY]
        self.assertEqual((retained.data_applied_at, retained.rows[0].lifetime), (AT, 'real-old'), 'unavailable keeps the data')
        self.apply(body('changes', generation=20, publication=2))
        self.assertEqual(self.availability(), 'incomplete', 'a new generation starts incomplete')
        self.apply(body(generation=20, publication=3))
        self.apply(body('changes', generation=20, publication=4, agents=[row(lifetime='new')],
                        removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}]))
        self.apply(body('changes', generation=20, publication=5, agents=[],
                        removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}]))
        self.assertEqual([r.lifetime for r in self.mirror.view()[KEY].rows], ['new'], 'a stale lifetime removes nothing')
        self.apply(body('changes', generation=20, publication=6, agents=[],
                        removed=[{'agentId': 'alpha', 'lifetime': 'new'}]))
        self.assertEqual(self.mirror.view()[KEY].rows, ())
        self.assertEqual(self.availability(), 'fresh', 'removal keeps the publisher complete')

    def test_refusal_rollback_and_postcommit_failure_do_not_lie_about_acceptance(self):
        from service.db import get_db
        self.apply(body()); before = self.mirror.view()
        self.assertFalse(self.apply(body()).applied)
        self.assertIs(self.mirror.view(), before)
        events = []
        class Connection:
            def __init__(self, db, fail=False): self.db, self.fail = db, fail
            async def execute(self, sql, args=()):
                if self.fail and sql.startswith('INSERT INTO agent_state_rows'):
                    raise sqlite3.OperationalError('fixture rollback')
                return await self.db.execute(sql, args)
            async def commit(self):
                await self.db.commit(); events.append('commit')
            async def rollback(self): events.append('rollback'); await self.db.rollback()
            async def close(self): events.append('close'); await self.db.close()
        async def failing(): return Connection(await get_db(), True)
        with self.assertRaises(sqlite3.OperationalError): self.apply(body(publication=2), factory=failing)
        self.assertIs(self.mirror.view(), before)
        async def factory(): return Connection(await get_db())
        def broken(projection): events.append('feed'); raise RuntimeError('private error text')
        events.clear()
        with patch.object(self.mirror, 'feed', side_effect=broken):
            self.assertTrue(self.apply(body(publication=2), factory=factory).applied)
        self.assertEqual(events, ['commit', 'feed', 'close'])
        self.assertEqual(self.availability(), 'lost')
        self.assertEqual(self.stored_publication(), 2)
        self.apply(body(publication=3))
        self.assertEqual(self.availability(), 'fresh', "the key's next snapshot ends the loss")
