"""Shared pytest fixtures for the service test suite.

Speed lever (P0 of the 2026-06-02 test-consolidation plan): ~25
``unittest.TestCase`` files run, in ``setUp`` for *every* test, the full
``asyncio.run(init_db(db_path))`` — 23 ``CREATE TABLE`` statements plus the
migration/backfill pass in :func:`service.db.init_db`. With ~600 tests that
fixed cost dominates wall-clock.

This conftest builds the schema exactly ONCE per pytest session into a
template SQLite file, then transparently replaces every ``init_db(path)``
call with a cheap ``shutil.copy`` of that template to ``path``. Each test
still gets its own fresh DB file (full per-test isolation) at a fraction of
the cost — there is no behavior change, the resulting DB is byte-for-byte the
output of the real ``init_db``.

The replacement is wired as an autouse session fixture so existing test
files inherit the speedup with ZERO edits to their bodies.
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

import service.db as _db


def _build_template() -> Path:
    """Run the real init_db once into a throwaway template file."""
    tmpdir = tempfile.mkdtemp(prefix="aify-schema-template-")
    template = Path(tmpdir) / "schema-template.db"
    # Use the genuine init_db so the template is exactly what production
    # creates (schema + every migration + backfill), checkpointed on close.
    asyncio.run(_db._real_init_db(template))
    # Sanity: init_db must leave a self-contained file (WAL checkpointed on
    # close). If a sidecar ever appears, fold it in so copies stay complete.
    for sidecar in (template.with_name(template.name + "-wal"),
                    template.with_name(template.name + "-shm")):
        if sidecar.exists():  # pragma: no cover - defensive
            raise RuntimeError(
                f"schema template left a {sidecar.name} sidecar; template "
                "copy would be incomplete. init_db must checkpoint on close."
            )
    return template


@pytest.fixture(scope="session", autouse=True)
def _fast_init_db():
    """Replace init_db with a template-copy for the whole session.

    Patches both ``service.db.init_db`` and the ``init_db`` name re-exported
    into every already-imported test module (the tests do
    ``from service.db import init_db``, binding their own reference), so the
    swap is total regardless of how a module imported it.
    """
    # Preserve the genuine implementation under a private name so the template
    # builder (and anyone who truly needs a from-scratch init) can reach it.
    _db._real_init_db = _db.init_db
    template = _build_template()

    async def _fast(db_path: Path = None):
        # Mirror init_db's global-path contract exactly: a passed db_path
        # becomes the active path; otherwise reuse the module global.
        if db_path is not None:
            _db._db_path = Path(db_path)
        target = Path(_db._db_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(template, target)

    patched_modules = []
    _db.init_db = _fast
    for mod in list(sys.modules.values()):
        if mod is None or mod is _db:
            continue
        name = getattr(mod, "__name__", "")
        if not name.startswith("service") and not name.startswith("test"):
            # Test modules live at top level (test_*) and the package under
            # service.*; skip everything else to avoid touching unrelated libs.
            if "tests" not in name:
                continue
        if getattr(mod, "init_db", None) is _db._real_init_db:
            mod.init_db = _fast
            patched_modules.append(mod)

    try:
        yield
    finally:
        _db.init_db = _db._real_init_db
        for mod in patched_modules:
            mod.init_db = _db._real_init_db


@pytest.fixture(autouse=True)
def _rebind_init_db_for_late_imports(_fast_init_db):
    """Catch test modules imported after the session fixture ran.

    pytest imports test modules lazily; a module collected after the
    session-scoped patch executed would still hold the genuine ``init_db``.
    This per-test autouse fixture re-points any such late-bound reference at
    the fast copy before the test's ``setUp`` runs.
    """
    fast = _db.init_db
    real = getattr(_db, "_real_init_db", None)
    for mod in list(sys.modules.values()):
        if mod is None or mod is _db:
            continue
        if real is not None and getattr(mod, "init_db", None) is real:
            mod.init_db = fast
    yield
