"""An existing database loses what 0.9.9 S3c retired, keeps everything else, and carries its publications across.

Every other test boots a fresh database, which never had the old shape. The operator's has: the legacy turn tables,
the host's screen observation on each terminal, and aify-env's saved publications under their `shadow` names with
`row_applied_at`. The real `init_db` runs over a database carrying all of it, with a row in each, and must drop the
retired parts while the terminal row and the saved publication survive under the new names.
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
            for new, old in (("agent_state_publishers", "agent_state_shadow_publishers"),
                             ("agent_state_rows", "agent_state_shadow_rows")):
                db.execute(f"ALTER TABLE {new} RENAME TO {old}")
            db.execute("ALTER TABLE agent_state_shadow_rows ADD COLUMN row_applied_at TEXT NOT NULL DEFAULT ''")
            db.execute(
                "INSERT INTO agent_state_shadow_publishers (machine_id, instance, generation, incarnation_id, publication,"
                " digest, kind, inputs) VALUES ('m', 'i', 1, 'inc', 1, 'd', 'snapshot', '{}')")
            db.execute("INSERT INTO agent_state_shadow_rows (machine_id, instance, agent_id, lifetime, record_json,"
                       " row_applied_at) VALUES ('m', 'i', 'a', 'L', '{}', 'then')")
            db.commit()

    def test_CONTROL_the_database_carries_what_is_retired(self):
        with closing(sqlite3.connect(self.path)) as db:
            self.assertLessEqual(set(LEGACY_TABLES), tables(db))
            self.assertLessEqual(set(ACTIVITY_COLUMNS), columns(db, "terminal_sessions"))
            self.assertLessEqual({"agent_state_shadow_publishers", "agent_state_shadow_rows"}, tables(db))
            self.assertEqual({"agent_state_publishers", "agent_state_rows"} & tables(db), set())

    def test_init_drops_the_retired_tables_and_columns_and_keeps_the_terminal(self):
        asyncio.run(db_module.init_db(self.path))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(set(LEGACY_TABLES) & tables(db), set())
            self.assertEqual(set(ACTIVITY_COLUMNS) & columns(db, "terminal_sessions"), set())
            self.assertEqual(db.execute("SELECT id, agent_id FROM terminal_sessions").fetchall(), [("t1", "a")])
        asyncio.run(db_module.init_db(self.path))  # every start runs it again

    def test_init_carries_the_saved_publications_to_the_new_names(self):
        asyncio.run(db_module.init_db(self.path))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual({"agent_state_shadow_publishers", "agent_state_shadow_rows"} & tables(db), set())
            self.assertEqual(db.execute("SELECT machine_id, instance, generation FROM agent_state_publishers").fetchall(),
                             [("m", "i", 1)])
            self.assertEqual(db.execute("SELECT machine_id, instance, agent_id, lifetime FROM agent_state_rows").fetchall(),
                             [("m", "i", "a", "L")])
            self.assertNotIn("row_applied_at", columns(db, "agent_state_rows"))
            self.assertEqual({row[2] for row in db.execute("PRAGMA foreign_key_list(agent_state_rows)")},
                             {"agent_state_publishers"})
        asyncio.run(db_module.init_db(self.path))  # every start runs it again


if __name__ == "__main__":
    unittest.main()
