"""`messages.timestamp` is INTEGER milliseconds while every sibling table stores ISO TEXT.

Two representations of one concept live in this schema:

    messages.timestamp          INTEGER   1787639177830
    dispatch_runs.requested_at  TEXT      '2026-08-25T06:26:17Z'
    read_receipts.read_at       TEXT      '2026-08-25T06:26:20Z'
    spawn_requests.created_at   TEXT      '2026-08-25T06:26:17Z'

Binding the wrong one is not an error. SQLite compares across storage classes by class first, and every
INTEGER sorts before every TEXT, so the predicate simply becomes a constant. The consequences are not
intuitive and they are not symmetric:

    timestamp <  '<iso>'   matches EVERY row     — and the rotation route DELETES on `timestamp < ?`
    timestamp >= '<iso>'   matches NO row        — and every analytics count silently reads zero

Both are silent. Nothing raises, nothing logs, and the count that comes back is a plausible number.

WHAT IS ACTUALLY CORRECT TODAY, checked before writing this: all 18 comparison sites against
messages.timestamp bind an integer, and the convention that keeps them right is the `_ms` suffix on the
bound name — `start_ms`, `end_ms`, `win_ms`, `today_start` from `time.time() * 1000`, and the rotation
`cutoff`. This file pins the column's storage class. The rotation DELETE's cutoff is pinned by running
it: `test_retention_and_artifact_deletion.py::test_a_message_past_the_window_is_expired` goes red for
an ISO cutoff (everything deleted) and for a seconds cutoff (nothing deleted). A source pin on the
cutoff expression and an SQLite demonstration used to sit here too; the first passed an ISO cutoff
built from `time.time() * 1000`, and the second exercised SQLite rather than this service, so both were
removed on 2026-09-18.

This repo has been bitten by this class before — the 2026-07-03 bughunt round recorded six SQL
lexical-timestamp defects. I hit it again while writing this file: my own retention query compared the
integer column against `date('now','-30 days')` and reported all 33,535 messages as older than 30 days.
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = (ROOT / "service" / "schema.py").read_text(encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()
