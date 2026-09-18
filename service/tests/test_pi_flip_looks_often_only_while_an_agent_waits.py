# deprecated-runtime: pi
"""The pi flip loop looks every few seconds only while a pi agent is waiting, and is woken by a registration.

MEASURED REASON, 2026-09-16: the idle service spent about 2% of a core, and this loop opened a database
connection every five seconds on a host with no pi agent at all. It now backs off when nothing waits.

What must NOT regress, and is driven here:
* a pi agent waiting on an open run is still looked at on the short interval;
* a registration that asks for a flip is looked at AT ONCE -- after its row is committed, so the look
  finds it rather than finding nothing and backing off;
* a registration that asks for nothing does not wake the loop.
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
    def test_short_while_an_agent_waits_long_when_none_does(self):
        self.assertEqual(flip.next_flip_check_delay(1), flip.FLIP_CHECK_WHILE_WAITING_S)
        self.assertEqual(flip.next_flip_check_delay(0), flip.FLIP_CHECK_WHEN_NONE_S)
        self.assertGreater(flip.FLIP_CHECK_WHEN_NONE_S, flip.FLIP_CHECK_WHILE_WAITING_S)


class TheLoop(unittest.TestCase):
    """The loop itself, with its intervals shrunk so the behaviour is observable in well under a second."""

    def _looks(self, waiting: int, *, run_for: float, poke_at: float | None = None) -> int:
        calls = []

        async def fake_drain():
            calls.append(asyncio.get_running_loop().time())
            return waiting

        async def body():
            task = asyncio.create_task(flip._periodic_pi_resident_flip_loop())
            if poke_at is not None:
                await asyncio.sleep(poke_at)
                flip.request_pi_flip_check()
                await asyncio.sleep(run_for - poke_at)
            else:
                await asyncio.sleep(run_for)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with mock.patch.object(flip, "_drain_and_flip_pi_resident_agents", fake_drain), \
                mock.patch.object(flip, "FLIP_CHECK_WHILE_WAITING_S", 0.05), \
                mock.patch.object(flip, "FLIP_CHECK_WHEN_NONE_S", 5.0):
            asyncio.run(body())
        return len(calls)

    def test_a_waiting_agent_is_looked_at_on_the_short_interval(self):
        self.assertGreaterEqual(self._looks(1, run_for=0.5), 4)

    def test_CONTROL_with_none_waiting_the_loop_looks_once_and_backs_off(self):
        # The first look comes soon after start (a pending flip survives a restart), then it waits long.
        self.assertEqual(self._looks(0, run_for=0.5), 1)

    def test_a_request_wakes_a_backed_off_loop_at_once(self):
        self.assertEqual(self._looks(0, run_for=0.5, poke_at=0.25), 2)


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

    def test_CONTROL_other_registrations_do_not_wake_it(self):
        self.assertEqual(self._register("claude-resident", "claude-code", "resident")[0], 0)
        self.assertEqual(self._register("pi-managed", "pi", "managed")[0], 0)


if __name__ == "__main__":
    unittest.main()
