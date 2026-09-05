"""`/messages/recent` can reach history older than its newest page, without dropping a message.

THE OPERATOR REPORT, 2026-09-05: "I notice that I see only small part of messages in dashboard. it
feels like upper messages get deleted. why is that. manager sent me lots of messages and I cannot
see them?" Nothing was deleted. The dashboard fetches ONE global window -- the newest 80 rows across
every conversation -- and filters it per conversation for display, so a busy fleet pushes a single
peer's older messages out of a window they were never individually allotted. Measured on the live
database that day: 137 recent messages from `sc-manager`, of which 43 fell inside the newest 80.

The endpoint had no cursor at all, so the other 94 could not be requested by any caller. This gate
covers the parameter that makes them reachable, and the two properties that make it safe.

INCLUSIVE, AND THAT IS THE WHOLE DESIGN. `timestamp` is milliseconds and a channel fanout writes
several rows inside one, so a page boundary can land in the middle of a tie. An EXCLUSIVE cursor
(`m.timestamp < ?`) silently drops every row sharing the boundary millisecond -- message loss, in
the feature built to stop message loss. `test_AN_EXCLUSIVE_CURSOR_WOULD_DROP_THE_TIED_ROWS` is the
negative control: it runs that form against the same fixture and shows the gap appearing, so the
`<=` is evidenced rather than asserted.

The overlap it costs is one page's worth of already-held rows, which the client discards by id.

AND THE PLAN STILL STOPS AT THE PAGE. `test_the_paged_form_does_not_sort_the_table` is the paged
sibling of `test_the_recent_messages_poll_does_not_sort_the_table`, which covers the UNPAGED form
only. A cursor that reintroduced `USE TEMP B-TREE FOR ORDER BY` would put a 33k-row sort back on an
endpoint every open tab polls every 15 seconds, which is what the `+m.source` hint exists to prevent.

THE STATEMENT IS IMPORTED, NEVER RETYPED. It comes from the same reader the unpaged gate uses, so
these tests cannot pass against a query the route does not run -- and there is one implementation of
"find the recent-messages statement", not two that agree until one is edited.
"""
from __future__ import annotations

import sqlite3
import unittest

from service.schema import SCHEMA
from service.tests.test_the_recent_messages_poll_does_not_sort_the_table import (
    _recent_messages_statement,
)

#: One millisecond shared by three messages, which is what makes the boundary case real rather than
#: hypothetical. `channel_send.py` stamps `int(time.time()*1000)` per row inside a loop.
TIED_MS = 1_756_000_000_000

#: Comfortably more than one page, so paging has somewhere to go.
TOTAL = 40

#: The page size whose boundary lands INSIDE the tie: 19 rows are newer than the shared
#: millisecond, so a page of 20 takes one of the four tied rows and leaves three. A size that
#: swallows the group whole loses nothing even when exclusive -- the first version of the
#: negative control used 8 and passed against the very form it exists to condemn.
SPLITS_THE_TIE = 20


