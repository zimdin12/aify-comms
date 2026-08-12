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

    def test_lease_ttl_spans_the_keepalive_cadence(self):
        # The managed-claude PTY repaint keepalive (terminal-runtime._armConsoleKeepalive)
        # SIGWINCHes the PTY every ~4s so claude re-emits its footer when the Console is closed.
        # The lease TTL MUST comfortably exceed that cadence — otherwise a missed poke or a
        # coalesced-output gap drops `working` while claude is still working (the flap this
        # whole mechanism exists to kill). 4x headroom guards against re-shrinking it.
        from service.control_plane import CONSOLE_WORKING_LEASE_SECONDS

        keepalive_seconds = 4
        self.assertGreaterEqual(
            CONSOLE_WORKING_LEASE_SECONDS,
            keepalive_seconds * 4,
            "lease TTL must span several keepalive ticks so an unwatched working claude "
            "never flaps to online between pokes",
        )

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
