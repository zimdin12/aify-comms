r"""`/stats` counts three kinds of unread in ONE pass, and the three answers are unchanged.

WHAT CHANGED. Three queries became one. They differed only in which rows they kept -- unread to a
REGISTERED agent split by source, and unread to an UNREGISTERED one -- and each re-walked `messages`
and re-probed `read_receipts` over the same population.

MEASURED on the operator's database, 2026-08-29: 34,107 messages, 31,913 receipts, 33,928 of them
addressed (33,440 `direct` + 488 `channel`). The three queries drove 33,440 + 488 + 33,928 = 67,856
read_receipts probes per request; the combined one drives 33,928. Exactly half, because the two
source-split populations sum to the third. `/stats` sits on the dashboard poll cycle and logged 2,262
SLOW-REQ warnings in the 8.5 hours to 07:56 that day.

NOT A WALL-CLOCK CLAIM. This host cannot measure one: the operator's live fleet is the load, and the
same code has timed 44-47ms and then 22-25ms minutes apart. Round-trips and join probes are
deterministic and attributable, so they are what the commit claims.

WHAT THIS FILE PROVES, which the count above does not: that the collapse is FAITHFUL. The three
original queries are kept here verbatim as the ORACLE and run beside the endpoint over a fixture
built to separate them -- a read direct message, an unread one, an unread channel message, an unread
message to an agent that no longer exists, and one addressed to nobody. A rewrite that quietly folded
`orphan` into `direct` would pass any test that only asserted "three numbers came back".
"""
from __future__ import annotations

import asyncio
import time

from service.db import get_db
from service.tests._base import FastApiTestCase

# The three queries as they stood before 2026-08-29. Kept verbatim, as the oracle.
BEFORE_DIRECT = """
    SELECT COUNT(*)
    FROM messages m
    JOIN agents a ON a.id = m.to_agent
    LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
    WHERE m.to_agent IS NOT NULL AND m.source = 'direct' AND r.message_id IS NULL
"""
BEFORE_CHANNEL = BEFORE_DIRECT.replace("'direct'", "'channel'")
BEFORE_ORPHAN = """
    SELECT COUNT(*)
    FROM messages m
    LEFT JOIN agents a ON a.id = m.to_agent
    LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
    WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
"""


