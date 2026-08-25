"""The columns /stats filters on have an index behind them.

GET /stats is polled about every 15s and was the slowest endpoint in the whole cycle: 2,398 bytes of
response and a 168ms median, against 3.5ms for /settings at a similar size. It is compute-bound, not
payload-bound, and it issues 20 queries — three of which count `messages` by `source` across a
33,498-row table with no index on that column, so each one did a full SCAN.

Measured on a COPY of the live database (never the live one — the service is single-worker and the
fleet was running):

    count of source='direct'      19.6ms -> 1.1ms
    unread channel anti-join      17.8ms -> 0.2ms
    unread direct anti-join       29.9ms -> 30.0ms

67.3ms -> 31.3ms. The third does not improve and cannot: 'direct' is 32,862 of 33,529 rows, so the
index is chosen and still walks 98% of the table. Recorded rather than hidden, because a later reader
measuring 30ms there and finding an index would otherwise assume it was not working.

WHY A TEST AND NOT JUST A SCHEMA LINE: an index is invisible when it is missing. Nothing errors, the
query just gets slower as the table grows, and the endpoint had already reached 168ms without anyone
noticing. This pins the pairing — the column a hot query filters on, and the index that serves it —
so deleting one without the other is a red test rather than a silent regression.
"""
from __future__ import annotations

import re
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "service" / "schema.py"
STATS = ROOT / "service" / "routers" / "stats.py"


def schema_sql() -> str:
    return SCHEMA.read_text(encoding="utf-8")


def built_database() -> sqlite3.Connection:
    """A real database from the real schema. Asserting on the source text would only prove a line was
    typed; this proves sqlite accepts it and the planner can see it."""
    db = sqlite3.connect(":memory:")
    statements = re.findall(r"(CREATE (?:TABLE|INDEX|UNIQUE INDEX)[^;]+;)", schema_sql(), re.S)
    assert statements, "no CREATE statements found — the extraction, not the schema, is broken"
    for statement in statements:
        try:
            db.execute(statement)
        except sqlite3.OperationalError:
            # Some statements depend on tables created elsewhere (migrations). Skipping them is fine:
            # the assertions below fail loudly if the one under test was among them.
            continue
    return db


class StatsMessageCountsAreIndexed(unittest.TestCase):
    def test_the_source_column_has_an_index(self):
        db = built_database()
        indexes = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='messages'",
            )
        }
        self.assertIn(
            "idx_messages_source", indexes,
            "the index /stats depends on is gone; its three message counts are full scans again",
        )

    def test_the_planner_actually_uses_it(self):
        """The pairing, not the declaration. An index the planner ignores is a line of schema and a
        slower table."""
        db = built_database()
        plan = "\n".join(
            str(row[-1]) for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM messages WHERE source = 'direct'",
            )
        )
        self.assertIn(
            "idx_messages_source", plan,
            f"the planner does not use the index for the query it was added for: {plan}",
        )

    def test_stats_still_filters_on_the_column_the_index_serves(self):
        """The other half of the pairing. If /stats stops filtering on `source`, this index is dead
        weight on every message insert and should go — and somebody should be told, not left to find
        it years later."""
        source = STATS.read_text(encoding="utf-8")
        self.assertRegex(
            source, r"source\s*=\s*'(direct|channel)'",
            "/stats no longer filters messages by source; idx_messages_source may now be dead weight",
        )

    def test_the_slow_anti_join_is_not_claimed_to_be_fixed(self):
        """Guards the honesty of the comment, which is the only place the 30ms is recorded.

        A future reader profiling /stats will find one query still at 30ms next to an index that was
        added to speed it up. Without the note they will reasonably conclude the index is broken and
        go looking. The note says it is not, and says why."""
        text = schema_sql()
        self.assertIn("idx_messages_source", text)
        self.assertIn(
            "32,862 of", text,
            "the note explaining why the direct anti-join does not improve has been removed",
        )


if __name__ == "__main__":
    unittest.main()
