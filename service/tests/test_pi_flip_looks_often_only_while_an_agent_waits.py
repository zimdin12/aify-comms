# deprecated-runtime: pi
"""Pi scans at startup and on committed agents writes, timing only a pending agent's recheck.

The registration-specific fast path stays pinned here; the generic post-commit signal is exercised
through real routes and reporting checkouts in test_pi_flip_committed_wakes.py.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import service.pi_resident_flip as flip
from service.db import init_db
from service.routers.api_v2 import router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None

    async def notify_agent(self, *_args, **_kwargs):
        return None


class TheInterval(unittest.TestCase):
    def test_only_a_waiting_agent_has_a_recheck_timeout(self):
        self.assertEqual(flip.next_flip_check_delay(1), flip.FLIP_CHECK_WHILE_WAITING_S)
        self.assertIsNone(flip.next_flip_check_delay(0))


class TheLoop(unittest.TestCase):
    """Advance timed waits explicitly; a non-timed, unset wait stops the harness. No sleeps."""

    def _looks(self, drain, *, wake_after_error=False):
        timeouts = []
        injected = False

        async def controlled_wait(waiter, timeout):
            nonlocal injected
            timeouts.append(timeout)
            if wake_after_error and not injected and drain.await_count:
                injected = True
                flip.request_pi_flip_check()
            if flip._wake.is_set():
                return await waiter  # Exercise the real event, including a wake during the scan.
            waiter.close()
            if timeout is None or len(timeouts) >= 4:
                raise asyncio.CancelledError
            raise asyncio.TimeoutError  # Advance a configured timer, never invent an idle wake.

        async def body():
            with self.assertRaises(asyncio.CancelledError):
                await flip._periodic_pi_resident_flip_loop()

        with mock.patch.object(flip, "_drain_and_flip_pi_resident_agents", drain), \
                mock.patch.object(flip.asyncio, "wait_for", controlled_wait), \
                mock.patch.object(flip, "_wake", None):
            asyncio.run(body())
        return timeouts

    def test_after_the_startup_scan_no_idle_timeout_can_cause_more_queries(self):
        drain = mock.AsyncMock(return_value=0)
        waits = self._looks(drain)
        self.assertEqual(waits, [None])
        self.assertEqual(drain.await_count, 1)

    def test_a_waiting_agent_gets_a_bounded_recheck_then_waits_without_a_timer(self):
        drain = mock.AsyncMock(side_effect=[1, 0, 0, 0])
        waits = self._looks(drain)
        self.assertEqual(waits, [flip.FLIP_CHECK_WHILE_WAITING_S, None])
        self.assertEqual(drain.await_count, 2)

    def test_a_write_during_the_scan_rearms_the_next_scan(self):
        calls = []

        async def scan():
            calls.append(True)
            if len(calls) == 1:
                flip.request_pi_flip_check()
            return 0

        waits = self._looks(mock.AsyncMock(side_effect=scan))
        self.assertEqual(waits, [None, None])
        self.assertEqual(len(calls), 2, "clearing after the scan lost the concurrent wake")

    def test_an_error_is_logged_and_the_next_wake_recovers_without_an_error_timer(self):
        drain = mock.AsyncMock(side_effect=[RuntimeError("fixture scan failed"), 0, 0, 0])
        with self.assertLogs("service.pi_resident_flip", level="ERROR") as logs:
            waits = self._looks(drain, wake_after_error=True)
        self.assertIn("fixture scan failed", "\n".join(logs.output))
        self.assertEqual(waits, [None, None])
        self.assertEqual(drain.await_count, 2)


class TheDrainReportsWhoIsStillWaiting(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "pi-flip-wait.db"
        asyncio.run(init_db(self._db_path))

    def tearDown(self):
        self._tmpdir.cleanup()

    def _insert_pi_resident(self, agent_id, *, open_run=False):
        now = "2026-09-16T00:00:00Z"
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO agents (id, role, name, runtime, session_mode, session_handle, runtime_state, "
                "runtime_config, capabilities, status, registered_at, last_seen) "
                "VALUES (?, 'tester', ?, 'pi', 'resident', 'h', ?, '{}', '[]', 'online', ?, ?)",
                (agent_id, agent_id, json.dumps({"pi_resident_pending_flip": True}), now, now),
            )
            if open_run:
                conn.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) "
                    "VALUES (?, 'someone', ?, 'running', ?)",
                    (f"run-{agent_id}", agent_id, now),
                )

    def test_an_agent_blocked_by_an_open_run_is_counted_as_waiting(self):
        self._insert_pi_resident("blocked", open_run=True)
        self.assertEqual(asyncio.run(flip._drain_and_flip_pi_resident_agents()), 1)

    def test_CONTROL_an_agent_that_flips_is_not_waiting_any_more(self):
        self._insert_pi_resident("free")
        self.assertEqual(asyncio.run(flip._drain_and_flip_pi_resident_agents()), 0)
        with closing(sqlite3.connect(self._db_path)) as conn, conn:
            mode = conn.execute("SELECT session_mode FROM agents WHERE id = 'free'").fetchone()[0]
        self.assertEqual(mode, "managed", "the control did not flip, so 0 would mean nothing")

    def test_no_pi_agent_at_all_is_zero(self):
        self.assertEqual(asyncio.run(flip._drain_and_flip_pi_resident_agents()), 0)


class ARegistrationWakesTheLoop(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "pi-flip-wake.db"
        asyncio.run(init_db(self._db_path))
        app = FastAPI()
        app.state.ws_manager = _DummyWS()
        app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        app.state.testing = True
        app.include_router(router, prefix="/api/v1")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._tmpdir.cleanup()

    def _register(self, agent_id, runtime, session_mode):
        seen_rows = []

        def woken():
            # WHAT THE LOOP WOULD SEE: read through a separate connection at the moment of the wake.
            with closing(sqlite3.connect(self._db_path)) as conn, conn:
                seen_rows.append(conn.execute("SELECT COUNT(*) FROM agents WHERE id = ?", (agent_id,)).fetchone()[0])

        with mock.patch("service.routers.agents.registration.request_pi_flip_check", side_effect=woken) as wake:
            response = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "tester", "runtime": runtime, "sessionMode": session_mode,
            })
        self.assertEqual(response.status_code, 200, response.text)
        return wake.call_count, seen_rows

    def test_a_pi_resident_registration_wakes_the_loop_after_its_row_is_committed(self):
        calls, seen = self._register("wake-me", "pi", "resident")
        self.assertEqual(calls, 1, "a registration that asks for a flip did not wake the loop")
        self.assertEqual(seen, [1], "the loop was woken before the registration's row was committed")

    def test_CONTROL_other_registrations_do_not_request_the_registration_fast_path(self):
        self.assertEqual(self._register("claude-resident", "claude-code", "resident")[0], 0)
        self.assertEqual(self._register("pi-managed", "pi", "managed")[0], 0)


if __name__ == "__main__":
    unittest.main()
