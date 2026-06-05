"""POST /api/v1/agents/{id}/console-working stamps a TTL working lease.

The managed-claude host bridge posts this while the claude TUI spinner footer is
visible; the lease is OR'd into derived `working` (a fresh turn_busy-equivalent) so
the agent reads `working` during a long thinking phase the per-completed-message
transcript can't see. Schema: service/db.py agent_console_signal. Derivation:
service/routers/api_v2.py _compute_live_status_cache (mirrors the turn_busy fresh
lease; surfaces as `working` at the turn_busy branch when a live worker is present).
"""

import sqlite3

from service.tests._base import FastApiTestCase


class ConsoleWorkingLeaseTests(FastApiTestCase):
    DB_NAME = "aify-test-console-working.db"

    def _register(self, agent_id: str, **extra):
        payload = {"agentId": agent_id, "role": "coder"}
        payload.update(extra)
        resp = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_console_working_stamps_lease(self):
        self._register("cw-agent", runtime="claude-code", sessionMode="managed")
        resp = self.client.post("/api/v1/agents/cw-agent/console-working", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json().get("ok"), True)

        con = sqlite3.connect(str(self._db_path))
        try:
            row = con.execute(
                "SELECT working_at FROM agent_console_signal WHERE agent_id = ?",
                ("cw-agent",),
            ).fetchone()
        finally:
            con.close()
        self.assertIsNotNone(row, "console-working lease row was not stamped")
        self.assertTrue(row[0], "working_at must be a non-empty timestamp")

    def test_console_working_is_idempotent(self):
        self._register("cw-agent-2", runtime="claude-code", sessionMode="managed")
        first = self.client.post("/api/v1/agents/cw-agent-2/console-working", json={})
        second = self.client.post("/api/v1/agents/cw-agent-2/console-working", json={})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        con = sqlite3.connect(str(self._db_path))
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM agent_console_signal WHERE agent_id = ?",
                ("cw-agent-2",),
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 1, "repeated posts must upsert a single lease row")
