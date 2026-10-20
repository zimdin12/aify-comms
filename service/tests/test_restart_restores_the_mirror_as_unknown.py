"""Restart restore (0.9.8 hard switch, SW1): saved publishers come back as last-known, never fresh.

Each case ingests through the real store into a real database, drops the process-local mirror as a restart does, runs
the load, and reads the operational view (SW2). A restored publisher reads `restored`, so its agents are unknown until
that publisher's next complete snapshot comms accepts.
"""
import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, row, wire, AT, LATER

MACHINE, AGENT = ' Host ', 'alpha'
KEY = (MACHINE, 'CaseSensitive')
AFTER = '2026-10-07T09:02:00Z'


def idle(agent=AGENT, lifetime='real-old'):
    return dict(row(agent, lifetime), state='idle', stateCause='at-prompt', busy=False, turn=None)


class _Stop(BaseException):
    """Ends the lifespan at the first step after the restore, before any task starts."""


class RestartRestoresTheMirrorAsUnknown(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import mirror_restore, operational_view, partial_status_shadow as shadow
        self.shadow, self.ov, self.restore_module = shadow, operational_view, mirror_restore
        for p in (patch.object(shadow, 'mirror', shadow.Mirror()), patch.object(shadow, 'recorder', shadow.Recorder()),
                  patch.object(shadow, '_quarantined', False), patch.object(shadow, 'clock', return_value=AT)):
            p.start()
            self.addCleanup(p.stop)

    def apply(self, value, at=AT):
        """Ingest as today's aify-env publishes: every body but a retirement announces 60 s (decision 2)."""
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        if value['kind'] != 'retired':
            value = {'reportEveryMs': 60_000, **value}
        return asyncio.run(AgentStateShadowStore().apply(wire(value), at))

    def restart(self):
        """A new process: an empty mirror, then the load from the saved tables."""
        from service.db import get_db
        self.shadow.mirror = self.shadow.Mirror()
        return asyncio.run(self.restore_module.restore_mirror(get_db))

    def view(self, at=AT, agent=AGENT):
        with patch.object(self.shadow, 'clock', return_value=at):
            holders = self.ov.holders_of(self.shadow.bind(), MACHINE, agent)
        return holders, self.ov.comms_view(holders, self.ov.Messaging())

    def availability(self, at=AT):
        return self.ov._availability(self.shadow.mirror.view()[KEY], at)

    def forget(self):
        """An empty database and mirror, between subtests."""
        self.sql('DELETE FROM agent_state_shadow_rows')
        self.sql('DELETE FROM agent_state_shadow_publishers')
        self.shadow.mirror = self.shadow.Mirror()

    def sql(self, query, params=()):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute(query, params)
            db.commit()

    # -- restored is last-known and never fresh ----------------------------------------------------------

    def test_an_idle_restart_is_unknown_until_the_next_complete_snapshot(self):
        self.assertTrue(self.apply(body(agents=[idle()])).applied)
        self.assertEqual(self.view()[1].eligibility, 'known', 'control: fresh and idle before the restart')
        self.assertEqual(self.restart().restored, (KEY,))
        holders, v = self.view()
        self.assertEqual([h.availability for h in holders.holders], ['restored'])
        self.assertEqual((v.status, v.eligibility, v.busy), ('unknown', 'unknown', None))
        self.assertEqual(v.last_known, ('online', AT), 'the last-known note keeps the saved application stamp')
        self.assertEqual(self.ov.claim_decision(v, False), 'withhold')
        self.assertTrue(self.apply(body(publication=2, agents=[idle()]), at=LATER).applied)
        v = self.view(LATER)[1]
        self.assertEqual((v.status, v.eligibility, v.busy), ('online', 'known', False))
        self.assertEqual(self.ov.claim_decision(v, False), 'proceed')

    def test_a_restart_mid_turn_keeps_top_level_busy_as_last_known_and_reads_unknown(self):
        self.apply(body())
        self.restart()
        holders, v = self.view()
        self.assertEqual([(h.availability, h.busy) for h in holders.holders], [('restored', True)])
        self.assertEqual((v.status, v.eligibility, v.busy, v.holds_delivery), ('unknown', 'unknown', None, None))
        self.assertEqual(v.last_known, ('working', AT))

    def test_a_changes_publication_after_a_restart_keeps_the_publisher_restored(self):
        for name, follow in (('same generation', body('changes', publication=2, agents=[idle()])),
                             ('next generation', body('changes', generation=11, publication=1, agents=[idle()]))):
            with self.subTest(name):
                self.forget()
                self.apply(body())
                self.restart()
                self.assertTrue(self.apply(follow, at=LATER).applied)
                self.assertEqual(self.availability(LATER), 'restored')
                self.assertEqual(self.view(LATER)[1].eligibility, 'unknown')
                self.assertTrue(self.apply(body(generation=12, agents=[idle()]), at=AFTER).applied)
                self.assertEqual(self.view(AFTER)[1].eligibility, 'known', 'a complete snapshot ends it')

    def test_snapshot_then_changes_and_changes_then_changes_restore_alike(self):
        """The same rows from two histories, one complete and one not: the rows cannot say which, so both wait."""
        histories = {'snapshot-then-changes': (body(), body('changes', publication=2, agents=[idle()])),
                     'changes-then-changes': (body('changes'), body('changes', publication=2, agents=[idle()]))}
        before = {'snapshot-then-changes': 'known', 'changes-then-changes': 'unknown'}
        for name, publications in histories.items():
            with self.subTest(name):
                self.forget()
                for publication in publications:
                    self.assertTrue(self.apply(publication).applied)
                self.assertEqual(self.view()[1].eligibility, before[name], 'control: the histories differ live')
                self.restart()
                self.assertEqual(self.availability(), 'restored')
                self.assertEqual(self.view()[1].eligibility, 'unknown')

    # -- what comes back, and what stays away ------------------------------------------------------------

    def test_an_empty_table_restores_nothing(self):
        result = self.restart()
        self.assertEqual((result.restored, result.lost, result.retired, result.unreadable), ((), (), (), False))
        self.assertEqual(dict(self.shadow.mirror.view()), {})

    def test_a_retired_publisher_stays_absent(self):
        self.apply(body())
        retired = {k: v for k, v in body('unavailable', publication=2).items() if k != 'reason'}
        self.assertTrue(self.apply(dict(retired, kind='retired', reason='shutdown')).applied)
        self.assertEqual(self.restart().retired, (KEY,))
        self.assertNotIn(KEY, self.shadow.mirror.view())
        self.assertEqual(self.view()[0], self.ov.Holders((), False), 'absent, so it holds nothing and hides nothing')

    def test_an_unavailable_publisher_stays_unavailable_with_its_data_generation(self):
        self.apply(body())
        self.assertTrue(self.apply(body('unavailable', generation=11, publication=1), at=LATER).applied)
        self.restart()
        publisher = self.shadow.mirror.view()[KEY]
        self.assertEqual(self.availability(), 'unavailable')
        self.assertEqual((publisher.generation, publisher.publication, publisher.data_applied_at), (10, 1, AT),
                         'the data coordinates are the saved ones, not the cursor')
        self.assertEqual(self.view()[1].eligibility, 'unknown')
        self.assertTrue(self.apply(body('changes', generation=11, publication=2, agents=[idle()]), at=AFTER).applied)
        self.assertEqual(self.availability(AFTER), 'restored', 'an unavailable publication keeps restored')

    def test_an_unavailable_publication_after_a_restart_keeps_the_publisher_restored(self):
        self.apply(body())
        self.restart()
        self.assertTrue(self.apply(body('unavailable', publication=2), at=LATER).applied)
        self.assertEqual(self.availability(LATER), 'unavailable')
        self.assertTrue(self.apply(body('changes', publication=3, agents=[idle()]), at=AFTER).applied)
        self.assertEqual(self.availability(AFTER), 'restored', 'recovering from unavailable is not a snapshot')

    def test_the_saved_generations_and_stamps_are_kept(self):
        self.apply(body(agents=[row(), idle('beta', 'L-beta')]))
        self.apply(body('changes', publication=2, agents=[idle()]), at=LATER)
        self.restart()
        publisher = self.shadow.mirror.view()[KEY]
        self.assertEqual((publisher.generation, publisher.incarnation, publisher.publication, publisher.data_applied_at),
                         (10, 'boot', 2, LATER))
        stamps = {r.agent_id: r.origin[3] for r in publisher.rows}
        self.assertEqual(stamps, {'alpha': LATER, 'beta': AT}, 'each row keeps the time it was applied')
        self.assertEqual({r.agent_id: r.busy for r in publisher.rows}, {'alpha': False, 'beta': False})

    def test_a_generation_going_backwards_is_refused_after_a_restart_and_changes_nothing(self):
        self.apply(body(generation=10))
        self.restart()
        refused = self.apply(body(generation=9, agents=[idle()]), at=LATER)
        self.assertEqual((refused.applied, refused.reason), (False, 'stale'))
        self.assertEqual(self.availability(LATER), 'restored')
        self.assertEqual(self.shadow.mirror.view()[KEY].generation, 10)

    # -- failures fail closed --------------------------------------------------------------------------

    def test_a_corrupt_row_makes_only_its_publisher_lost(self):
        for name, saved in (('not json', '{"agentId":'), ('not a record', wire(dict(idle(), busy='yes')).decode())):
            with self.subTest(name):
                self.forget()
                self.apply(body())
                self.apply(body(instance='Other', agents=[idle('beta', 'L-beta')]))
                self.sql('UPDATE agent_state_shadow_rows SET record_json=? WHERE instance=?', (saved, 'CaseSensitive'))
                result = self.restart()
                self.assertEqual((result.lost, result.restored), ((KEY,), ((MACHINE, 'Other'),)))
                self.assertEqual(self.availability(), 'lost')
                self.assertEqual(self.view()[1].eligibility, 'unknown')
                self.assertEqual(self.view(agent='beta')[1].eligibility, 'unknown', 'a lost peer hides beta too')

    def test_unreadable_tables_quarantine_the_mirror_and_never_fail_the_start(self):
        async def broken():
            raise sqlite3.OperationalError('no such table')
        result = asyncio.run(self.restore_module.restore_mirror(broken))
        self.assertTrue(result.unreadable)
        self.assertTrue(self.shadow.bind().loss, 'every bind is a loss, so every agent is unknown')

    # -- ordering: under the ingest lock, never over an ingest, before any route --------------------------

    def test_the_load_never_overwrites_a_publisher_an_ingest_applied(self):
        self.apply(body())
        self.shadow.mirror = self.shadow.Mirror()
        self.apply(body(publication=2, agents=[idle()]), at=LATER)
        from service.db import get_db
        result = asyncio.run(self.restore_module.restore_mirror(get_db))
        self.assertEqual((result.skipped, result.restored), ((KEY,), ()))
        self.assertEqual(self.availability(LATER), 'fresh')

    def test_the_load_holds_the_ingest_lock(self):
        from service.api_core import agent_state_shadow
        from service.db import get_db

        # A fresh lock for this loop: the module's lock binds to the first loop it ever waits in, so holding it here
        # would fail after, and poison for, any other test that made it wait. The restore reads the attribute at
        # call time, so it takes this lock.
        async def scenario():
            agent_state_shadow._INGEST_LOCK = asyncio.Lock()
            await agent_state_shadow._INGEST_LOCK.acquire()
            task = asyncio.create_task(self.restore_module.restore_mirror(get_db))
            await asyncio.sleep(0.05)
            waited = not task.done()
            agent_state_shadow._INGEST_LOCK.release()
            await task
            return waited, task.result()
        self.apply(body())
        self.shadow.mirror = self.shadow.Mirror()
        real = agent_state_shadow._INGEST_LOCK
        try:
            waited, result = asyncio.run(scenario())
        finally:
            agent_state_shadow._INGEST_LOCK = real
        self.assertTrue(waited, 'the load ran while an ingest held the lock')
        self.assertEqual(result.restored, (KEY,), 'control: the load ran once the lock was free')

    def test_the_lifespan_restores_before_any_task_starts_or_route_is_served(self):
        from service import main
        calls = []

        async def restore(connect):
            calls.append('restore')

        async def first_task_step():
            calls.append('reconcile')
            raise _Stop()
        config = SimpleNamespace(name='aify', version='test', data_dir=str(Path(self._db_path).parent))
        with patch.object(main, 'get_config', return_value=config), patch.object(main, '_setup_logging'), \
                patch.object(main, 'init_db', AsyncMock()), patch.object(main, 'CONNECTION_POOL', Mock()), \
                patch.object(self.restore_module, 'restore_mirror', restore), \
                patch.object(main, '_run_dispatch_reconcile_once', first_task_step), \
                patch.object(main.asyncio, 'create_task', side_effect=AssertionError('a task started first')):
            async def enter():
                async with main.lifespan(Mock()):
                    calls.append('served')
            with self.assertRaises(_Stop):
                asyncio.run(enter())
        self.assertEqual(calls, ['restore', 'reconcile'])
