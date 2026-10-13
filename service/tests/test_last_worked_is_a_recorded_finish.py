"""The pulse board's "last worked" is a recorded finish, never "now" (2026-10-14 audit, L6).

`MAX(COALESCE(finished_at, now))` turned any unfinished run, such as a stale claimed or delivered one,
into "finished now", so idle agents read "worked 0m ago". Working now is the derived status
(`workingNow`); an agent with no recorded finish has no last-worked time.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

FINISHED_AT = "2026-10-13T10:00:00Z"


class LastWorkedIsARecordedFinish(FastApiTestCase):
    DB_NAME = "aify-last-worked.db"

    def setUp(self) -> None:
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:test-host:default", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)
        for agent in ("finisher", "stale-only"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent, "role": "coder", "runtime": "claude-code", "sessionMode": "managed",
                "machineId": "linux:test-host", "bridgeId": "bridge-a", "capabilities": ["managed-run"],
            })
            self.assertEqual(registered.status_code, 200, registered.text)

        async def seed():
            async with aiosqlite.connect(self._db_path) as db:
                rows = [
                    ("run-done", "finisher", "completed", "2026-10-13T09:00:00Z", FINISHED_AT),
                    ("run-stale-a", "finisher", "claimed", "2026-10-13T11:00:00Z", None),
                    ("run-stale-b", "stale-only", "delivered", "2026-10-13T11:00:00Z", None),
                ]
                for run_id, target, status, claimed, finished in rows:
                    await db.execute(
                        "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at, claimed_at, finished_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (run_id, "lead", target, status, claimed, claimed, finished))
                await db.commit()

        asyncio.run(seed())

    def test_last_worked_is_the_recorded_finish_and_an_unfinished_run_is_not_now(self):
        pulse = self.client.get("/api/v1/analytics/pulse")
        self.assertEqual(pulse.status_code, 200, pulse.text)
        board = {row["id"]: row for row in pulse.json().get("board", pulse.json().get("agents", []))}
        self.assertIn("finisher", board, f"control: the agents are on the board: {sorted(board)}")
        self.assertIn("stale-only", board, f"control: the agents are on the board: {sorted(board)}")
        self.assertEqual(board["finisher"]["lastWorkedAt"], FINISHED_AT, "a stale unfinished run replaced the recorded finish")
        self.assertIsNone(board["stale-only"]["lastWorkedAt"], "an agent with no recorded finish read as having worked now")
