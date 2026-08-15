"""Which channel messages a recovering environment still owes, asked of the query itself.

`_select_undelivered_channel_messages` was inline in
`_replay_undelivered_channel_messages_on_env_recovery` until v0.5.4, so exercising it meant running
the reconciler. It is now a leaf and these tests run it against a real sqlite database.

THE TIMESTAMP TEST IS THE POINT OF THIS FILE. `messages.timestamp` is epoch MILLISECONDS, and the
predicate used to call `datetime()` on it directly — which returns NULL, so the comparison was never
true and this reconciler could not match a single row it exists to replay. 0 candidates on the live
database under the old predicate, 115 under `datetime(m.timestamp / 1000, 'unixepoch')`.

The repo records that as the SIXTH lexical/epoch timestamp bug of its kind, and notes that other code
already did the conversion correctly — a copy that drifted, not a misunderstanding. A copy that
drifts is caught by execution and not by review, which is why the conversion gets three tests here:
one that a row inside the window is found, one that a row outside it is not, and one that a
plausible-looking ISO string is NOT a valid timestamp for this column.

A CANDIDATE MUST SATISFY ALL FIVE conditions — from a channel, real recipient, dispatch requested, no
read receipt, no dispatch run — and each has its own test, because any one of them relaxing means a
recovering environment redelivers something the agent already saw.
"""

from __future__ import annotations

import unittest

import aiosqlite

from service.api_core.channel_replay_query import _select_undelivered_channel_messages

SCHEMA = """
CREATE TABLE messages (
    id TEXT PRIMARY KEY, from_agent TEXT, to_agent TEXT, channel TEXT, type TEXT,
    subject TEXT, body TEXT, priority TEXT, source TEXT, dispatch_requested INTEGER,
    timestamp INTEGER
);
CREATE TABLE read_receipts (message_id TEXT, agent_id TEXT, read_at TEXT);
CREATE TABLE dispatch_runs (id TEXT PRIMARY KEY, message_id TEXT);
"""
#: `timestamp INTEGER` is copied from the real schema deliberately: the whole defect is that this
#: column holds epoch milliseconds rather than an ISO string, and a fixture that typed it TEXT would
#: let the broken predicate look fine.

#: Milliseconds, which is what the column actually holds. 2026-08-15T12:00:00Z.
RECENT_MS = 1786968000000
#: 2020-01-01, comfortably outside any window used here.
OLD_MS = 1577836800000

INSIDE_WINDOW = "-100 years"
OUTSIDE_WINDOW = "-0 seconds"


class ChannelReplayQueryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript(SCHEMA)

    async def asyncTearDown(self):
        await self.db.close()

    async def _message(self, mid, *, to_agent="a1", source="channel", dispatch_requested=1,
                       timestamp=RECENT_MS):
        await self.db.execute(
            "INSERT INTO messages VALUES (?,?,?,?,'request','s','b','normal',?,?,?)",
            (mid, "sender", to_agent, "chan", source, dispatch_requested, timestamp))

    async def _select(self, *, cutoff=INSIDE_WINDOW, limit=200):
        rows = await _select_undelivered_channel_messages(self.db, cutoff, limit)
        return [r["id"] for r in rows]

    # ---- the epoch conversion -----------------------------------------------

    async def test_a_message_inside_the_window_is_found(self):
        await self._message("m1", timestamp=RECENT_MS)
        self.assertEqual(["m1"], await self._select(cutoff=INSIDE_WINDOW))

    async def test_a_message_OUTSIDE_the_window_is_not(self):
        """Proves the comparison is live rather than merely never-false."""
        await self._message("m1", timestamp=OLD_MS)
        self.assertEqual([], await self._select(cutoff="-1 hours"))

    async def test_an_ISO_STRING_in_the_timestamp_column_is_not_matched(self):
        """The shape the broken predicate assumed. `timestamp` holds milliseconds; a row that
        somehow carried an ISO string would divide to ~0 and fall outside every real window, which
        is the safe direction — it is not replayed rather than replayed wrongly."""
        await self._message("m1", timestamp="2026-08-15T12:00:00Z")
        self.assertEqual([], await self._select(cutoff="-1 hours"))

    # ---- the five conditions -------------------------------------------------

    async def test_only_CHANNEL_messages_are_replayed(self):
        for source in ("direct", "dispatch", "message_send", ""):
            with self.subTest(source=source):
                await self.db.execute("DELETE FROM messages")
                await self._message("m1", source=source)
                self.assertEqual([], await self._select())

    async def test_a_message_with_no_dispatch_requested_is_not_replayed(self):
        """Nobody asked for it to be delivered, so there is nothing owed."""
        await self._message("m1", dispatch_requested=0)
        self.assertEqual([], await self._select())

    async def test_a_message_with_no_real_recipient_is_not_replayed(self):
        for recipient in (None, "", "dashboard"):
            with self.subTest(recipient=recipient):
                await self.db.execute("DELETE FROM messages")
                await self._message("m1", to_agent=recipient)
                self.assertEqual([], await self._select())

    async def test_a_message_the_recipient_already_READ_is_not_replayed(self):
        await self._message("m1", to_agent="a1")
        await self.db.execute(
            "INSERT INTO read_receipts VALUES ('m1','a1','2026-08-15T12:00:00Z')")
        self.assertEqual([], await self._select())

    async def test_ANOTHER_agents_read_receipt_does_not_count(self):
        """The join is on `rr.agent_id = m.to_agent`; a receipt from someone else is not delivery."""
        await self._message("m1", to_agent="a1")
        await self.db.execute(
            "INSERT INTO read_receipts VALUES ('m1','someone-else','2026-08-15T12:00:00Z')")
        self.assertEqual(["m1"], await self._select())

    async def test_a_message_that_ALREADY_has_a_dispatch_run_is_not_replayed(self):
        """The strongest guard: a run exists, so delivery was already attempted."""
        await self._message("m1")
        await self.db.execute("INSERT INTO dispatch_runs VALUES ('r1','m1')")
        self.assertEqual([], await self._select())

    # ---- shape ---------------------------------------------------------------

    async def test_the_oldest_message_comes_first(self):
        await self._message("m-new", timestamp=RECENT_MS)
        await self._message("m-old", timestamp=RECENT_MS - 60_000)
        self.assertEqual(["m-old", "m-new"], await self._select())

    async def test_the_limit_is_honoured(self):
        for i in range(4):
            await self._message(f"m{i}", timestamp=RECENT_MS + i)
        self.assertEqual(["m0", "m1"], await self._select(limit=2))

    async def test_the_row_carries_what_the_replay_needs_to_resend(self):
        await self._message("m1")
        rows = await _select_undelivered_channel_messages(self.db, INSIDE_WINDOW, 200)
        for column in ("id", "from_agent", "to_agent", "channel", "type", "subject", "body",
                       "priority"):
            self.assertIn(column, rows[0].keys())


if __name__ == "__main__":
    unittest.main()
