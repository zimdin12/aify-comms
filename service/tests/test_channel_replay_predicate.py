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
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _product_sources():
    """(path, text) for every non-test Python source under service/, mcp/ and scripts/."""
    for base in ("service", "mcp", "scripts"):
        root = REPO / base
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or "tests" in path.parts:
                continue
            yield path, path.read_text(encoding="utf-8", errors="replace")


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

    def test_the_replay_predicate_divides_by_1000_WHEREVER_IT_LIVES(self):
        """FINDS THE CODE RATHER THAN NAMING ITS FILE.

        This sliced 6000 characters out of `dispatch_queue.py` from a function name, and went red on
        a v0.5.4 relocation that changed nothing about the predicate — the fourth location pin in
        this repo to do that. The predicate is what matters, so the product tree is searched for it
        and required to have exactly one writer.

        `test_channel_replay_query.py` now also asserts this by EXECUTING the query, which is the
        stronger check; this one survives because "the broken form has not come back ANYWHERE" is a
        different question from "this one query behaves".
        """
        holders = [
            path for path, text in _product_sources()
            if "datetime(m.timestamp / 1000, 'unixepoch')" in text
        ]
        self.assertEqual(1, len(holders), f"expected exactly one writer of this predicate: {holders}")
        self.assertIn(
            "test_channel_replay_query.py",
            {p.name for p in (Path(__file__).resolve().parent).iterdir()},
            "the behavioural successor to this source check must exist")

    def test_NOTHING_ANYWHERE_compares_a_raw_millisecond_column_as_a_date(self):
        """The whole class, tree-wide — which is what this always claimed to be.

        Its docstring said "sweep for the whole class, not just the one instance", and then it read
        one file. The claim is now true: every product source is scanned, so the same drift landing
        in another module fails here rather than surviving because nobody pointed a test at it.
        """
        import re

        offenders = [
            f"{path.relative_to(REPO).as_posix()}: {match}"
            for path, text in _product_sources()
            for match in re.findall(r"datetime\(\s*\w*\.?timestamp\s*\)", text)
        ]
        self.assertEqual([], offenders, f"raw epoch-ms compared as a date: {offenders}")


if __name__ == "__main__":
    unittest.main()
