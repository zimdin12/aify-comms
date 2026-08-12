"""PATCH /api/v1/agents/{id}/ready sets agent_turn_state.ready=True.

`ready` is now internal bridge/controller readiness state. Public agent status
uses `online` for a live idle worker and `available` for a spawnable idle
identity, so operators do not see both `ready` and `available`.

The router is mounted at /api/v1 in production (see service/main.py); the
"v2" in the file name is historical. The plan's "/api/v2" shorthand maps
to this prefix.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


from service.tests._base import FastApiTestCase


class ReadyStatusEndpointTests(FastApiTestCase):
    DB_NAME = "aify-test-ready.db"

    def _register(self, agent_id: str, *, role: str = "tester", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        response = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_patch_ready_endpoint_returns_200_for_known_agent(self):
        self._register("ready-known", runtime="codex", sessionMode="managed")
        resp = self.client.patch(
            "/api/v1/agents/ready-known/ready",
            json={"ready": True, "requestedBy": "test"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body.get("ok"), True)
        self.assertEqual(body.get("agentId"), "ready-known")
        self.assertEqual(body.get("ready"), True)

    def test_patch_ready_404_for_unknown_agent(self):
        resp = self.client.patch(
            "/api/v1/agents/nonexistent-agent/ready",
            json={"ready": True},
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_ready_false_clears_ready_state(self):
        self._register("ready-clear", runtime="codex", sessionMode="managed")
        # First set ready=True.
        first = self.client.patch(
            "/api/v1/agents/ready-clear/ready",
            json={"ready": True},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json().get("ready"), True)
        # Then set ready=False.
        second = self.client.patch(
            "/api/v1/agents/ready-clear/ready",
            json={"ready": False},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json().get("ready"), False)

    def test_patch_ready_persists_to_agent_turn_state(self):
        """End-to-end: PATCH writes a row with ready=1 that survives reads."""
        import sqlite3
        self._register("ready-persist", runtime="codex", sessionMode="managed")
        resp = self.client.patch(
            "/api/v1/agents/ready-persist/ready",
            json={"ready": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # Inspect the SQLite row directly.
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT ready FROM agent_turn_state WHERE agent_id = ?",
                ("ready-persist",),
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row, "agent_turn_state row missing after PATCH")
            self.assertEqual(int(row[0] or 0), 1)
        finally:
            conn.close()

    def test_patch_ready_preserves_existing_turn_busy(self):
        """Setting ready must not clobber turn_busy on the same row."""
        import sqlite3
        self._register("ready-preserve", runtime="codex", sessionMode="managed")
        # Seed turn_busy=1 directly so the ready PATCH must merge, not replace.
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO agent_turn_state
                    (agent_id, turn_busy, turn_run_id, turn_bridge_id,
                     turn_runtime, turn_updated_at)
                VALUES (?, 1, '', '', 'codex', '2026-05-25T00:00:00Z')
                """,
                ("ready-preserve",),
            )
            conn.commit()
        finally:
            conn.close()
        resp = self.client.patch(
            "/api/v1/agents/ready-preserve/ready",
            json={"ready": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT turn_busy, ready FROM agent_turn_state WHERE agent_id = ?",
                ("ready-preserve",),
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(int(row[0] or 0), 1, "turn_busy clobbered by ready upsert")
            self.assertEqual(int(row[1] or 0), 1)
        finally:
            conn.close()

    def test_patch_ready_invalidates_cached_live_state(self):
        """Ready changes must invalidate the cached live state; otherwise the
        dashboard can keep showing a future cached ready/online status after
        the bridge has explicitly changed readiness."""
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE
        from service.control_plane import _live_state_fresh, _live_state_get
        self._register("ready-cache", runtime="codex", sessionMode="managed")
        _LIVE_STATE_CACHE["ready-cache"] = {
            "status": "ready", "reason": "future-cache", "environment_id": "",
            "session_id": "", "terminal_id": "", "active_run_id": "",
            "refresh_after": "2099-01-01T00:00:00Z",
            "updated_at": "2026-05-26T00:00:00Z",
        }

        resp = self.client.patch(
            "/api/v1/agents/ready-cache/ready",
            json={"ready": False},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

        self.assertIsNone(
            _live_state_fresh("ready-cache"),
            "ready PATCH must invalidate cached live state (expire it: the next read recomputes)",
        )


if __name__ == "__main__":
    unittest.main()
