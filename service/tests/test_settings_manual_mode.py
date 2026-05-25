"""Plan 6 C3 (2026-05-26) — `manual_session_mode` setting.

Default `false`: dashboard hides resident<->managed switch chips (today's
TTY auto-detect behavior continues).
When `true`: dashboard exposes the switch chips so the operator can flip
an agent's session_mode mid-life via the Plan 6 C1 PATCH endpoint.

This is a server-side setting, mirrored into the get/put settings
round-trip plumbing in api_v2.py.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import DEFAULT_SETTINGS, router


class _DummyWS:
    async def broadcast(self, *_args, **_kwargs):
        return None


class ManualSessionModeSettingTests(unittest.TestCase):
    def test_default_settings_includes_manual_session_mode_false(self):
        self.assertIn("manual_session_mode", DEFAULT_SETTINGS,
                      "Plan 6 C3: DEFAULT_SETTINGS must declare manual_session_mode")
        self.assertIs(DEFAULT_SETTINGS["manual_session_mode"], False,
                      "Plan 6 C3: manual_session_mode must default to False so today's "
                      "TTY auto-detect remains the default behavior")

    def test_get_settings_includes_manual_session_mode(self):
        tmpdir = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmpdir.name) / "aify-test.db"
            asyncio.run(init_db(db_path))
            app = FastAPI()
            app.state.ws_manager = _DummyWS()
            app.state.config = SimpleNamespace(data_dir=tmpdir.name)
            app.state.testing = True
            app.include_router(router, prefix="/api/v1")
            client = TestClient(app)
            try:
                res = client.get("/api/v1/settings")
                self.assertEqual(res.status_code, 200)
                body = res.json()
                self.assertIn("manual_session_mode", body,
                              "Plan 6 C3: GET /settings must surface manual_session_mode")
                self.assertEqual(body["manual_session_mode"], False)
            finally:
                client.close()
        finally:
            tmpdir.cleanup()

    def test_put_settings_round_trips_manual_session_mode(self):
        tmpdir = tempfile.TemporaryDirectory()
        try:
            db_path = Path(tmpdir.name) / "aify-test.db"
            asyncio.run(init_db(db_path))
            app = FastAPI()
            app.state.ws_manager = _DummyWS()
            app.state.config = SimpleNamespace(data_dir=tmpdir.name)
            app.state.testing = True
            app.include_router(router, prefix="/api/v1")
            client = TestClient(app)
            try:
                res = client.put("/api/v1/settings", json={"manual_session_mode": True})
                self.assertEqual(res.status_code, 200, res.text)
                body = res.json()
                self.assertEqual(body.get("manual_session_mode"), True,
                                 "Plan 6 C3: PUT /settings must round-trip manual_session_mode=True")
                # Re-read to confirm persistence.
                res2 = client.get("/api/v1/settings")
                self.assertEqual(res2.json().get("manual_session_mode"), True)
            finally:
                client.close()
        finally:
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
