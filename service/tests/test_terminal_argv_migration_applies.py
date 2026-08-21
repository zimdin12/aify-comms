"""The `argv` column migration, run against a database that predates it.

This is the one step of the deploy that touches the operator's live data. `service/db.py` reads
`PRAGMA table_info` and adds only columns that are missing, which is the right shape — and nothing
exercised it. The read path was covered (a row without the column yields an empty list); the write that
CREATES the column was not.

What a failure would look like: the container starts, the migration raises, and the service is down on
a database it half-touched. What silent damage would look like: the column arrives but existing rows
lose data. Both are cheap to rule out and expensive to discover during a deploy.
"""

import asyncio
import sqlite3
import tempfile
from pathlib import Path

from service.db import TERMINAL_SESSION_MIGRATIONS, _migrate_terminal_sessions_table

import aiosqlite

# terminal_sessions as it stood BEFORE any of the migrations in that table's dict — the shape an
# operator's database is in right now.
PRE_MIGRATION_DDL = """
CREATE TABLE terminal_sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    command TEXT,
    status TEXT,
    created_at TEXT
)
"""

EXISTING_ROWS = [
    ("t1", "agent-a", "claude --aify-agent agent-a", "running", "2026-08-01T00:00:00Z"),
    ("t2", "agent-b", "codex resume abc123", "exited", "2026-08-02T00:00:00Z"),
]


def _make_pre_migration_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(PRE_MIGRATION_DDL)
    conn.executemany("INSERT INTO terminal_sessions VALUES (?,?,?,?,?)", EXISTING_ROWS)
    conn.commit()
    conn.close()


def _columns(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(terminal_sessions)")}
    finally:
        conn.close()


def _rows(path: Path) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return list(conn.execute("SELECT id, agent_id, command, status FROM terminal_sessions ORDER BY id"))
    finally:
        conn.close()


async def _migrate(path: Path) -> None:
    async with aiosqlite.connect(path) as db:
        await _migrate_terminal_sessions_table(db)
        await db.commit()


def test_the_fixture_really_predates_the_migration():
    """Anti-vacuity: if the starting database already had the columns, every assertion below would be
    about a migration that had nothing to do."""
    with tempfile.TemporaryDirectory(prefix="aify-mig-") as tmp:
        path = Path(tmp) / "pre.db"
        _make_pre_migration_db(path)
        before = _columns(path)
        assert "argv" not in before
        assert TERMINAL_SESSION_MIGRATIONS, "no migrations declared; this gate would be vacuous"
        missing = set(TERMINAL_SESSION_MIGRATIONS) - before
        assert len(missing) == len(TERMINAL_SESSION_MIGRATIONS), (
            f"the fixture already carries {before & set(TERMINAL_SESSION_MIGRATIONS)}"
        )


def test_every_declared_column_is_added_and_no_row_is_lost():
    with tempfile.TemporaryDirectory(prefix="aify-mig2-") as tmp:
        path = Path(tmp) / "pre.db"
        _make_pre_migration_db(path)
        before_rows = _rows(path)

        asyncio.run(_migrate(path))

        after = _columns(path)
        for column in TERMINAL_SESSION_MIGRATIONS:
            assert column in after, f"{column} was declared but not added"
        assert _rows(path) == before_rows, "existing rows must survive the migration unchanged"


def test_argv_defaults_to_empty_rather_than_null_on_existing_rows():
    """`_decoded_argv` fails closed on anything that is not a list, so NULL would still read as [] —
    but the column is declared DEFAULT '' and a row that disagrees with its own DDL is a trap for the
    next reader."""
    with tempfile.TemporaryDirectory(prefix="aify-mig3-") as tmp:
        path = Path(tmp) / "pre.db"
        _make_pre_migration_db(path)
        asyncio.run(_migrate(path))
        conn = sqlite3.connect(path)
        try:
            values = [row[0] for row in conn.execute("SELECT argv FROM terminal_sessions")]
        finally:
            conn.close()
        assert values == ["", ""], f"pre-existing rows should carry the declared default: {values}"


def test_running_it_twice_changes_nothing():
    """Containers restart. A migration that is not idempotent fails the SECOND boot, which is the one
    nobody is watching."""
    with tempfile.TemporaryDirectory(prefix="aify-mig4-") as tmp:
        path = Path(tmp) / "pre.db"
        _make_pre_migration_db(path)
        asyncio.run(_migrate(path))
        once = (_columns(path), _rows(path))
        asyncio.run(_migrate(path))
        assert (_columns(path), _rows(path)) == once
