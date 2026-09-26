"""The service boots a database created before a migration-added column, and no schema index can stop it.

v0.7.1 review (S1). `init_db` runs `SCHEMA` BEFORE the migrations that add columns to older tables. 0.7.0
put `CREATE INDEX ... ON dispatch_controls(source_message_id)` into `SCHEMA`, so a database whose
`dispatch_controls` predated that column failed `init_db` with "no such column" and the service did not
start. Every other test boots a FRESH database, which is exactly the case that hides this.

Two tests: the boot itself, through the real `init_db`, and a gate DERIVED from the migration tables so
the next index on a migrated column fails here rather than on an operator's old database.
"""

import asyncio
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from service import db as db_module
from service.schema import SCHEMA

_ADD_COLUMN = re.compile(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)", re.I)
_SCHEMA_INDEX = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF NOT EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\(([^)]*)\)", re.I)


def _migrated_columns() -> set[tuple[str, str]]:
    """Every (table, column) some `*_MIGRATIONS` table in `service/db.py` adds to an older database."""
    found = set()
    for name, value in vars(db_module).items():
        if name.endswith("_MIGRATIONS") and isinstance(value, dict):
            for statement in value.values():
                match = _ADD_COLUMN.search(str(statement))
                if match:
                    found.add((match.group(1).lower(), match.group(2).lower()))
    return found


class AnOldDatabaseStillBootsTests(unittest.TestCase):
    def test_the_gate_reads_the_migration_tables(self):
        migrated = _migrated_columns()
        self.assertIn(("dispatch_controls", "source_message_id"), migrated, "control: the scan finds a known one")
        self.assertGreater(len(migrated), 20)

    def test_no_schema_index_names_a_column_a_migration_adds(self):
        migrated = _migrated_columns()
        indexes = _SCHEMA_INDEX.findall(SCHEMA)
        self.assertGreater(len(indexes), 10, "control: the scan finds the schema's indexes")
        offenders = []
        for index, table, columns in indexes:
            for column in (c.strip().split()[0].lower() for c in columns.split(",") if c.strip()):
                if (table.lower(), column) in migrated:
                    offenders.append(f"{index} on {table}({column})")
        self.assertEqual(offenders, [], (
            "SCHEMA runs before the migrations, so on an older database these indexes name a column that "
            "does not exist yet and init_db fails. Create them in the table's _migrate_* function, after "
            "its columns are added."))

    def test_a_database_whose_dispatch_controls_predates_source_message_id_boots(self):
        previous = db_module._db_path
        self.addCleanup(setattr, db_module, "_db_path", previous)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            asyncio.run(db_module.init_db(path))
            old = sqlite3.connect(path)
            try:
                old.execute("DROP INDEX IF EXISTS idx_dispatch_controls_source_message")
                old.execute("ALTER TABLE dispatch_controls DROP COLUMN source_message_id")
                old.commit()
                columns = {row[1] for row in old.execute("PRAGMA table_info(dispatch_controls)")}
            finally:
                old.close()
            self.assertNotIn("source_message_id", columns, "control: the database has the old shape")
            asyncio.run(db_module.init_db(path))
            booted = sqlite3.connect(path)
            try:
                columns = {row[1] for row in booted.execute("PRAGMA table_info(dispatch_controls)")}
                indexes = {row[1] for row in booted.execute("PRAGMA index_list(dispatch_controls)")}
            finally:
                booted.close()
            self.assertIn("source_message_id", columns)
            self.assertIn("idx_dispatch_controls_source_message", indexes, "the index must still be created")
