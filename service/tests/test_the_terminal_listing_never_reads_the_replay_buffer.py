r"""`GET /terminals` never READS `terminal_sessions.output`, not merely drops it from the response.

The route's own docstring says the replay buffer is what makes a 200-row listing "tens of megabytes",
and it popped `output` from every row before answering -- after `SELECT *` had already read every
buffer out of SQLite on the single-worker service. The dashboard's agent drawer asked for
`/terminals?status=all` on every refresh while a drawer was open (v0.7 scan, C9), so that cost was
paid on each data change.

HOW A TEST CAN SEE A READ. The response looks identical whether or not the column was read, so the
seeded buffer is TEXT that is not valid UTF-8: Python's sqlite3 raises "Could not decode to UTF-8"
the moment a SELECT fetches it, and never when a SELECT leaves it out. A listing that reads the
buffer therefore fails, and one that does not answers normally.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from service.tests._base import FastApiTestCase


class TheListingLeavesTheBufferInTheTable(FastApiTestCase):
    DB_NAME = "terminals-listing-no-buffer.db"

    def _seed_terminal(self, terminal_id: str, output_sql: str) -> None:
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "an-agent", "role": "coder", "runtime": "claude-code", "sessionMode": "resident",
        })
        self.assertEqual(registered.status_code, 200, registered.text)

        async def write():
            from service.db import get_db

            db = await get_db()
            try:
                await db.execute(
                    "INSERT OR IGNORE INTO environments (id, machine_id, status, last_seen, registered_at) "
                    "VALUES (?,?,?,?,?)",
                    ("windows:host:default", "test-machine", "online", "2026-09-25T00:00:00Z",
                     "2026-09-25T00:00:00Z"),
                )
                await db.execute(
                    "INSERT OR IGNORE INTO agent_sessions (id, agent_id, environment_id, runtime, "
                    "status, started_at, last_seen, spawn_spec_id, spawn_request_id) "
                    "VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                    ("sess-test", "an-agent", "windows:host:default", "claude-code", "running",
                     "2026-09-25T00:00:00Z", "2026-09-25T00:00:00Z"),
                )
                await db.execute(
                    f"""
                    INSERT INTO terminal_sessions
                        (id, session_id, agent_id, environment_id, bridge_id, runtime, workspace,
                         command, status, requested_by, created_at, updated_at, process_id, output)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, {output_sql})
                    """,
                    (terminal_id, "sess-test", "an-agent", "windows:host:default", "", "claude-code", "",
                     "", "attached", "test", "2026-09-25T00:00:00Z", "2026-09-25T01:00:00Z", "4242"),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(write())

    def test_a_buffer_that_cannot_be_read_does_not_break_the_listing(self) -> None:
        self._seed_terminal("t-undecodable", "CAST(X'FFFE41' AS TEXT)")
        response = self.client.get("/api/v1/terminals?status=all")
        self.assertEqual(response.status_code, 200, "the listing read the replay buffer column")
        terminal = response.json()["terminals"][0]
        self.assertEqual(terminal["id"], "t-undecodable")
        self.assertEqual(terminal["processId"], "4242", "the join key still arrives")
        self.assertNotIn("output", terminal)

    def test_control_the_detector_fires_when_the_column_is_read(self) -> None:
        """Without this the test above could pass because the seeded value decodes after all."""
        self._seed_terminal("t-control", "CAST(X'FFFE41' AS TEXT)")

        async def read_it():
            from service.db import get_db

            db = await get_db()
            try:
                await (await db.execute("SELECT output FROM terminal_sessions WHERE id = 't-control'")).fetchall()
            finally:
                await db.close()

        with self.assertRaises(Exception) as caught:
            asyncio.run(read_it())
        self.assertIn("decode", str(caught.exception).lower())


if __name__ == "__main__":
    unittest.main()
