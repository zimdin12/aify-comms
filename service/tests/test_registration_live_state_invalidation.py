"""Registration must invalidate cached agent live state.

Agent registration changes runtime/session/bridge metadata that live-state
resolution depends on. A future-dated cache row must not survive a fresh
register, or the dashboard can keep showing stale status after the wrapper
reconnects.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


from service.tests._base import FastApiTestCase


class RegistrationLiveStateInvalidationTests(FastApiTestCase):
    DB_NAME = "aify-test-register-cache.db"

    def _register(self):
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": "cache-register-agent",
                "role": "tester",
                "runtime": "hermes",
                "sessionMode": "resident",
                "sessionHandle": "session-handle",
                "bridgeId": "bridge-current",
                "machineId": "machine-current",
                "runtimeConfig": {"gatewayUrl": "ws://127.0.0.1:9999/api/ws?token=t"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_register_invalidates_future_cached_live_state(self):
        self._register()
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                INSERT INTO agent_live_state
                    (agent_id, status, reason, updated_at, refresh_after)
                VALUES (?, 'stale', 'future-cache', '2026-05-26T00:00:00Z',
                        '2099-01-01T00:00:00Z')
                """,
                ("cache-register-agent",),
            )
            conn.commit()
        finally:
            conn.close()

        self._register()

        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT status FROM agent_live_state WHERE agent_id = ?",
                ("cache-register-agent",),
            ).fetchone()
            self.assertIsNone(row, "registration must invalidate cached live state")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
