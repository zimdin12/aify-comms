"""An interrupt on a run its target never claimed cancels it in the service (dashboard-manager, 2026-10-13).

A request to an agent with no delivery loop sat queued for 13 hours. `comms_run_interrupt` refused it as "not active",
since an interrupt is a control for the bridge running the turn and a queued run has none; the only way out was to
unsend the source message. A queued run has no turn, so the service cancels it itself.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase


class AQueuedRunCanBeCancelledByInterrupt(FastApiTestCase):
    DB_NAME = "aify-queued-interrupt.db"

    def _seed(self, run_id: str, status: str) -> None:
        async def write():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO agents (id, name, role, runtime, registered_at, last_seen) VALUES (?,?,?,?,?,?)",
                    ("target", "target", "coder", "hermes", "2026-10-13T00:00:00Z", "2026-10-13T00:00:00Z"))
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) VALUES (?,?,?,?,?)",
                    (run_id, "asker", "target", status, "2026-10-13T00:00:00Z"))
                await db.commit()

        asyncio.run(write())

    def _run(self, run_id: str) -> dict:
        async def read():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                run = dict(await (await db.execute("SELECT status, summary FROM dispatch_runs WHERE id = ?", (run_id,))).fetchone())
                run["events"] = [r[0] for r in await (await db.execute(
                    "SELECT event_type FROM dispatch_events WHERE run_id = ?", (run_id,))).fetchall()]
                run["controls"] = (await (await db.execute(
                    "SELECT COUNT(*) FROM dispatch_controls WHERE run_id = ?", (run_id,))).fetchone())[0]
                return run

        return asyncio.run(read())

    def _control(self, run_id: str, action: str):
        return self.client.post(f"/api/v1/dispatch/runs/{run_id}/control", json={"from_agent": "asker", "action": action})

    def test_an_interrupt_cancels_a_queued_run_and_queues_no_control(self):
        self._seed("run-q", "queued")
        answer = self._control("run-q", "interrupt")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["status"], "cancelled")
        run = self._run("run-q")
        self.assertEqual(run["status"], "cancelled")
        self.assertIn("asker", run["summary"])
        self.assertIn("cancelled", run["events"])
        self.assertEqual(run["controls"], 0, "a control was queued for a bridge that is not running the run")

    def test_an_unnamed_cancel_is_not_attributed_to_the_operator(self):
        # comms_run_interrupt sends from_agent="" when its optional `from` is omitted, an agent's ordinary call.
        self._seed("run-u", "queued")
        answer = self.client.post("/api/v1/dispatch/runs/run-u/control", json={"action": "interrupt"})
        self.assertEqual(answer.json()["status"], "cancelled", answer.text)
        run = self._run("run-u")
        self.assertEqual(run["summary"], "Cancelled before delivery.")
        self.assertNotIn("operator", run["summary"])

    def test_a_running_run_still_gets_a_control_for_its_bridge(self):
        # CONTROL: the existing interrupt path is unchanged.
        self._seed("run-r", "running")
        answer = self._control("run-r", "interrupt")
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertEqual(answer.json()["status"], "pending")
        self.assertEqual(self._run("run-r")["status"], "running")

    def test_a_steer_on_a_queued_run_is_still_refused(self):
        self._seed("run-s", "queued")
        self.assertEqual(self._control("run-s", "steer").status_code, 409)
        self.assertEqual(self._run("run-s")["status"], "queued")
