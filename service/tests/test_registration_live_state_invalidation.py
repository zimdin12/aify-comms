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
from service.reconcilers.status_cache import (
    _live_state_fresh,
    _live_state_get,
)


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
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE

        self._register()
        _LIVE_STATE_CACHE["cache-register-agent"] = {
            "status": "stale", "reason": "future-cache", "environment_id": "",
            "session_id": "", "terminal_id": "", "active_run_id": "",
            "refresh_after": "2099-01-01T00:00:00Z",
            "updated_at": "2026-05-26T00:00:00Z",
        }

        self._register()

        self.assertIsNone(
            _live_state_fresh("cache-register-agent"),
            "registration must invalidate cached live state (expire it: the next read recomputes)",
        )


if __name__ == "__main__":
    unittest.main()
