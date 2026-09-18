"""A channel send is visible to the transcript, whichever way "no recipient" was written.

TWO SHAPES OF ONE CHANNEL MESSAGE. A send writes a CANONICAL row with no recipient -- that is the
channel transcript -- plus one fan-out copy per member, each addressed to that member, which is what
drives inboxes and unread counts. Measured on the live database, 2026-08-28: 667 channel rows, 179
canonical and 488 copies.

TWO READERS DISAGREED ABOUT WHAT "NO RECIPIENT" MEANS. `channel_replay_query` excludes both shapes
(`to_agent IS NOT NULL AND to_agent != ''`); the transcript predicate accepted only NULL. Nothing is
broken today -- all 179 are NULL and none is an empty string -- but a row written with `''` would be
invisible HERE and in every inbox, since those match `to_agent = ?` and never `''`. A channel message
nobody can see, with no error raised anywhere.

THE INVARIANT HOLDS BY OMISSION, which is why it is worth pinning. The canonical INSERTs in
`channel_send.py` and `channel_membership.py` do not list the column, so it takes its default. A later
edit making those statements uniform with the fan-out INSERT beside them is all it would take.

WHAT THIS ROUND CHECKED AND FOUND HEALTHY, recorded so it is not re-walked. The 179/488 split looks
alarming and is not: the canonical rows ARE read, as the transcript. Of 179, twenty-two produced no
fan-out copies at all, and every one is explained -- 21 are `_system` join/leave notices, which have
no recipients by design, and the 22nd is an agent posting to a channel whose only member is itself.
The unread badge and the inbox list were checked against each other on six live agents with counts
from 1 to 97 and agree exactly; the inbox already computes its count with the LIMIT stripped, which is
the thing `/contracts` got wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase

CHANNEL = "transcript-check"


class AChannelMessageIsNeverInvisibleToBothReadersTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        created = self.client.post("/api/v1/channels", json={
            "name": CHANNEL, "description": "fixture", "createdBy": "operator",
        })
        self.assertIn(created.status_code, (200, 201), created.text)

    def _seed_canonical(self, message_id: str, *, recipient) -> None:
        """One canonical row, written with the recipient shape under test."""
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, "
                    "subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
                    (message_id, "operator", recipient, CHANNEL, "channel", "info",
                     f"#{CHANNEL}", f"body {message_id}", 1787000000000),
                )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(go())

    def test_the_transcript_count_agrees_with_the_rows_it_returns(self) -> None:
        """The count and the list are built from the same predicate, and a filter whose count is
        computed by a different rule than its list is the defect `/contracts` had.

        The three rows are the three shapes: NULL (how every canonical row is written today), `''`
        (the shape that used to vanish from every reader) and a fan-out copy (an inbox row, which a
        predicate loose enough to admit `''` must still exclude)."""
        self._seed_canonical("count-a", recipient=None)
        self._seed_canonical("count-b", recipient="")
        self._seed_canonical("count-c", recipient="a-member")
        response = self.client.get(f"/api/v1/channels/{CHANNEL}")
        body = response.json()
        messages = body.get("messages") or body.get("history") or []
        total = body.get("totalMessages", body.get("total"))
        if total is not None:
            self.assertEqual(
                total, len(messages),
                f"the channel reports {total} messages and returns {len(messages)}",
            )
        self.assertEqual({"count-a", "count-b"}, {str(m.get("id")) for m in messages})


if __name__ == "__main__":
    import unittest

    unittest.main()
