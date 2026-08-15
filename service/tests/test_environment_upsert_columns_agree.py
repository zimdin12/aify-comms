"""The environment upsert's two statements must stay one row shape.

`_record_environment_registration` writes an environment row two ways: an UPDATE when the row exists
and an INSERT when it does not. The UPDATE writes eleven columns; the INSERT writes those eleven plus
`id` and `registered_at`. That difference IS the design — a re-registering environment must not have
its identity or its first-seen time rewritten — and everything else about it is whatever the bridge
just reported.

THE FAILURE MODE IS SILENT AND ASYMMETRIC, which is why this is pinned rather than reviewed:

    added to the INSERT only  -> a FRESH environment gets it; every re-registering one keeps a stale
                                 value forever. Re-registration is the COMMON case, since a bridge
                                 heartbeats through here on every restart.
    added to the UPDATE only  -> a brand-new environment is missing it until its second heartbeat.

Neither raises. Both produce a table that looks populated.

DERIVED FROM THE SQL, not listed here. A hand-maintained expected-column list would be a third copy
to keep in step, and the first one to rot would be the test.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
LEAF = REPO / "service" / "api_core" / "environment_registration.py"
HELPER = "_record_environment_registration"

#: Columns the INSERT is EXPECTED to carry that the UPDATE does not. Both are immutable facts about
#: the row: which environment this is, and when it was first seen. A third name appearing here means
#: someone decided a column should survive re-registration, which is a decision worth making
#: deliberately rather than by adding it to one statement.
INSERT_ONLY = {"id", "registered_at"}


def _statements() -> tuple[str, str]:
    """The UPDATE and the INSERT SQL, from the helper's own source."""
    tree = ast.parse(LEAF.read_text(encoding="utf-8"))
    helper = next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == HELPER
    )
    literals = [
        n.value for n in ast.walk(helper)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    updates = [s for s in literals if "UPDATE environments" in s]
    inserts = [s for s in literals if "INSERT INTO environments" in s]
    assert len(updates) == 1, f"expected exactly one UPDATE, found {len(updates)}"
    assert len(inserts) == 1, f"expected exactly one INSERT, found {len(inserts)}"
    return updates[0], inserts[0]


def _update_columns(sql: str) -> set[str]:
    body = sql[sql.index("SET"):sql.index("WHERE")]
    return {m.group(1) for m in re.finditer(r"(\w+)\s*=\s*\?", body)}


def _insert_columns(sql: str) -> set[str]:
    match = re.search(r"environments\s*\((.*?)\)\s*VALUES", sql, re.S)
    assert match, "could not read the INSERT column list"
    return {c.strip() for c in match.group(1).replace("\n", " ").split(",") if c.strip()}


class EnvironmentUpsertColumnsAgreeTests(unittest.TestCase):
    def test_both_statements_are_found(self):
        """Without this the comparisons below would raise instead of failing informatively."""
        update, insert = _statements()
        self.assertIn("UPDATE environments", update)
        self.assertIn("INSERT INTO environments", insert)

    def test_the_scan_reads_a_plausible_number_of_columns(self):
        """Anti-vacuity: two empty sets agree with each other perfectly."""
        update, insert = _statements()
        self.assertGreaterEqual(len(_update_columns(update)), 8)
        self.assertGreaterEqual(len(_insert_columns(insert)), 10)

    def test_every_UPDATED_column_is_also_INSERTED(self):
        update, insert = _statements()
        missing = sorted(_update_columns(update) - _insert_columns(insert))
        self.assertEqual(
            [], missing,
            "these columns are written on re-registration but not on creation, so a brand-new "
            f"environment is missing them until its second heartbeat: {missing}")

    def test_the_INSERT_adds_ONLY_the_immutable_columns(self):
        update, insert = _statements()
        extra = _insert_columns(insert) - _update_columns(update)
        self.assertEqual(
            INSERT_ONLY, extra,
            "the INSERT and UPDATE have drifted. A column the INSERT writes and the UPDATE does not "
            "is one that every re-registering environment keeps a STALE value for, forever — and "
            "re-registration is the common case. If a new column really should survive "
            f"re-registration, add it to INSERT_ONLY deliberately. Found: {sorted(extra)}")

    def test_the_placeholder_count_matches_on_both_sides(self):
        """A column added without its `?` raises; a `?` without its column binds the wrong value."""
        update, insert = _statements()
        self.assertEqual(
            len(_update_columns(update)), update[update.index("SET"):update.index("WHERE")].count("?"))
        placeholders = re.search(r"VALUES\s*\(([^)]*)\)", insert, re.S)
        self.assertIsNotNone(placeholders)
        self.assertEqual(len(_insert_columns(insert)), placeholders.group(1).count("?"))


if __name__ == "__main__":
    unittest.main()
