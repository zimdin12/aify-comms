"""A database that already existed ends up shaped like one created today.

Adding a column can mean editing two places: `service/schema.py` so a new database has it, and the
matching `*_MIGRATIONS` dict in `service/db.py` so an existing one gains it. Doing only the schema is
invisible in development -- every test database is fresh, so everything passes -- and breaks only on a
machine that already has data, which is every real one. `launcher_version` and
`launcher_registry_fingerprint` were added on 2026-08-24 and the operator's live database had neither,
so the migration was the path that had to work.

THE SCHEMA IS NOT REQUIRED TO DECLARE EVERY MIGRATED COLUMN, and asserting that was this test's first
version. `subagents_at` and `ready` live only in migrations, and that works: the ALTERs are guarded by
`column not in existing` and run on EVERY init, so a brand-new database gets schema.py's tables and
then the same ALTERs. Both paths converge. Had that assertion stood, it would have sent someone to
"fix" schema.py for two columns that are correct.

So the invariant is convergence, not declaration: whatever a fresh database ends up with after init,
an older one must end up with too.
"""
from __future__ import annotations

import re
import sqlite3
import unittest

from service import db as db_module
from service import schema as schema_module

MIGRATION_DICTS = {
    name: value
    for name, value in vars(db_module).items()
    if name.endswith("_MIGRATIONS") and isinstance(value, dict) and value
}


def table_of(statements) -> str:
    tables = {re.search(r"ALTER TABLE (\w+)", s).group(1) for s in statements}
    assert len(tables) == 1, f"one migration dict alters more than one table: {sorted(tables)}"
    return tables.pop()


def columns_of(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def apply_guarded(connection, table: str, migrations: dict) -> None:
    """The same guard db.py uses: skip a column the table already has."""
    existing = columns_of(connection, table)
    for column, statement in migrations.items():
        if column not in existing:
            connection.execute(statement)


def fresh_shape(table: str, migrations: dict) -> set[str]:
    """What a brand-new database ends up with: schema.py, then the guarded migrations."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(schema_module.SCHEMA)
    apply_guarded(connection, table, migrations)
    shape = columns_of(connection, table)
    connection.close()
    return shape


class MigratedMatchesFreshTests(unittest.TestCase):
    def test_the_scan_found_the_migration_dicts(self):
        """Positive control: an empty scan makes every assertion below vacuous."""
        self.assertGreaterEqual(len(MIGRATION_DICTS), 5, sorted(MIGRATION_DICTS))
        self.assertIn("ENVIRONMENT_MIGRATIONS", MIGRATION_DICTS)

    def test_an_older_database_converges_on_the_same_shape(self):
        """Each table rebuilt WITHOUT the columns its migrations manage -- which is what a database
        from before those migrations looks like -- then migrated, and compared to a fresh one."""
        for name, migrations in MIGRATION_DICTS.items():
            with self.subTest(dict=name):
                table = table_of(migrations.values())
                fresh = fresh_shape(table, migrations)
                older = fresh - set(migrations)
                self.assertTrue(older, f"{table}: migrations claim every column; nothing to build from")

                connection = sqlite3.connect(":memory:")
                connection.execute(
                    f"CREATE TABLE {table} ({', '.join(f'{c} TEXT' for c in sorted(older))})"
                )
                connection.execute(
                    f"INSERT INTO {table} ({', '.join(sorted(older))}) "
                    f"VALUES ({', '.join(['?'] * len(older))})",
                    tuple("x" for _ in older),
                )
                apply_guarded(connection, table, migrations)

                self.assertEqual(
                    columns_of(connection, table), fresh,
                    f"{table}: an existing database and a new one disagree on "
                    f"{sorted(columns_of(connection, table) ^ fresh)}",
                )
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1,
                    f"{table}: migrating dropped the existing row",
                )
                connection.close()

    def test_running_the_migrations_twice_changes_nothing(self):
        """init_db runs on every start, so the guard is load-bearing rather than incidental."""
        for name, migrations in MIGRATION_DICTS.items():
            with self.subTest(dict=name):
                table = table_of(migrations.values())
                connection = sqlite3.connect(":memory:")
                connection.executescript(schema_module.SCHEMA)
                apply_guarded(connection, table, migrations)
                once = columns_of(connection, table)
                apply_guarded(connection, table, migrations)
                self.assertEqual(columns_of(connection, table), once)
                connection.close()

    def test_the_two_launcher_columns_are_covered_by_the_rule_above(self):
        """Named explicitly because they are why this file exists: a general test that quietly stopped
        covering them would otherwise still pass."""
        self.assertIn("launcher_version", db_module.ENVIRONMENT_MIGRATIONS)
        self.assertIn("launcher_registry_fingerprint", db_module.ENVIRONMENT_MIGRATIONS)
        shape = fresh_shape("environments", db_module.ENVIRONMENT_MIGRATIONS)
        self.assertIn("launcher_version", shape)
        self.assertIn("launcher_registry_fingerprint", shape)


if __name__ == "__main__":
    unittest.main()
