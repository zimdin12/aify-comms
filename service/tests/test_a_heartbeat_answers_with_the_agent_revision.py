"""A heartbeat answers with the agent's record revision: steady while nothing changes, moved by a change.

Resident bridges fetched `GET /agents/{id}` on every dispatch tick (every 3 s on a managed hermes
bridge) to notice a Stop and refresh their copy of the record. They now fetch only when this revision
moves (mcp/stdio/dispatch-loop.mjs), so the two properties that matter are pinned here, through the
real route: a beat alone must NOT move it -- or the fetch returns every tick -- and a real change,
such as a Stop, MUST -- or a stopped resident is never told.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

AGENT = "revision-probe"


class AHeartbeatAnswersWithTheAgentRevision(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "codex", "sessionMode": "resident",
        }).status_code, 200)

    def _beat(self) -> str:
        response = self.client.post(f"/api/v1/agents/{AGENT}/heartbeat", json={})
        self.assertEqual(response.status_code, 200, response.text)
        revision = response.json().get("agentRevision")
        self.assertTrue(revision, "the heartbeat answered with no revision")
        return revision

    def _set(self, column: str, value: str) -> None:
        from service.db import get_db

        async def write():
            db = await get_db()
            try:
                await db.execute(f"UPDATE agents SET {column} = ? WHERE id = ?", (value, AGENT))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(write())

    def test_beats_alone_leave_the_revision_where_it_was(self) -> None:
        # Two beats a second apart, as they are live -- the clock has second resolution, so two beats
        # inside one test would otherwise write the same `last_seen` and prove nothing.
        with mock.patch("service.routers.agents.liveness._now", return_value="2026-09-18T10:00:00Z"):
            first = self._beat()
        with mock.patch("service.routers.agents.liveness._now", return_value="2026-09-18T10:00:03Z"):
            self.assertEqual(self._beat(), first, "a beat moved the revision, so the bridge would fetch every tick")

    def test_CONTROL_a_stop_moves_the_revision(self) -> None:
        first = self._beat()
        self._set("status", "stopped")
        self.assertNotEqual(self._beat(), first, "a Stop left the revision unchanged, so the bridge is never told")

    def test_CONTROL_a_config_edit_moves_the_revision(self) -> None:
        first = self._beat()
        self._set("model", "a-different-model")
        self.assertNotEqual(self._beat(), first)
