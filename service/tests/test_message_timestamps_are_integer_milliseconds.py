"""`messages.timestamp` is INTEGER milliseconds while every sibling table stores ISO TEXT.

Two representations of one concept live in this schema:

    messages.timestamp          INTEGER   1787639177830
    dispatch_runs.requested_at  TEXT      '2026-08-25T06:26:17Z'
    read_receipts.read_at       TEXT      '2026-08-25T06:26:20Z'
    spawn_requests.created_at   TEXT      '2026-08-25T06:26:17Z'

Binding the wrong one is not an error. SQLite compares across storage classes by class first, and every
INTEGER sorts before every TEXT, so the predicate simply becomes a constant. Demonstrated below rather
than asserted from the manual, because the consequences are not intuitive and they are not symmetric:

    timestamp <  '<iso>'   matches EVERY row     — and the rotation route DELETES on `timestamp < ?`
    timestamp >= '<iso>'   matches NO row        — and every analytics count silently reads zero

Both are silent. Nothing raises, nothing logs, and the count that comes back is a plausible number.

WHAT IS ACTUALLY CORRECT TODAY, checked before writing this: all 18 comparison sites against
messages.timestamp bind an integer, and the convention that keeps them right is the `_ms` suffix on the
bound name — `start_ms`, `end_ms`, `win_ms`, `today_start` from `time.time() * 1000`, and the rotation
`cutoff`. So this gate does not fix a break. It pins the two facts a future edit could quietly reverse:
the column's storage class, and the type of the value the DELETE binds.

This repo has been bitten by this class before — the 2026-07-03 bughunt round recorded six SQL
lexical-timestamp defects. I hit it again while writing this file: my own retention query compared the
integer column against `date('now','-30 days')` and reported all 33,535 messages as older than 30 days.
"""
from __future__ import annotations

import ast
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = (ROOT / "service" / "schema.py").read_text(encoding="utf-8")
MAINTENANCE = (ROOT / "service" / "routers" / "maintenance.py").read_text(encoding="utf-8")


class MessageTimestampsAreIntegerMilliseconds(unittest.TestCase):
    def test_the_column_is_declared_integer(self):
        """If this ever becomes TEXT, every `_ms` binding in the service silently stops matching."""
        db = sqlite3.connect(":memory:")
        create = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS messages"):]
        create = create[:create.index(";") + 1]
        db.execute(create)
        types = {row[1]: row[2].upper() for row in db.execute("PRAGMA table_info(messages)")}
        self.assertEqual(
            types.get("timestamp"), "INTEGER",
            "messages.timestamp changed storage class; every integer binding against it now matches "
            "nothing, and the rotation DELETE now matches everything",
        )

    def test_a_string_cutoff_would_delete_every_message(self):
        """The demonstration, run rather than described.

        This is why the column type above is worth a test of its own: the highest-consequence site in
        the codebase is a DELETE whose predicate becomes universally true under the wrong type."""
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, timestamp INTEGER)")
        now_ms = 1787639177830
        db.executemany(
            "INSERT INTO messages VALUES (?, ?)",
            [(f"m{i}", now_ms - i * 1000) for i in range(10)],   # all seconds old
        )

        correct = db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp < ?", (now_ms - 30 * 86400 * 1000,),
        ).fetchone()[0]
        self.assertEqual(correct, 0, "an integer cutoff 30 days back matched recent messages")

        wrong = db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp < ?", ("2026-07-26T00:00:00Z",),
        ).fetchone()[0]
        self.assertEqual(wrong, 10, "SQLite's cross-class comparison changed; re-read this whole file")

        # The other direction, which is how a read goes quietly to zero.
        blind = db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ?", ("2026-07-26T00:00:00Z",),
        ).fetchone()[0]
        self.assertEqual(blind, 0)

    def test_the_rotation_cutoff_is_derived_as_integer_milliseconds(self):
        """The DELETE site specifically, scoped to the function that owns it.

        Parsed rather than grepped, so a comment mentioning the expression cannot satisfy it — and
        scoped to `rotate`, because a first version of this test walked the whole module and used
        `any()`. maintenance.py has THREE `cutoff` assignments; breaking the one the DELETE uses
        left the other two satisfying `any()` and the test stayed green. Watched it happen.
        """
        tree = ast.parse(MAINTENANCE)
        rotate = next(
            (node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'rotate'),
            None,
        )
        self.assertIsNotNone(rotate, 'the rotate route is gone or was renamed')

        sources = [
            ast.unparse(node.value) for node in ast.walk(rotate)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'cutoff' for t in node.targets)
        ]
        self.assertTrue(sources, 'the rotation cutoff assignment is gone or was renamed')
        for src in sources:
            # EVERY one, not any: the point is that the value this route deletes on is integer
            # milliseconds, and a second cutoff in scope must not be able to vouch for the first.
            self.assertIn(
                '1000', src,
                f'the rotation cutoff is no longer scaled to milliseconds: {src}',
            )
            self.assertIn(
                'time', src,
                f'the rotation cutoff is no longer derived from a clock: {src}',
            )
    def test_the_delete_still_compares_the_column_this_file_is_about(self):
        """The other half of the pairing. If rotation stops deleting on `timestamp`, this file is
        guarding a site that no longer exists and should be revisited rather than left to pass."""
        self.assertIn(
            '"timestamp < ?"', MAINTENANCE,
            "rotation no longer deletes messages on a timestamp comparison",
        )


if __name__ == "__main__":
    unittest.main()
