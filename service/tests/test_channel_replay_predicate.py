"""The channel-replay reconciler could not match a single row it exists to replay.

AUDIT FINDING 3/3-1, source-found by `comms-senior-dev`, then measured.

`_replay_undelivered_channel_messages_on_env_recovery` promises to replay stored channel fanout
messages when a managed environment recovers. Its predicate was:

    datetime(m.timestamp) >= datetime('now', ?)

`messages.timestamp` is epoch MILLISECONDS. SQLite's `datetime(1786402075333)` returns NULL, so the
comparison was NULL — never true. Measured on the live DB before the fix:

    channel messages                      665
    matched by the BROKEN predicate         0
    matched by the CORRECTED predicate    115

So the reconciler had never fired. A recovered environment silently got no replay, and the absence
of a dispatch_run was again readable as "no work owed".

SIXTH timestamp bug of this class in this repo, and the same shape as the `finished_at` guard that
excluded its own target rows for two months. What makes it a drifted copy rather than a
misunderstanding: other code in the same file already does it correctly
(`datetime(timestamp / 1000, 'unixepoch')`).

These tests pin the epoch-ms handling directly against SQLite rather than through the reconciler,
because the failure is in how the column is READ, and a test that only exercised the reconciler
would pass the moment any row happened to match for another reason.
"""

from __future__ import annotations

import sqlite3
import time
import unittest


class ChannelReplayTimestampPredicateTests(unittest.TestCase):
    """SQLite semantics for the epoch-ms column, verified against SQLite itself."""

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE messages (id TEXT, timestamp INTEGER, channel TEXT)")
        now_ms = int(time.time() * 1000)
        rows = [
            ("recent", now_ms - 60_000),            # a minute ago — must be inside any horizon
            ("older", now_ms - 3 * 3600 * 1000),    # three hours ago
            ("ancient", now_ms - 800 * 3600 * 1000),  # outside the 720h horizon (800 > 720)
        ]
        self.db.executemany("INSERT INTO messages VALUES (?,?,'sand-castle')", rows)

    def _count(self, predicate, horizon="-720 hours"):
        return self.db.execute(
            f"SELECT COUNT(*) FROM messages WHERE {predicate}", (horizon,)
        ).fetchone()[0]

    def test_the_broken_predicate_matches_NOTHING(self):
        """Pinned so nobody 'simplifies' the corrected form back to this."""
        self.assertEqual(
            self._count("datetime(timestamp) >= datetime('now', ?)"),
            0,
            "datetime() on epoch-ms yields NULL, so this can never be true",
        )

    def test_datetime_of_raw_epoch_ms_is_NULL(self):
        self.assertIsNone(self.db.execute("SELECT datetime(1786402075333)").fetchone()[0])

    def test_the_corrected_predicate_matches_rows_inside_the_horizon(self):
        self.assertEqual(self._count("datetime(timestamp / 1000, 'unixepoch') >= datetime('now', ?)"), 2)

    def test_the_corrected_predicate_still_EXCLUDES_rows_outside_it(self):
        """A fix that matched everything would be its own bug — the horizon must still bound."""
        self.assertEqual(
            self._count("datetime(timestamp / 1000, 'unixepoch') >= datetime('now', ?)", "-1 hours"),
            1,
            "only the one-minute-old row is inside a 1h horizon",
        )

    def test_the_horizon_boundary_is_respected_at_both_ends(self):
        self.assertEqual(self._count("datetime(timestamp / 1000, 'unixepoch') >= datetime('now', ?)", "-2 hours"), 1)
        self.assertEqual(self._count("datetime(timestamp / 1000, 'unixepoch') >= datetime('now', ?)", "-4 hours"), 2)
        self.assertEqual(self._count("datetime(timestamp / 1000, 'unixepoch') >= datetime('now', ?)", "-99999 hours"), 3)


class ReconcilerSourceTests(unittest.TestCase):
    """The reconciler itself must use the corrected form."""

    def test_the_replay_reconciler_divides_by_1000(self):
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "service" / "reconcilers" / "dispatch_queue.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        at = text.index("_replay_undelivered_channel_messages_on_env_recovery")
        body = text[at:at + 6000]
        self.assertIn("datetime(m.timestamp / 1000, 'unixepoch')", body)
        self.assertNotIn("datetime(m.timestamp) >=", body,
                         "the raw-epoch form never matches and must not come back")

    def test_no_reconciler_compares_a_raw_millisecond_column_as_a_date(self):
        """Sweep for the whole class, not just the one instance the reviewer happened to find."""
        import re
        from pathlib import Path
        src = Path(__file__).resolve().parents[2] / "service" / "reconcilers" / "dispatch_queue.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        # `datetime(<something>.timestamp)` with no /1000 — the exact broken shape.
        offenders = re.findall(r"datetime\(\s*\w*\.?timestamp\s*\)", text)
        self.assertEqual(offenders, [], f"raw epoch-ms compared as a date: {offenders}")


if __name__ == "__main__":
    unittest.main()
