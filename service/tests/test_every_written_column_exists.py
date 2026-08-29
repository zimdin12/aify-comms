r"""Every column a statement names is a column the schema declares.

WHAT THIS CATCHES. A misspelled column is an `OperationalError` at the moment the statement runs.
On a path the suite exercises, that is a red test; on a path it does not -- a reaper branch, an error
handler, a migration corner -- it ships and fires the first time an operator reaches it. The schema is
declared in one place and every statement is readable, so the question is answerable without running
anything.

MEASURED 2026-08-29, and the answer today is clean: 81 INSERT column lists, 224 UPDATE SET lists and
194 single-table SELECT lists -- 499 in all -- naming no column the schema lacks. A gate that finds
nothing on the day it is written is worth keeping only if it can be shown to speak, so both controls
below plant a typo and require it to be named.

WHY IT CAN READ THE STATEMENTS AT ALL. `service/tests/sql_sources.py`, which handles f-strings -- one
in eight SQL literals here is one, and a scan keyed on `ast.Constant` sees their pieces or nothing.

THE SCHEMA IS DERIVED, never listed: `CREATE TABLE` bodies from `service/schema.py` plus every
`ALTER TABLE ... ADD COLUMN` in the tree, because a column added by a migration is as real as one in
the original table and a gate that did not know that would accuse the code that uses it.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.tests.sql_sources import sql_literals

REPO = Path(__file__).resolve().parents[2]
SCHEMA_FILE = REPO / "service" / "schema.py"
SERVICE = REPO / "service"

CREATE_TABLE = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", re.IGNORECASE | re.DOTALL)
ADD_COLUMN = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", re.IGNORECASE)
INSERT_COLUMNS = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s*\(([^)]*)\)", re.IGNORECASE)
UPDATE_SET = re.compile(r"UPDATE\s+(\w+)\s+SET\s+(.*?)(?:\bWHERE\b|$)", re.IGNORECASE | re.DOTALL)
#: Single table, plain identifiers, nothing computed. Anything else is skipped rather than guessed at:
#: attributing a column to the wrong table is a false accusation, and this repo has already shipped
#: one scan that named fourteen innocent lines.
SIMPLE_SELECT = re.compile(
    r"SELECT\s+(?!DISTINCT\b)([\w\s,]+?)\s+FROM\s+(\w+)\s*(?:WHERE\b|ORDER\b|LIMIT\b|GROUP\b|$)",
    re.IGNORECASE | re.DOTALL,
)
NOT_A_COLUMN = {"AS", "DISTINCT", "COUNT", "MAX", "MIN", "NULL", "CASE"}


def schema_columns() -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table, body in CREATE_TABLE.findall(SCHEMA_FILE.read_text(encoding="utf-8")):
        columns = set()
        for raw in body.split("\n"):
            line = raw.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK")):
                continue
            name = line.split()[0].strip('"')
            if name.isidentifier():
                columns.add(name)
        tables[table] = columns
    for path in SERVICE.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for table, column in ADD_COLUMN.findall(path.read_text(encoding="utf-8", errors="replace")):
            tables.setdefault(table, set()).add(column)
    return tables


def unknown_columns(statements, tables) -> tuple[list[str], int]:
    """(complaints, how many column lists were actually examined)."""
    complaints: list[str] = []
    checked = 0
    for where, text in statements:
        flat = " ".join(text.split())
        for table, columns in INSERT_COLUMNS.findall(flat):
            if table not in tables:
                continue
            named = [c.strip().strip('"') for c in columns.split(",") if c.strip()]
            if not named or any(not c.isidentifier() for c in named):
                continue
            checked += 1
            missing = sorted(c for c in named if c not in tables[table])
            if missing:
                complaints.append(f"{where}  INSERT INTO {table} names {missing}")
        for table, assignments in UPDATE_SET.findall(flat):
            if table not in tables:
                continue
            named = re.findall(r"(?:^|,)\s*(\w+)\s*=", assignments)
            if not named:
                continue
            checked += 1
            missing = sorted(c for c in named if c not in tables[table])
            if missing:
                complaints.append(f"{where}  UPDATE {table} names {missing}")
        if flat.count("SELECT") == 1 and " JOIN " not in flat.upper():
            match = SIMPLE_SELECT.search(flat)
            if match:
                columns_text, table = match.groups()
                named = [c.strip() for c in columns_text.split(",") if c.strip()]
                if (table in tables and named
                        and all(c.isidentifier() for c in named)
                        and not any(c.upper() in NOT_A_COLUMN for c in named)):
                    checked += 1
                    missing = sorted(c for c in named if c not in tables[table])
                    if missing:
                        complaints.append(f"{where}  SELECT FROM {table} names {missing}")
    return complaints, checked


def service_statements():
    for path, line, text in sql_literals(SERVICE):
        yield f"{path.relative_to(REPO).as_posix()}:{line}", text


class EveryWrittenColumnExists(unittest.TestCase):
    def setUp(self) -> None:
        self.tables = schema_columns()

    def test_NO_STATEMENT_NAMES_A_COLUMN_THE_SCHEMA_LACKS(self):
        complaints, checked = unknown_columns(service_statements(), self.tables)
        self.assertEqual(complaints, [], (
            "these statements name columns the schema does not declare, which is an OperationalError "
            f"the first time the path runs (checked {checked} column list(s)):\n  "
            + "\n  ".join(complaints)
        ))

    def test_THE_SCAN_EXAMINES_A_REAL_POPULATION(self):
        """POSITIVE CONTROL. The assertion above is a list-is-empty check and an inert scan passes it
        perfectly. Measured 2026-08-29: 499 column lists across INSERT, UPDATE and simple SELECT."""
        _complaints, checked = unknown_columns(service_statements(), self.tables)
        self.assertGreater(checked, 400, (
            f"only {checked} column list(s) examined; the scan has stopped seeing the statements it "
            "governs and its clean verdict means nothing"
        ))

    def test_THE_SCHEMA_MAP_IS_NOT_EMPTY(self):
        """The other way the verdict could be empty: every table unknown, so every list is skipped."""
        self.assertGreater(len(self.tables), 20, "the schema scan found almost no tables")
        for table in ("agents", "messages", "agent_sessions", "terminal_sessions", "dispatch_runs"):
            self.assertIn(table, self.tables)
            self.assertGreater(len(self.tables[table]), 3, f"{table} has almost no columns")

    def test_it_knows_about_columns_added_by_a_MIGRATION(self):
        """`bridge_kind` is added by `ALTER TABLE bridge_instances ADD COLUMN`, not by the CREATE. A
        gate reading only the CREATE would accuse every statement that uses it."""
        self.assertIn("bridge_kind", self.tables["bridge_instances"])

    def test_THE_SCAN_CAN_SAY_NO(self):
        """NEGATIVE CONTROL, on statements written to fail -- one per shape, since a scan that saw
        only INSERTs would report the other two populations clean forever.

        The first attempt at this control planted a typo in the real tree and reported success
        without checking that the edit took: the source had the statement across three lines and the
        single-line replacement matched nothing. The scan looked silent when it had been shown
        nothing."""
        tables = {"agents": {"id", "status"}}
        statements = [
            ("planted.py:1", "INSERT INTO agents (id, statsu) VALUES (?,?)"),
            ("planted.py:2", "UPDATE agents SET statsu = ? WHERE id = ?"),
            ("planted.py:3", "SELECT id, statsu FROM agents WHERE id = ?"),
        ]
        complaints, checked = unknown_columns(statements, tables)
        self.assertEqual(checked, 3, "a shape was skipped, so its population is ungoverned")
        self.assertEqual(len(complaints), 3, f"the scan missed a planted column: {complaints}")
        for complaint in complaints:
            self.assertIn("statsu", complaint)

    def test_a_correct_statement_of_each_shape_is_left_alone(self):
        """The other half of the control: a scan that flagged everything would also pass the one
        above."""
        tables = {"agents": {"id", "status"}}
        statements = [
            ("ok.py:1", "INSERT INTO agents (id, status) VALUES (?,?)"),
            ("ok.py:2", "UPDATE agents SET status = ? WHERE id = ?"),
            ("ok.py:3", "SELECT id, status FROM agents WHERE id = ?"),
        ]
        complaints, checked = unknown_columns(statements, tables)
        self.assertEqual(checked, 3)
        self.assertEqual(complaints, [])


if __name__ == "__main__":
    unittest.main()