def _seeded() -> sqlite3.Connection:
    """A message table with INTEGER millisecond timestamps, as the write paths actually produce.

    The unpaged gate seeds ISO STRINGS, which is harmless for a plan assertion but useless here:
    comparing an integer cursor against a text timestamp makes SQLite order every integer before
    every string, so a cursor test on that fixture would prove nothing about production.
    """
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    rows = []
    for i in range(TOTAL):
        # Every fifth row is a channel message with no recipient; the rest are direct with one.
        # Both arms match the endpoint's predicate, and nothing else does.
        is_channel = i % 5 == 0
        rows.append((
            f"m{i:03d}", "sender", None if is_channel else "recipient", "s", "b",
            TIED_MS + i * 1000, "channel" if is_channel else "direct", "note",
        ))
    # THE TIE: three more rows landing on the millisecond m020 already occupies, so FOUR rows
    # share it -- placed mid-history so a page boundary can be made to fall inside the group.
    for n, suffix in enumerate(("a", "b", "c")):
        rows.append((
            f"tie{suffix}", "sender", "recipient", "s", "b",
            TIED_MS + 20 * 1000, "direct", "note",
        ))
    db.executemany(
        "INSERT INTO messages (id, from_agent, to_agent, subject, body, timestamp, source, type)"
        " VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    return db


def _page(db: sqlite3.Connection, limit: int, before: int | None) -> list[tuple[str, int]]:
    """One page, as the route issues it: `(before, before, limit + 1)`, newest first."""
    sql = _recent_messages_statement()
    return [
        (row["id"], row["timestamp"])
        for row in db.execute(sql, (before, before, limit + 1))
    ]


class RecentMessagesPagesBackwardsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _seeded()
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

    def test_POSITIVE_CONTROL_the_fixture_holds_more_than_one_page(self) -> None:
        """A fixture that fits in one page would make every paging assertion below vacuous."""
        everything = _page(self.db, limit=10_000, before=None)
        self.assertEqual(len(everything), TOTAL + 3)
        # And it is ordered newest first, which every assertion here depends on.
        stamps = [ts for _id, ts in everything]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_POSITIVE_CONTROL_the_tie_is_really_a_tie(self) -> None:
        """If the fixture stopped producing a shared millisecond, the boundary tests would pass
        while proving nothing about the case they exist for."""
        everything = _page(self.db, limit=10_000, before=None)
        at_tie = [mid for mid, ts in everything if ts == TIED_MS + 20 * 1000]
        self.assertEqual(len(at_tie), 4, f"expected four rows sharing one millisecond, got {at_tie}")

    def test_no_cursor_returns_the_newest_page(self) -> None:
        page = _page(self.db, limit=10, before=None)
        everything = _page(self.db, limit=10_000, before=None)
        self.assertEqual([mid for mid, _ in page][:10], [mid for mid, _ in everything][:10])

    def test_a_cursor_returns_only_messages_at_or_older_than_it(self) -> None:
        cutoff = TIED_MS + 25 * 1000
        page = _page(self.db, limit=10_000, before=cutoff)
        self.assertTrue(page, "the cursor returned nothing, so the assertion below is vacuous")
        self.assertTrue(all(ts <= cutoff for _id, ts in page), "a cursor let a newer message through")

    def test_paging_to_the_end_reaches_every_message_exactly_once(self) -> None:
        """The whole point: what the client can eventually see must be the whole history.

        This walks the same loop the dashboard runs -- take the oldest row of the page, ask for that
        timestamp again, discard ids already held -- and requires the union to equal the full set.
        """
        everything = {mid for mid, _ in _page(self.db, limit=10_000, before=None)}
        # INCLUDING THE SIZES WHOSE BOUNDARY LANDS INSIDE THE TIE. A walk proven only at a page size
        # that swallows the tied group whole never exercises the case the cursor is inclusive for.
        for limit in (7, SPLITS_THE_TIE, SPLITS_THE_TIE + 1, SPLITS_THE_TIE + 2):
            with self.subTest(limit=limit):
                seen: set[str] = set()
                before: int | None = None
                # Bounded, so a cursor that stops advancing fails here rather than hanging.
                for _ in range(TOTAL + 10):
                    page = _page(self.db, limit=limit, before=before)
                    fresh = [mid for mid, _ in page if mid not in seen]
                    if not fresh:
                        break
                    seen.update(fresh)
                    before = min(ts for _id, ts in page)
                self.assertEqual(seen, everything, "paging backwards did not reach the whole history")

    def test_AN_EXCLUSIVE_CURSOR_WOULD_DROP_THE_TIED_ROWS(self) -> None:
        """The negative control, and the reason `<=` is load-bearing rather than a style choice.

        Same fixture, same loop, one character different. The exclusive form skips past every row
        sharing the boundary millisecond, so the tie disappears from the union -- silent loss of
        exactly the messages this feature exists to recover.
        """
        exclusive = _recent_messages_statement().replace("m.timestamp <= ?", "m.timestamp < ?")
        self.assertNotEqual(exclusive, _recent_messages_statement(), "the cursor is already exclusive")

        everything = {mid for mid, _ in _page(self.db, limit=10_000, before=None)}
        seen: set[str] = set()
        before: int | None = None
        for _ in range(TOTAL + 10):
            rows = [
                (row["id"], row["timestamp"])
                for row in self.db.execute(exclusive, (before, before, SPLITS_THE_TIE))
            ]
            fresh = [mid for mid, _ in rows if mid not in seen]
            if not fresh:
                break
            seen.update(fresh)
            before = min(ts for _id, ts in rows)

        missing = everything - seen
        self.assertTrue(
            missing,
            "an exclusive cursor lost nothing on this fixture, so it no longer demonstrates the "
            "hazard and the tie seeding above has stopped working",
        )
        self.assertTrue(
            any(mid.startswith("tie") for mid in missing),
            f"the loss should be the rows sharing a millisecond; lost {sorted(missing)}",
        )

    def test_the_paged_form_does_not_sort_the_table(self) -> None:
        """A cursor must not put the 33k-row sort back on a 15-second poll."""
        plan = " | ".join(
            row[-1] for row in self.db.execute(
                "EXPLAIN QUERY PLAN " + _recent_messages_statement(),
                (TIED_MS + 25 * 1000, TIED_MS + 25 * 1000, 81),
            )
        )
        self.assertNotIn("TEMP B-TREE", plan, f"the paged form sorts every match. Plan: {plan}")
        self.assertIn("idx_messages_timestamp", plan, f"the LIMIT cannot stop it. Plan: {plan}")


if __name__ == "__main__":
    unittest.main()
