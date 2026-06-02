"""Shared base TestCase for the FastAPI/TestClient regression suites.

P0 of the 2026-06-02 test-consolidation plan. The historical pattern —
repeated verbatim across ~25 ``unittest.TestCase`` files — rebuilds, in
``setUp`` for EVERY test:

    1. ``asyncio.run(init_db(db_path))``           (handled by conftest copy)
    2. ``FastAPI()`` + ``include_router(...)``      (~30ms route compilation)
    3. ``TestClient(app)``
    4. a legacy ``PUT /api/v1/settings``            (pre-Plan-4 defaults)

``include_router`` route compilation is the single largest fixed cost
(~30ms/test). This base hoists the app + router + TestClient build to
``setUpClass`` so it runs ONCE per TestCase class, while keeping a fresh DB
file and fresh per-test app.state (ws manager + data_dir) so isolation is
preserved exactly. The router reads ws_manager / config off
``request.app.state`` at request time, so re-pointing them per test on the
shared app is equivalent to building a new app per test.

Subclasses opt into the legacy settings PUT via ``LEGACY_SETTINGS`` so only
suites that need the pre-Plan-4 contract pay for it; the rest skip it.

This is a drop-in: a subclass keeps its existing test bodies unchanged and
uses ``self.client`` / ``self.ws`` / ``self._db_path`` exactly as before.
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


class DummyWS:
    """Records broadcasts / notifications; matches the per-file _DummyWS."""

    def __init__(self):
        self.broadcasts = []
        self.notifications = []

    async def broadcast(self, *args, **kwargs):
        self.broadcasts.append((args, kwargs))
        return None

    async def notify_agent(self, *args, **kwargs):
        self.notifications.append((args, kwargs))
        return None


class FastApiTestCase(unittest.TestCase):
    """Base for suites that build a FastAPI app + TestClient + init_db.

    Subclass knobs:
      DB_NAME        — filename for the per-test sqlite db (default generic).
      LEGACY_SETTINGS — dict PUT to /api/v1/settings in setUp, or None to
                        skip. Set to the pre-Plan-4 default dict for suites
                        whose contracts predate the channel-route / wrapper
                        defaults.
    """

    DB_NAME = "aify-test.db"
    LEGACY_SETTINGS = None

    # ---- one-time per class: the expensive route compilation ----
    @classmethod
    def setUpClass(cls):
        cls._app = FastAPI()
        cls._app.state.testing = True
        cls._app.include_router(router, prefix="/api/v1")
        cls._client = TestClient(cls._app)

    @classmethod
    def tearDownClass(cls):
        cls._client.close()

    # ---- per test: fresh DB + fresh app.state, cheap ----
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / self.DB_NAME
        asyncio.run(init_db(self._db_path))

        self.ws = DummyWS()
        # Re-point shared app.state at THIS test's collaborators. The router
        # reads these at request time, so per-test reset == per-test app.
        self._app.state.ws_manager = self.ws
        self._app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        self.client = self._client

        if self.LEGACY_SETTINGS is not None:
            resp = self.client.put("/api/v1/settings", json=self.LEGACY_SETTINGS)
            assert resp.status_code == 200, resp.text

    def tearDown(self):
        self._tmpdir.cleanup()


# The exact pre-Plan-4 default bundle the legacy regression suites opt into.
PRE_PLAN4_SETTINGS = {
    "insert_messages_via_console": True,
    "managed_via_wrapper": False,
    "managed_pty_eager_spawn": False,
}
