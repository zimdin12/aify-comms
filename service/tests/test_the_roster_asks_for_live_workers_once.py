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
