"""An existing database loses the tables and columns 0.9.9 S3c retired, and keeps everything else.

Every other test boots a fresh database, which never had them. The operator's has: the legacy turn tables and the
host's screen observation on each terminal. The real `init_db` runs over a database that still carries them, with
a row in each, and must drop them while the terminal row they sat beside survives.
"""

import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from service import db as db_module

LEGACY_TABLES = ("agent_turn_state", "agent_status_state", "agent_hook_order", "agent_console_signal")
ACTIVITY_COLUMNS = ("activity_state", "activity_rule", "activity_observed_at", "activity_reported_at")


def tables(db) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def columns(db, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


class AnExistingDatabaseGivesUpTheRetiredTables(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "existing.db"
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(setattr, db_module, "_db_path", db_module._db_path)
        asyncio.run(db_module.init_db(self.path))
        with closing(sqlite3.connect(self.path)) as db:
            for table in LEGACY_TABLES:
                db.execute(f"CREATE TABLE {table} (agent_id TEXT PRIMARY KEY)")
                db.execute(f"INSERT INTO {table} (agent_id) VALUES ('a')")
            for column in ACTIVITY_COLUMNS:
                db.execute(f"ALTER TABLE terminal_sessions ADD COLUMN {column} TEXT DEFAULT ''")
            db.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, created_at, updated_at,"
                " activity_state) VALUES ('t1', 's1', 'a', 'e1', 'claude-code', 'now', 'now', 'working')")
            db.commit()

    def test_CONTROL_the_database_carries_what_is_retired(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertLessEqual(set(LEGACY_TABLES), tables(db))
            self.assertLessEqual(set(ACTIVITY_COLUMNS), columns(db, "terminal_sessions"))

    def test_init_drops_the_retired_tables_and_columns_and_keeps_the_terminal(self):
        asyncio.run(db_module.init_db(self.path))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(set(LEGACY_TABLES) & tables(db), set())
            self.assertEqual(set(ACTIVITY_COLUMNS) & columns(db, "terminal_sessions"), set())
            self.assertEqual(db.execute("SELECT id, agent_id FROM terminal_sessions").fetchall(), [("t1", "a")])
        asyncio.run(db_module.init_db(self.path))  # every start runs it again


if __name__ == "__main__":
    unittest.main()
