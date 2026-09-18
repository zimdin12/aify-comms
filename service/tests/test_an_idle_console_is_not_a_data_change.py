"""Output from a console whose status is not moving tells the dashboard only that something is alive.

MEASURED 2026-09-18: one open dashboard tab refetched /agents, /sessions and /environments 84 times
each in 25 s. Every output flush re-wrote `terminal_sessions.status` and the session's mirrored
`terminal_status` with the value they already held, and a SET naming either column is a real change
to the change feed however equal the value -- so every chunk from an idle console read as an edit.
The CONTROL keeps the other half true: a status that does move is still published as a change.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

BRIDGE = "bridge-idle-console-probe"
ENVIRONMENT = "linux:test-host:default"
TERMINAL = "term_idle_console_probe"
AGENT = "idle-console-agent"
WATCHED = {"terminal_sessions", "agent_sessions"}


class AnIdleConsoleIsNotADataChange(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.assertEqual(self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT, "machineId": "linux:test-host", "os": "linux", "kind": "linux",
            "bridgeId": BRIDGE, "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        }).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/agents", json={
            "agentId": AGENT, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:test-host", "bridgeId": BRIDGE,
        }).status_code, 200)
        from service.db import get_db

        async def seed():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, started_at,"
                    " last_seen, terminal_status, spawn_spec_id, spawn_request_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (f"sess-{AGENT}", AGENT, ENVIRONMENT, "claude-code", "running",
                     "2026-09-18T02:00:00Z", "2026-09-18T02:00:00Z", "attached", None, None))
                await db.execute(
                    "INSERT INTO terminal_sessions (id, agent_id, session_id, environment_id, runtime, bridge_id,"
                    " command, status, output, error, cols, rows, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (TERMINAL, AGENT, f"sess-{AGENT}", ENVIRONMENT, "claude-code", BRIDGE,
                     "claude-aify --aify-agent x", "attached", "", "", 120, 30,
                     "2026-09-18T02:00:00Z", "2026-09-18T02:00:00Z"))
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())
        from service.db import CONNECTION_POOL

        # Commits are reported only by a pooled checkout, and the test app never starts the pool.
        self.pool = CONNECTION_POOL
        self.reported: list = []
        self._original = self.pool._on_commit
        self.pool._on_commit = self.reported.extend
        self.pool.enable()

    def tearDown(self) -> None:
        asyncio.run(self.pool.aclose())
        self.pool._on_commit = self._original
        super().tearDown()

    def _flush_chunk(self, output: str, status: str) -> set[str]:
        from service import terminal_write_queue

        self.reported.clear()
        response = self.client.post(f"/api/v1/terminals/{TERMINAL}/output",
                                    json={"bridgeId": BRIDGE, "output": output, "status": status})
        self.assertEqual(response.status_code, 200, response.text)
        asyncio.run(terminal_write_queue.flush_terminal_output_writes_for_tests())
        self.assertTrue(self.reported, "no commit was reported, so this test would judge nothing")
        return {write.table for write in self.reported if not write.liveness}

    def _stored(self) -> tuple:
        from service.db import get_db

        async def read():
            db = await get_db()
            try:
                terminal = await (await db.execute(
                    "SELECT status FROM terminal_sessions WHERE id = ?", (TERMINAL,))).fetchone()
                session = await (await db.execute(
                    "SELECT terminal_status FROM agent_sessions WHERE id = ?", (f"sess-{AGENT}",))).fetchone()
                return terminal[0], session[0]
            finally:
                await db.close()

        return asyncio.run(read())

    def test_output_at_an_unchanged_status_is_only_liveness(self) -> None:
        changed = self._flush_chunk("thinking...\r\n", "attached")
        self.assertFalse(changed & WATCHED,
                         f"an idle console's output was published as a change to {sorted(changed & WATCHED)}")

    def test_CONTROL_a_status_that_moves_is_still_a_change(self) -> None:
        changed = self._flush_chunk("working\r\n", "running")
        self.assertEqual(self._stored(), ("running", "running"))
        self.assertTrue(WATCHED <= changed,
                        f"a moved status was not published: only {sorted(changed)}")


if __name__ == "__main__":
    unittest.main()
