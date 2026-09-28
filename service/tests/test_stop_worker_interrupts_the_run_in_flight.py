"""Stop worker interrupts the run the agent is working on, the way Stop session always did.

The agent drawer offered two stops: "Stop worker" (`POST /agents/{id}/stop-worker`, agent-wide: every
terminal, every live session, the turn state) and "Stop session" (`POST /sessions/{id}/control`, one
session, and it INTERRUPTS the in-flight run). Neither was the whole stop, and the operator asked why
there were two (2026-09-29). The drawer now offers one, Stop worker, so it must also record the
interrupt: otherwise the run the worker was carrying is left running with nobody behind it, and its
eventual failure cannot name who stopped it.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT_ID = "sw-worker"


class StopWorkerInterruptsTheRunInFlightTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        response = self.client.post(
            "/api/v1/agents", json={"agentId": AGENT_ID, "role": "coder", "runtime": "codex"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _controls(self, run_id: str) -> list[dict]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT action, from_agent FROM dispatch_controls WHERE run_id = ?", (run_id,),
                )
                return [dict(row) for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_run(self, run_id: str, status: str) -> None:
        self._write(
            "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, message_type,"
            " subject, body, priority, status, require_reply, requested_at, claimed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, f"msg-{run_id}", "peer", AGENT_ID, "request", "s", "b", "normal",
             status, 1, "2026-09-29T00:00:00Z", "2026-09-29T00:00:00Z"),
        )

    def test_the_running_run_gets_an_interrupt_from_whoever_stopped_the_worker(self):
        self._seed_run("run-live", "running")
        response = self.client.post(f"/api/v1/agents/{AGENT_ID}/stop-worker", json={"requestedBy": "dashboard"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._controls("run-live"), [{"action": "interrupt", "from_agent": "dashboard"}])

    def test_a_finished_run_is_left_alone(self):
        # CONTROL: the interrupt is for the run in flight, not for every run the agent ever had.
        self._seed_run("run-done", "completed")
        response = self.client.post(f"/api/v1/agents/{AGENT_ID}/stop-worker", json={"requestedBy": "dashboard"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self._controls("run-done"), [])
