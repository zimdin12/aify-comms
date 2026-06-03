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
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.db import init_db
from service.routers.api_v2 import router


# --- schema template cache (mirrors conftest.py for the unittest runner) ---
# ``python -m unittest`` does NOT load conftest.py's pytest fixtures, so under
# the unittest runner every test paid the full ``init_db`` schema build
# (~24ms × hundreds of tests). Build the schema ONCE per process into a
# template file and ``shutil.copy`` it per test. The copy is byte-for-byte the
# output of the real ``init_db`` (WAL checkpointed on the ``async with`` close),
# so there is zero behavior change and per-test isolation is preserved.
_SCHEMA_TEMPLATE: Path = None


def _schema_template() -> Path:
    global _SCHEMA_TEMPLATE
    if _SCHEMA_TEMPLATE is not None and _SCHEMA_TEMPLATE.exists():
        return _SCHEMA_TEMPLATE
    tmpdir = tempfile.mkdtemp(prefix="aify-base-schema-template-")
    template = Path(tmpdir) / "schema-template.db"
    # ``init_db`` may have been monkeypatched (e.g. by conftest's own fast
    # copy) — reach for the genuine implementation if one was stashed.
    import service.db as _db
    real = getattr(_db, "_real_init_db", None) or init_db
    asyncio.run(real(template))
    for sidecar in (template.with_name(template.name + "-wal"),
                    template.with_name(template.name + "-shm")):
        if sidecar.exists():  # pragma: no cover - defensive
            raise RuntimeError(
                f"schema template left a {sidecar.name} sidecar; template "
                "copy would be incomplete. init_db must checkpoint on close."
            )
    _SCHEMA_TEMPLATE = template
    return template


def _fast_init_db(db_path: Path, source: Path = None) -> None:
    """Materialize a fresh per-test DB from a cached template.

    ``source`` defaults to the bare schema template; a class may pass a
    pre-seeded template (e.g. with LEGACY_SETTINGS already applied) so every
    test starts from that exact state without re-running the seed per test.
    Keeps ``service.db._db_path`` in sync with ``init_db``'s global-path
    contract so request handlers resolve the right file.
    """
    import service.db as _db
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(source or _schema_template(), target)
    _db._db_path = target


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
        cls._base_template = None
        cls._base_tmpdir = None
        # If a suite opts into LEGACY_SETTINGS, apply that PUT ONCE into a
        # per-class template DB instead of paying the ~18ms HTTP round-trip in
        # every test's setUp. Each test then copies this pre-seeded template, so
        # it starts from the exact same settings state — behaviour-identical to
        # the per-test PUT, just hoisted out of the hot path.
        if cls.LEGACY_SETTINGS is not None:
            import service.db as _db
            cls._base_tmpdir = tempfile.TemporaryDirectory()
            seed = Path(cls._base_tmpdir.name) / "legacy-template.db"
            _fast_init_db(seed)
            cls._app.state.ws_manager = DummyWS()
            cls._app.state.config = SimpleNamespace(data_dir=cls._base_tmpdir.name)
            resp = cls._client.put("/api/v1/settings", json=cls.LEGACY_SETTINGS)
            assert resp.status_code == 200, resp.text
            # The PUT writes via a WAL connection; fold the -wal sidecar back into
            # the main file so the per-test copy is self-contained (otherwise the
            # settings could live only in a sidecar that never gets copied).
            import sqlite3
            conn = sqlite3.connect(str(seed))
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
            finally:
                conn.close()
            for sidecar in (seed.with_name(seed.name + "-wal"),
                            seed.with_name(seed.name + "-shm")):
                if sidecar.exists():
                    sidecar.unlink()
            # Sanity: the seeded settings must actually be in the copied file.
            check = sqlite3.connect(str(seed))
            try:
                rows = dict(check.execute("SELECT key, value FROM settings").fetchall())
            finally:
                check.close()
            for k in cls.LEGACY_SETTINGS:
                assert k in rows, (
                    f"LEGACY_SETTINGS key {k!r} missing from seeded template — "
                    "WAL checkpoint did not fold the PUT into the main db file."
                )
            cls._base_template = seed

    @classmethod
    def tearDownClass(cls):
        cls._client.close()
        if cls._base_tmpdir is not None:
            cls._base_tmpdir.cleanup()
            cls._base_tmpdir = None

    # ---- per test: fresh DB + fresh app.state, cheap ----
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / self.DB_NAME
        # When the class pre-seeded a LEGACY_SETTINGS template, copy that so the
        # per-test settings PUT is hoisted to setUpClass (run once, not per test).
        _fast_init_db(self._db_path, source=self._base_template)

        self.ws = DummyWS()
        # Re-point shared app.state at THIS test's collaborators. The router
        # reads these at request time, so per-test reset == per-test app.
        self._app.state.ws_manager = self.ws
        self._app.state.config = SimpleNamespace(data_dir=self._tmpdir.name)
        self.client = self._client

        if self.LEGACY_SETTINGS is not None and self._base_template is None:
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
