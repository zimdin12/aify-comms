"""aify-env's port of the turn law and the hook ordering agrees with the Python it replaces (0.9 plan P0 C3).

One table, `tests/fixtures/agent-state-law.json` in the aify-env checkout, is run here through
`turn_is_still_live` and `accept_hook_event` (against its real table, in an in-memory database), and in aify-env
through its agent-state module. A port that drifts from this service fails one side or the other on the same row.
The hook ordering's "registered machine" is aify-env's "current lifetime"; the table calls both the owner.
"""
from __future__ import annotations

import asyncio
import json
import unittest

import aiosqlite

from service.api_core.hook_event_order import accept_hook_event
from service.api_core.turn_liveness_policy import turn_is_still_live
from service.tests.test_the_env_plugin_addresses_routes_this_service_serves import env_repo

FIXTURE = "tests/fixtures/agent-state-law.json"


def _law():
    repo, reason = env_repo()
    if repo is None:
        raise unittest.SkipTest(f"no aify-env checkout to compare with: {reason}")
    path = repo / FIXTURE
    if not path.is_file():
        raise unittest.SkipTest(f"{repo} has no {FIXTURE} (an aify-env from before 0.9)")
    return json.loads(path.read_text(encoding="utf-8"))


class ThePortedTurnLawAgrees(unittest.TestCase):
    def setUp(self):
        self.law = _law()

    def test_the_turn_law_answers_every_row_as_the_port_does(self):
        now = self.law["now"]
        at = lambda ago: 0.0 if ago is None else now - ago
        self.assertGreaterEqual(len(self.law["turnLaw"]), 15)
        for row in self.law["turnLaw"]:
            with self.subTest(row=row["name"]):
                live = turn_is_still_live(started_epoch=at(row["startedAgo"]), touched_epoch=at(row["touchedAgo"]),
                                          renewable=row["renewable"], now_epoch=now,
                                          strict_seconds=float(self.law["strictSeconds"]))
                self.assertEqual(live, row["live"])

    def test_the_hook_ordering_answers_every_row_as_the_port_does(self):
        async def accepted(row) -> bool:
            async with aiosqlite.connect(":memory:") as db:
                await db.execute("CREATE TABLE agents (id TEXT PRIMARY KEY, machine_id TEXT)")
                await db.execute("CREATE TABLE agent_hook_order (agent_id TEXT PRIMARY KEY, last_at INTEGER NOT NULL, "
                                 "machine_id TEXT NOT NULL DEFAULT '')")
                await db.execute("INSERT INTO agents (id, machine_id) VALUES ('a1', ?)", (row["current"],))
                if row["last"]:
                    await db.execute("INSERT INTO agent_hook_order VALUES ('a1', ?, ?)",
                                     (row["last"]["at"], row["last"]["owner"]))
                return await accept_hook_event(db, "a1", fired_at_us=row["event"]["at"],
                                               machine_id=row["event"]["owner"], kind=row["event"]["kind"])

        self.assertGreaterEqual(len(self.law["hookOrder"]), 10)
        for row in self.law["hookOrder"]:
            with self.subTest(row=row["name"]):
                self.assertEqual(asyncio.run(accepted(row)), row["accept"])

    def test_CONTROL_a_wrong_expectation_is_caught(self):
        """The comparison can say no: flipping one row's answer must disagree with the Python."""
        row = dict(self.law["turnLaw"][1], live=not self.law["turnLaw"][1]["live"])
        now = self.law["now"]
        live = turn_is_still_live(started_epoch=now - row["startedAgo"], touched_epoch=now - row["touchedAgo"],
                                  renewable=row["renewable"], now_epoch=now, strict_seconds=1800.0)
        self.assertNotEqual(live, row["live"])


if __name__ == "__main__":
    unittest.main()
