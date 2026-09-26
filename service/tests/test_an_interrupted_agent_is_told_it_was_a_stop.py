"""An interrupted claude agent is told, in one short unread note, that it was stopped, not refused.

Claude Code tells the model "The user doesn't want to proceed with this tool use" when a turn is
interrupted, the same words it uses for a declined permission prompt. An agent interrupted live
concluded a permission gate existed and built theories about it (KNOWN_ISSUES, 2026-08-25). The wording
is Claude Code's; the note is what this service can add. It wakes nothing: the operator stopped the
agent on purpose, so it waits for the agent's next turn (v0.7.4).
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase


class AnInterruptedAgentIsToldItWasAStopTests(FastApiTestCase):
    DB_NAME = "aify-interrupt-notice.db"

    def _seed(self, agent_id: str, runtime: str, run_id: str) -> None:
        async def write():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "INSERT INTO agents (id, name, role, runtime, registered_at, last_seen) VALUES (?,?,?,?,?,?)",
                    (agent_id, agent_id, "coder", runtime, "2026-09-26T00:00:00Z", "2026-09-26T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at) VALUES (?,?,?,?,?)",
                    (run_id, "dashboard", agent_id, "running", "2026-09-26T00:00:00Z"),
                )
                await db.commit()

        asyncio.run(write())

    def _notes(self, agent_id: str):
        async def read():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT from_agent, type, body, dispatch_requested FROM messages WHERE to_agent = ?", (agent_id,),
                )
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(read())

    def _interrupt(self, run_id: str, status: str) -> str:
        made = self.client.post(f"/api/v1/dispatch/runs/{run_id}/control", json={"from_agent": "dashboard", "action": "interrupt"})
        self.assertEqual(made.status_code, 200, made.text)
        control_id = made.json()["controlId"]
        self._settle(control_id, status)
        return control_id

    def _settle(self, control_id: str, status: str) -> None:
        settled = self.client.patch(
            f"/api/v1/dispatch/controls/{control_id}",
            json={"status": status, "handledBy": "bridge-x", "machineId": "m"},
        )
        self.assertEqual(settled.status_code, 200, settled.text)

    def test_settling_the_same_control_again_adds_no_second_note(self):
        """A bridge that retries its PATCH, or a replayed request, is one stop (review of 28eb72a0)."""
        self._seed("claude-d", "claude-code", "run-d")
        control_id = self._interrupt("run-d", "completed")
        self._settle(control_id, "completed")
        self.assertEqual(len(self._notes("claude-d")), 1)

    def test_CONTROL_a_second_interrupt_is_a_second_stop(self):
        """The dedupe is per control, not per agent: two stops are two notes."""
        self._seed("claude-e", "claude-code", "run-e")
        self._interrupt("run-e", "completed")
        self._interrupt("run-e", "completed")
        self.assertEqual(len(self._notes("claude-e")), 2)

    def test_a_completed_interrupt_leaves_one_short_unread_note(self):
        self._seed("claude-a", "claude-code", "run-a")
        self._interrupt("run-a", "completed")
        notes = self._notes("claude-a")
        self.assertEqual(len(notes), 1, notes)
        note = notes[0]
        self.assertEqual(note["from_agent"], "aify-comms")
        self.assertEqual(note["type"], "info")
        self.assertEqual(note["dispatch_requested"], 0, "the note must not wake an agent the operator stopped")
        self.assertIn("stopped by dashboard", note["body"])
        self.assertIn("not a permission refusal", note["body"])
        self.assertLess(len(note["body"]), 300, "the note is meant to be short")

    def test_CONTROL_a_failed_interrupt_says_nothing(self):
        self._seed("claude-b", "claude-code", "run-b")
        self._interrupt("run-b", "failed")
        self.assertEqual(self._notes("claude-b"), [])

    def test_CONTROL_another_runtime_is_not_told_about_claudes_wording(self):
        self._seed("codex-c", "codex", "run-c")
        self._interrupt("run-c", "completed")
        self.assertEqual(self._notes("codex-c"), [])
