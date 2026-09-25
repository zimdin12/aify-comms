"""An existing database loses the retired `agent_live_state` table at startup; a new one never has it.

The table held derived agent status until 2026-06-18, when the status moved to the in-memory
`_LIVE_STATE_CACHE`. It stayed in the schema, read and written by nothing, while its ON DELETE
CASCADE made every agent delete touch it. `init_db` now drops it, and the schema no longer creates it.
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import service.db as db_module


def _tables(path: Path) -> set[str]:
    con = sqlite3.connect(str(path))
    try:
        return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    finally:
        con.close()


class TheRetiredLiveStateTableIsDropped(unittest.TestCase):
    def setUp(self):
        self._saved_path = db_module._db_path
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "aify.db"

    def tearDown(self):
        db_module._db_path = self._saved_path
        self._tmp.cleanup()

    def test_an_existing_database_loses_the_table_and_its_index(self):
        con = sqlite3.connect(str(self.path))
        con.execute("CREATE TABLE agent_live_state (agent_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT)")
        con.execute("CREATE INDEX idx_agent_live_state_refresh_after ON agent_live_state(updated_at)")
        con.execute("INSERT INTO agent_live_state VALUES ('a', 'online', 'now')")
        con.commit()
        con.close()
        self.assertIn("agent_live_state", _tables(self.path), "control: the seeded table exists")

        asyncio.run(db_module.init_db(self.path))

        tables = _tables(self.path)
        self.assertNotIn("agent_live_state", tables)
        self.assertNotIn("idx_agent_live_state_refresh_after", tables)
        self.assertIn("agents", tables, "control: init_db still built the live schema")

    def test_a_new_database_never_has_it_and_a_second_start_is_harmless(self):
        asyncio.run(db_module.init_db(self.path))
        asyncio.run(db_module.init_db(self.path))
        self.assertNotIn("agent_live_state", _tables(self.path))


if __name__ == "__main__":
    unittest.main()
