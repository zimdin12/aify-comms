"""The roster's live-worker gate: one query per roster, and the right statuses (v0.7 scan A7, A8).

`GET /agents` ran a terminal_sessions COUNT per online managed agent (22 of 84 statements at 30
agents). The gate also listed `online` and `ready` -- `ready` is not a status, and `shell` was
missing, so a cached `shell` outlived its terminal.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from service.api_core.live_process_probes import _agents_with_live_terminal_sessions, _has_live_terminal_session
from service.api_core.registration_gates import _enforce_live_worker_gate
from service.db import init_db

SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}
TERMINAL_COLUMNS = "(id, session_id, agent_id, environment_id, runtime, status, created_at, updated_at)"


class _RefusesQueries:
    async def execute(self, *args, **kwargs):
        raise AssertionError("the gate queried per agent although the roster passed its answer")


class RosterLiveWorkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "roster.db"
        asyncio.run(init_db(self.path))
        con = sqlite3.connect(self.path)
        rows = [
            ("t1", "s1", "live", "e", "hermes", "running"),
            ("vterm_1", "s2", "synth-only", "e", "hermes", "running"),
            ("t3", "s3", "stopped-only", "e", "hermes", "stopped"),
        ]
        con.executemany(f"INSERT INTO terminal_sessions {TERMINAL_COLUMNS} VALUES (?,?,?,?,?,?,'x','x')", rows)
        con.commit()
        con.close()

    def tearDown(self):
        self._tmp.cleanup()

    def _with_db(self, fn):
        async def run():
            async with aiosqlite.connect(self.path) as db:
                db.row_factory = aiosqlite.Row
                return await fn(db)
        return asyncio.run(run())

    def test_the_batch_agrees_with_the_single_agent_probe(self):
        agents = ["live", "synth-only", "stopped-only", "absent"]
        batch = self._with_db(lambda db: _agents_with_live_terminal_sessions(db, agents))
        self.assertEqual(batch, {"live"})
        for agent in agents:
            single = self._with_db(lambda db, a=agent: _has_live_terminal_session(db, a))
            self.assertEqual(single, agent in batch, agent)

    def test_the_gate_uses_the_roster_answer_and_asks_nothing(self):
        def payload(status):
            return {"status": status, "sessionMode": "managed", "runtime": "hermes"}

        gate = lambda p, agent: asyncio.run(_enforce_live_worker_gate(
            p, _RefusesQueries(), SETTINGS, agent, live_terminal_agents={"live"}))
        self.assertEqual(gate(payload("online"), "live")["status"], "online")
        self.assertEqual(gate(payload("online"), "gone")["status"], "available")
        self.assertEqual(gate(payload("shell"), "gone")["status"], "available",
                         "a cached shell with no live terminal is not a worker at its prompt")
        self.assertEqual(gate(payload("working"), "gone")["status"], "working", "control: the gate's scope is unchanged")


from unittest import mock

from service.db import get_db as _get_db
from service.tests._base import FastApiTestCase


class TheRosterRouteAsksOnceTests(FastApiTestCase):
    """v0.7.1 review (T02): the tests above call the helper and the gate, not `GET /agents`, so the
    route could stop passing its one-query answer and every one of them would stay green. Here the
    per-agent probe raises, and the route must answer without it."""

    DB_NAME = "aify-roster-route.db"

    def _seed_online_managed(self, *agent_ids):
        async def seed():
            db = await _get_db()
            try:
                for agent_id in agent_ids:
                    await db.execute(
                        "INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen) "
                        "VALUES (?, ?, 'coder', 'hermes', 'managed', 'online', ?, ?)",
                        (agent_id, agent_id, "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z"))
                await db.commit()
            finally:
                await db.close()
        asyncio.run(seed())

    def test_get_agents_answers_the_gate_from_its_one_query(self):
        self._seed_online_managed("hermes-a", "hermes-b", "hermes-c")

        async def asked_per_agent(*args, **kwargs):
            raise AssertionError("GET /agents asked for one agent's live terminal")

        # The live-status engine would derive `available` itself, and the gate returns early for that,
        # which made this test's first draft pass with the route's batch removed. Cached `online` with a
        # refresh far off is exactly the stale state the gate exists to correct.
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE
        for agent_id in ("hermes-a", "hermes-b", "hermes-c"):
            _LIVE_STATE_CACHE[agent_id] = {"status": "online", "refresh_after": "2999-01-01T00:00:00Z"}
        with mock.patch("service.api_core.registration_gates._has_live_terminal_session", asked_per_agent):
            response = self.client.get("/api/v1/agents")
        self.assertEqual(response.status_code, 200, response.text[:300])
        agents = response.json()["agents"]
        self.assertEqual({agents[a].get("statusNote", "") for a in ("hermes-a", "hermes-b", "hermes-c")},
                         {"no-live-worker (Plan 5 read-path gate)"},
                         "control: the gate itself downgraded each cached online agent")
