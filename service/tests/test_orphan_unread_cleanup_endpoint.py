"""`POST /messages/cleanup/orphan-unread` — the one route that deletes rows nobody asked it about.

`cleanup_orphan_unread_messages` was among the service functions the suite never entered. Its WHERE
clause has its own tests; the handler had never run, and the handler is what turns that clause into
deletions, a websocket broadcast and a count an operator reads.

WHAT IT IS FOR. Removing an agent deletes its row, but the messages already sitting in its inbox
stay — unread forever, addressed to somebody who no longer exists. They inflate every unread count
that scans the table and they are the only thing keeping a dead conversation alive.

WHAT MUST SURVIVE IT is where the risk lives, and each survivor fails for its own reason:
  * a message to a LIVE agent, however old — this endpoint is not retention;
  * a message the recipient already READ, even if that agent is gone — it is history, not an orphan;
  * a CHANNEL BROADCAST row, which has no `to_agent` at all. Drop that condition and every unread
    broadcast in the database matches, because the "agent is gone" test is trivially true for a row
    with no recipient.

THE COUNT IS THE OPERATOR'S ONLY FEEDBACK. It is a manual hygiene action, so a number that does not
match what was deleted is the same defect the contract-repair endpoint had: work reported that was
never done.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

LIVE = "lc-live"
GONE = "lc-gone"
SENDER = "lc-sender"


class OrphanUnreadCleanupTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (LIVE, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _message_ids(self) -> list[str]:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                cursor = await db.execute("SELECT id FROM messages ORDER BY id")
                return [row[0] for row in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_message(self, message_id: str, *, to_agent, channel: str = "",
                      source: str = "direct") -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, channel, source, subject, body, type,"
            " priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (message_id, SENDER, to_agent, channel, source, "s", "b", "info", "normal", 1700000000),
        )

    def _mark_read(self, message_id: str, agent_id: str) -> None:
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, "2026-08-16T00:00:00Z"),
        )

    def _cleanup(self):
        return self.client.post("/api/v1/messages/cleanup/orphan-unread")

    # ── what it deletes ──────────────────────────────────────────────────────────────────────

    def test_an_unread_message_to_a_REMOVED_agent_is_deleted(self):
        self._seed_message("m-orphan", to_agent=GONE)
        response = self._cleanup()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "deleted": 1})
        self.assertEqual(self._message_ids(), [])

    def test_the_reported_count_matches_what_was_actually_deleted(self):
        """A manual hygiene action's only feedback. A count that does not match is work reported
        that was never done — the same defect the contract-repair endpoint had."""
        for index in range(3):
            self._seed_message(f"m-{index}", to_agent=GONE)
        self._seed_message("m-keep", to_agent=LIVE)
        self.assertEqual(self._cleanup().json()["deleted"], 3)
        self.assertEqual(self._message_ids(), ["m-keep"])

    def test_running_it_twice_deletes_nothing_the_second_time(self):
        self._seed_message("m-orphan", to_agent=GONE)
        self.assertEqual(self._cleanup().json()["deleted"], 1)
        self.assertEqual(self._cleanup().json()["deleted"], 0)

    def test_nothing_to_clean_is_a_success_reporting_zero(self):
        response = self._cleanup()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "deleted": 0})

    # ── what must survive ────────────────────────────────────────────────────────────────────

    def test_a_message_to_a_LIVE_agent_is_never_touched(self):
        """This is not retention. An unread message to an agent that still exists is work waiting to
        be picked up, however long it has been sitting there."""
        self._seed_message("m-live", to_agent=LIVE)
        self.assertEqual(self._cleanup().json()["deleted"], 0)
        self.assertEqual(self._message_ids(), ["m-live"])

    def test_a_message_the_removed_agent_had_READ_is_kept(self):
        """It is history — the conversation happened, and the record of it is what an operator reads
        afterwards to understand what the agent did."""
        self._seed_message("m-read", to_agent=GONE)
        self._mark_read("m-read", GONE)
        self.assertEqual(self._cleanup().json()["deleted"], 0)
        self.assertEqual(self._message_ids(), ["m-read"])

    def test_a_CHANNEL_BROADCAST_row_is_kept(self):
        """THE CONDITION THAT LOOKS REDUNDANT AND IS NOT. A broadcast row has no `to_agent` at all,
        so the "agent is gone" test is trivially true for it — without `to_agent IS NOT NULL` this
        endpoint deletes every unread broadcast in the database."""
        self._seed_message("m-broadcast", to_agent=None, channel="general", source="channel")
        self.assertEqual(self._cleanup().json()["deleted"], 0)
        self.assertEqual(self._message_ids(), ["m-broadcast"])

    def test_a_channel_FAN_OUT_row_for_a_removed_agent_IS_deleted(self):
        """The other half of the same shape: the per-member copy DOES carry a recipient, so a
        removed member's unread copy is as much an orphan as a direct message."""
        self._seed_message("m-broadcast", to_agent=None, channel="general", source="channel")
        self._seed_message("m-fanout", to_agent=GONE, channel="general", source="channel")
        self.assertEqual(self._cleanup().json()["deleted"], 1)
        self.assertEqual(self._message_ids(), ["m-broadcast"])

    def test_a_receipt_from_ANOTHER_agent_does_not_save_an_orphan(self):
        """The read-receipt join is scoped to the message's own recipient. A receipt written by
        somebody else says nothing about whether the addressee ever saw it."""
        self._seed_message("m-orphan", to_agent=GONE)
        self._mark_read("m-orphan", LIVE)
        self.assertEqual(self._cleanup().json()["deleted"], 1)
        self.assertEqual(self._message_ids(), [])

    def test_a_mixed_table_loses_ONLY_the_orphans(self):
        """The whole contract in one pass, because each condition was tested in isolation above and
        a cleanup runs against all of them at once."""
        self._seed_message("m-orphan", to_agent=GONE)
        self._seed_message("m-live", to_agent=LIVE)
        self._seed_message("m-read", to_agent=GONE)
        self._mark_read("m-read", GONE)
        self._seed_message("m-broadcast", to_agent=None, channel="general", source="channel")
        self.assertEqual(self._cleanup().json()["deleted"], 1)
        self.assertEqual(self._message_ids(), ["m-broadcast", "m-live", "m-read"])

    # ── what it tells the dashboard ──────────────────────────────────────────────────────────

    def test_a_cleanup_that_deleted_something_is_broadcast(self):
        """The dashboard's unread counts change under it. Without the event they stay wrong until
        the next poll, which is exactly when an operator is looking at them."""
        self._seed_message("m-orphan", to_agent=GONE)
        self._cleanup()
        events = [args[0] for args, _ in self.ws.broadcasts]
        self.assertIn("messages_cleaned", events)
        payload = next(args[1] for args, _ in self.ws.broadcasts if args[0] == "messages_cleaned")
        self.assertEqual(payload, {"kind": "orphan_unread", "deleted": 1})

    def test_a_cleanup_that_deleted_NOTHING_is_silent(self):
        """An event per no-op would flicker every dashboard on a hygiene run that changed nothing.

        Asserted on THIS event rather than on an empty broadcast list: registering the two agents in
        setUp already emitted `agent_registered` twice, so an empty-list assertion was failing for a
        reason that has nothing to do with the cleanup."""
        before = len(self.ws.broadcasts)
        self._cleanup()
        emitted = [args[0] for args, _ in self.ws.broadcasts[before:]]
        self.assertNotIn("messages_cleaned", emitted)