class ThreeUnreadCountsAgreeAfterOnePass(FastApiTestCase):
    def _sql(self, query: str, params: tuple = ()):
        async def run():
            db = await get_db()
            try:
                cursor = await db.execute(query, params)
                return await cursor.fetchall()
            finally:
                await db.close()

        return asyncio.run(run())

    def _write(self, query: str, params: tuple = ()) -> None:
        async def run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _agent(self, agent_id: str) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _message(self, message_id: str, to_agent, source: str, read: bool = False) -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority,"
            " timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (message_id, "sender", to_agent, source, "info", "s", "b", "normal", int(time.time() * 1000)),
        )
        if read:
            self._write(
                "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, to_agent, "2026-08-29T00:00:00Z"),
            )

    def _fixture(self) -> None:
        """One message per case the three counters are supposed to separate.

        THE THREE EXPECTED COUNTS ARE 1, 2 AND 5, AND THE THIRD IS NOT THE SUM OF THE OTHER TWO.
        Both weaker versions of this fixture were caught by the same mutant -- flipping the orphan
        slot from `a.id IS NULL` to `a.id IS NOT NULL`, which makes it count the registered rows
        instead. At direct=1, channel=1, orphan=2 the mutant scored 1 + 1 = 2 and passed. Making them
        distinct (1, 2, 3) was not enough either: 1 + 2 = 3 and it passed again. Distinctness is not
        the property that matters; no counter being reachable as a combination of the others is.
        """
        self._agent("registered")
        self._message("m-direct-unread", "registered", "direct")
        self._message("m-direct-read", "registered", "direct", read=True)
        self._message("m-channel-unread-a", "registered", "channel")
        self._message("m-channel-unread-b", "registered", "channel")
        self._message("m-channel-read", "registered", "channel", read=True)
        # Addressed to an agent that was never registered (or has been removed): the orphan case.
        self._message("m-orphan-a", "vanished", "direct")
        self._message("m-orphan-b", "vanished", "channel")
        self._message("m-orphan-c", "vanished", "direct")
        self._message("m-orphan-d", "vanished", "channel")
        self._message("m-orphan-e", "vanished", "direct")
        self._message("m-orphan-read", "vanished", "direct", read=True)
        # Addressed to nobody: a channel fanout row. Excluded from all three.
        self._message("m-broadcast", None, "channel")

    def _stats(self) -> dict:
        response = self.client.get("/api/v1/stats")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_THE_ENDPOINT_AGREES_WITH_THE_THREE_QUERIES_IT_REPLACED(self):
        self._fixture()
        oracle = (
            self._sql(BEFORE_DIRECT)[0][0],
            self._sql(BEFORE_CHANNEL)[0][0],
            self._sql(BEFORE_ORPHAN)[0][0],
        )
        stats = self._stats()
        served = (
            stats["unread_messages"],
            stats["channel_unread_messages"],
            stats["orphan_unread_messages"],
        )
        self.assertEqual(served, oracle, (
            "the single-pass form disagrees with the three queries it replaced; the fixture separates "
            "read from unread, direct from channel, and registered from orphaned recipients"
        ))

    def test_the_fixture_actually_separates_the_three(self):
        """POSITIVE CONTROL. Three zeroes agree with three zeroes, and an agreement between empty
        answers is not evidence of anything. Every counter must be non-zero and they must differ."""
        self._fixture()
        stats = self._stats()
        for key in ("unread_messages", "channel_unread_messages", "orphan_unread_messages"):
            self.assertGreater(stats[key], 0, f"{key} is zero, so this fixture proves nothing about it")
        self.assertEqual(stats["unread_messages"], 1)
        self.assertEqual(stats["channel_unread_messages"], 2)
        self.assertEqual(stats["orphan_unread_messages"], 5)
        self.assertEqual(len({stats["unread_messages"], stats["channel_unread_messages"],
                              stats["orphan_unread_messages"]}), 3,
                         "two counters are equal, so a fold of one into the other would go unnoticed")

    def test_a_read_message_is_counted_by_none_of_them(self):
        """The `r.message_id IS NULL` half. Without it all three counters would be totals, and the
        fixture above would still pass every equality if the oracle had lost the same clause."""
        self._agent("registered")
        self._message("only-read", "registered", "direct", read=True)
        stats = self._stats()
        self.assertEqual(stats["unread_messages"], 0)
        self.assertEqual(stats["channel_unread_messages"], 0)
        self.assertEqual(stats["orphan_unread_messages"], 0)

    def test_a_message_addressed_to_nobody_is_counted_by_none_of_them(self):
        """`to_agent IS NULL` is the channel fanout row. It has no reader, so it cannot be unread by
        one -- and it is the row an `a.id IS NULL` test would wrongly claim as an orphan."""
        self._message("broadcast-only", None, "channel")
        stats = self._stats()
        self.assertEqual(stats["orphan_unread_messages"], 0)
        self.assertEqual(stats["unread_messages"], 0)
        self.assertEqual(stats["channel_unread_messages"], 0)

    def test_an_empty_database_answers_zero_rather_than_null(self):
        """SUM over no rows is NULL where COUNT(*) is 0. Left unguarded, a fresh install would put
        `null` into three dashboard counters -- a regression the shape this change is in could
        introduce and no other test here would see."""
        stats = self._stats()
        for key in ("unread_messages", "channel_unread_messages", "orphan_unread_messages"):
            self.assertEqual(stats[key], 0, f"{key} came back {stats[key]!r} on an empty database")
            self.assertIsInstance(stats[key], int)
