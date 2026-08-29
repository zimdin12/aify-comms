r"""`/messages/recent` returns a page of 80 without sorting every message to get it.

THE MEASUREMENT, taken in the container against the live database on 2026-08-29. `messages` holds
34,107 rows and 33,619 match this endpoint's predicate -- 98.6 percent, because almost every message
is either a direct one with a recipient or a channel one without. The plan was::

    MULTI-INDEX OR
      INDEX 1  SEARCH m USING INDEX idx_messages_source (source=?)
      INDEX 2  SEARCH m USING INDEX idx_messages_source (source=?)
    USE TEMP B-TREE FOR ORDER BY

so SQLite indexed the two `source` values and then materialised and sorted all 33,619 matches to take
the newest 81. This is the dashboard's `messages` slice, one of the ten a refresh cycle settles, and
its cost grew with a table that has no automatic retention.

THE EVIDENCE IS THE PLAN, NOT A STOPWATCH. Wall-clock on this host is not usable -- the same code has
timed 44-47 ms and then 22-25 ms minutes apart, because the live fleet is the load. A plan is
deterministic. The timings that do exist are corroboration and were taken four times alternating with
the hinted form FIRST, so the page cache cannot be the explanation: 0.06 / 36.83 / 0.06 / 35.79 ms.

WHY A 200-ROW FIXTURE IS ENOUGH. The planner's choice here is structural, not statistical: it makes
the same one at 200, 1,000 and 5,000 rows, measured before this test was written. So the test seeds
the smallest table that reproduces it rather than pretending to hold 34,107.

AND IT READS THE REAL STATEMENT. The SQL is extracted from `inbox.py` by the shared f-string-aware
reader rather than retyped here -- a test that retypes the query it is proving passes happily while
the route runs something else.
"""
from __future__ import annotations

import sqlite3
import unittest

from pathlib import Path

from service.schema import SCHEMA
from service.tests.sql_sources import sql_literals

INBOX = Path(__file__).resolve().parents[2] / "service" / "routers" / "dispatch_messages"


def _recent_messages_statement() -> str:
    """The one statement in `inbox.py` that selects the recent-message page."""
    found = [
        text for path, _line, text in sql_literals(INBOX)
        if path.name == "inbox.py"
        and "FROM messages m" in text
        and "ORDER BY m.timestamp DESC" in text
        and "LIMIT ?" in text
        and "to_agent IS NULL" in text
    ]
    if len(found) != 1:
        raise AssertionError(
            f"expected exactly one recent-messages statement in inbox.py, found {len(found)}"
        )
    return found[0]


def _seeded(rows: int = 200) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.executemany(
        "INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp, source, type)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [
            (f"m{i}", "sender", None if i % 5 == 0 else "recipient", "s", "b",
             f"2026-08-{(i % 28) + 1:02d}T00:00:{i % 60:02d}Z",
             "channel" if i % 5 == 0 else "direct", "note")
            for i in range(rows)
        ],
    )
    db.commit()
    return db


class TheRecentMessagesPollDoesNotSortTheTableTests(unittest.TestCase):
    def test_the_statement_was_found_and_is_the_endpoints_own(self) -> None:
        """The control. A test that silently found nothing would assert nothing below."""
        sql = _recent_messages_statement()
        self.assertIn("read_receipts", sql, "this is not the recent-messages join")
        self.assertIn("m.source", sql)

    def test_it_walks_the_timestamp_index_instead_of_sorting_the_matches(self) -> None:
        db = _seeded()
        try:
            plan = [row[-1] for row in db.execute(
                "EXPLAIN QUERY PLAN " + _recent_messages_statement(), (81,)
            )]
        finally:
            db.close()
        joined = " | ".join(plan)
        self.assertNotIn("TEMP B-TREE", joined, (
            "the poll sorts every matching message to return one page; measured against the live "
            "database that is 33,619 rows sorted per refresh cycle. Plan: " + joined
        ))
        self.assertIn("idx_messages_timestamp", joined, (
            "it must walk the timestamp index so the LIMIT can stop it. Plan: " + joined
        ))

    def test_THE_INDEXED_FORM_IS_WHAT_THE_HINT_PREVENTS(self) -> None:
        """The negative control, and the reason the `+` is not decoration.

        Without it the planner takes the OR-of-two-index-searches path and has to sort. Asserting
        only that the current form is fast leaves no evidence that the hint is what made it so.
        """
        indexed = _recent_messages_statement().replace("+m.source", "m.source")
        self.assertNotEqual(indexed, _recent_messages_statement(), "the hint is already gone")
        db = _seeded()
        try:
            plan = " | ".join(row[-1] for row in db.execute("EXPLAIN QUERY PLAN " + indexed, (81,)))
        finally:
            db.close()
        self.assertIn("TEMP B-TREE", plan, (
            "without the hint this used to sort every match; if SQLite no longer does, the hint has "
            "stopped earning its place and should be reconsidered rather than kept. Plan: " + plan
        ))

    def test_the_hint_changes_no_result(self) -> None:
        """Same rows, same order. `+` is a planner instruction, not a filter."""
        sql = _recent_messages_statement()
        indexed = sql.replace("+m.source", "m.source")
        db = _seeded()
        try:
            hinted_rows = [r[0] for r in db.execute(sql, (81,))]
            indexed_rows = [r[0] for r in db.execute(indexed, (81,))]
        finally:
            db.close()
        self.assertEqual(hinted_rows, indexed_rows)
        self.assertEqual(len(hinted_rows), 81, "the fixture must fill a page, or this proves little")


if __name__ == "__main__":
    unittest.main()
