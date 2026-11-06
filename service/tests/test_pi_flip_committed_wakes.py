# deprecated-runtime: pi
"""Issue 44: the real registration/mode switch and DB commits wake Pi without a UI subscriber."""
from contextlib import closing
import asyncio
import sqlite3
from unittest import mock

from service import pi_resident_flip as flip
from service.change_feed import CHANGE_FEED
from service.db import CONNECTION_POOL, get_db
from service.tests._base import FastApiTestCase


class CommittedAgentsWake(FastApiTestCase):
    def setUp(self):
        super().setUp()
        # Production enables the reporting checkout in lifespan; the tiny route app does not.
        CONNECTION_POOL.enable()

    def tearDown(self):
        asyncio.run(CONNECTION_POOL.aclose())
        super().tearDown()

    def _mode(self):
        with closing(sqlite3.connect(self._db_path)) as connection:
            row = connection.execute("SELECT session_mode FROM agents WHERE id = 'pi-candidate'").fetchone()
            return row[0] if row else None

    def _register(self, mode):
        response = self.client.post('/api/v1/agents', json={
            'agentId': 'pi-candidate', 'role': 'tester', 'runtime': 'pi', 'sessionMode': mode,
            'sessionHandle': 'fixture-session',
        })
        self.assertEqual(response.status_code, 200, response.text)

    def test_resident_registration_is_visible_at_the_change_feed_wake(self):
        seen = []
        with mock.patch.object(CHANGE_FEED, '_manager', None), \
                mock.patch.object(flip, 'request_pi_flip_check', side_effect=lambda: seen.append(self._mode())):
            self._register('resident')
        self.assertTrue(seen, 'the generic post-commit signal did not wake Pi')
        self.assertEqual(set(seen), {'resident'}, 'a separate reader could not see the committed candidate')

    def test_real_mode_switch_wakes_after_commit_with_no_dashboard(self):
        self._register('managed')
        self.assertEqual(self._mode(), 'managed')
        seen = []
        with mock.patch.object(CHANGE_FEED, '_manager', None), \
                mock.patch.object(flip, 'request_pi_flip_check', side_effect=lambda: seen.append(self._mode())):
            # Pi's resident override is deliberately metadata-only and requires the real force path.
            response = self.client.patch('/api/v1/agents/pi-candidate/session-mode',
                                         json={'mode': 'resident', 'force': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['changed'], response.text)
        self.assertEqual(self._mode(), 'resident')
        self.assertTrue(seen, 'mode switch landed but no post-commit wake reached Pi')
        self.assertEqual(set(seen), {'resident'}, 'the wake preceded the commit')

    def test_rollback_no_matching_rows_and_unrelated_writes_do_not_wake_but_a_commit_does(self):
        self._register('managed')

        async def exercise():
            db = await get_db()
            try:
                with mock.patch.object(CHANGE_FEED, '_manager', None), \
                        mock.patch.object(flip, 'request_pi_flip_check') as wake:
                    await db.execute("UPDATE agents SET session_mode = 'resident' WHERE id = 'pi-candidate'")
                    self.assertEqual(wake.call_count, 0, 'an uncommitted write woke Pi')
                    await db.rollback()
                    self.assertEqual(wake.call_count, 0, 'a rollback woke Pi')
                    self.assertEqual(self._mode(), 'managed')
                    await db.execute("UPDATE agents SET session_mode = 'resident' WHERE id = 'known-absent-agent'")
                    await db.commit()
                    self.assertEqual(wake.call_count, 0, 'a zero-row update woke Pi')
                    await db.execute('CREATE TABLE issue44_control (id INTEGER)')
                    await db.execute('INSERT INTO issue44_control VALUES (1)')
                    await db.commit()
                    self.assertEqual(wake.call_count, 0, 'an unrelated table woke Pi')
                    await db.execute("UPDATE agents SET session_mode = 'resident' WHERE id = 'pi-candidate'")
                    await db.commit()
                    self.assertEqual(wake.call_count, 1, 'the healthy agents commit did not wake Pi')
                    self.assertEqual(self._mode(), 'resident')
            finally:
                await db.close()

        asyncio.run(exercise())

    def _assert_liveness_commit_does_not_wake(self, statement):
        self._register('managed')

        async def exercise():
            db = await get_db()
            try:
                with mock.patch.object(CHANGE_FEED, '_manager', None), \
                        mock.patch.object(flip, 'request_pi_flip_check') as wake:
                    cursor = await db.execute(statement, ('2026-11-06T12:00:00Z', 'pi-candidate'))
                    self.assertEqual(cursor.rowcount, 1, 'control: the liveness write matched no agent')
                    await db.commit()
                    self.assertEqual(wake.call_count, 0, 'a liveness-only agents commit woke the scanner')
            finally:
                await db.close()

        asyncio.run(exercise())

    def test_a_heartbeat_shaped_last_seen_and_status_case_commit_does_not_wake(self):
        # The statement shape used by routers/agents/liveness.py, not a made-up heartbeat flag.
        self._assert_liveness_commit_does_not_wake(
            "UPDATE agents SET last_seen = ?,"
            " status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?")

    def test_a_last_present_at_only_commit_does_not_wake(self):
        self._assert_liveness_commit_does_not_wake("UPDATE agents SET last_present_at = ? WHERE id = ?")

    def test_startup_scan_finds_a_candidate_persisted_before_the_loop_existed(self):
        self._register('resident')
        self.assertEqual(self._mode(), 'resident')

        async def stop_at_first_wait(waiter, timeout):
            waiter.close()
            raise asyncio.CancelledError

        async def exercise():
            with mock.patch.object(flip.asyncio, 'wait_for', stop_at_first_wait), \
                    mock.patch.object(flip, '_wake', None), \
                    mock.patch.object(flip, '_drain_and_flip_pi_resident_agents',
                                      wraps=flip._drain_and_flip_pi_resident_agents) as drain:
                with self.assertRaises(asyncio.CancelledError):
                    await flip._periodic_pi_resident_flip_loop()
                self.assertEqual(drain.await_count, 1, 'startup waited instead of scanning persisted state')

        asyncio.run(exercise())
        self.assertEqual(self._mode(), 'managed', 'startup did not flip the persisted candidate')
