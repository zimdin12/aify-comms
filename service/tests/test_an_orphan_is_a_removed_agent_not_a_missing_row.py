r"""`POST /messages/cleanup/orphan-unread` deletes messages to REMOVED agents, and nothing else.

THE DEFECT, measured on the operator's database 2026-08-29. The route is documented as "Delete unread
inbox messages addressed to removed agents" and asked `a.id IS NULL` -- no row in `agents`. That is
true of every message addressed to `dashboard`, which was never an agent and was never removed: it is
the UI's own identity, it holds 1,792 unread messages, and it has SENT 3,401.

    rows the old predicate matched                              1,891
      addressed to `dashboard`, an active participant           1,792   (95%)
      addressed to a recipient with an agent_tombstones row         78
      addressed to a recipient deleted before tombstones existed     21

One call would have deleted the operator's entire dashboard inbox. `/stats` reported
`orphan_unread_messages: 1891` beside it -- a number that invites exactly that call.

A REMOVAL LEAVES A RECORD. `agent_removal.py` writes an `agent_tombstones` row, so "the agent is
gone" is something the schema states rather than something inferred from an absence. Verified against
live data before and after: 1,891 -> 78, and the dashboard's share goes 1,792 -> 0.

ONE PREDICATE, TWO CALLERS. The count and the deletion now read the same constant
(`api_core/orphan_messages.py`). They had drifted in the direction where the number recommends an
action and the action removes something else.
"""
from __future__ import annotations

import asyncio
import time

from service.api_core.orphan_messages import ORPHAN_UNREAD_WHERE
from service.db import get_db
from service.tests._base import FastApiTestCase


class AnOrphanIsARemovedAgentNotAMissingRow(FastApiTestCase):
    def _write(self, query: str, params: tuple = ()) -> None:
        async def run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(run())

    def _message(self, message_id: str, to_agent, *, read: bool = False, source: str = "direct") -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority,"
            " timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (message_id, "sender", to_agent, source, "info", "s", "b", "normal",
             int(time.time() * 1000)),
        )
        if read:
            self._write(
                "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, to_agent, "2026-08-29T00:00:00Z"),
            )

    def _tombstone(self, agent_id: str) -> None:
        self._write(
            "INSERT INTO agent_tombstones (agent_id, removed_at, removed_by, reason)"
            " VALUES (?,?,?,?)",
            (agent_id, "2026-08-29T00:00:00Z", "dashboard", "test"),
        )

    def _remaining(self) -> set[str]:
        async def run():
            db = await get_db()
            try:
                rows = await (await db.execute("SELECT id FROM messages")).fetchall()
                return {str(r["id"]) for r in rows}
            finally:
                await db.close()

        return asyncio.run(run())

    def _cleanup(self) -> int:
        response = self.client.post("/api/v1/messages/cleanup/orphan-unread")
        self.assertEqual(response.status_code, 200, response.text)
        return int(response.json().get("deleted", 0))

    def test_THE_DEFECT_the_dashboard_inbox_survives(self):
        """`dashboard` has no `agents` row because it is not an agent. Under the old predicate that
        made every unread message to it an orphan -- 1,792 of them on the operator's fleet."""
        self._message("to-dashboard-1", "dashboard")
        self._message("to-dashboard-2", "dashboard")
        deleted = self._cleanup()
        self.assertEqual(deleted, 0, "the cleanup deleted messages addressed to the dashboard")
        self.assertEqual(self._remaining(), {"to-dashboard-1", "to-dashboard-2"})

    def test_a_message_to_a_REMOVED_agent_is_still_deleted(self):
        """The case the endpoint exists for. Without this the fix would be "delete nothing", which
        also passes the test above."""
        self._tombstone("gone-agent")
        self._message("to-gone", "gone-agent")
        self.assertEqual(self._cleanup(), 1)
        self.assertEqual(self._remaining(), set())

    def test_a_READ_message_to_a_removed_agent_is_history(self):
        """The third condition, inherited unchanged. A message somebody read is not an orphan."""
        self._tombstone("gone-agent")
        self._message("read-one", "gone-agent", read=True)
        self.assertEqual(self._cleanup(), 0)
        self.assertEqual(self._remaining(), {"read-one"})

    def test_a_CHANNEL_BROADCAST_row_is_not_an_orphan(self):
        """The first condition, inherited unchanged and still load-bearing: `channel_send` writes one
        row with no `to_agent` plus a fan-out row per member. Drop `to_agent IS NOT NULL` and every
        unread broadcast in the database matches."""
        self._message("broadcast", None, source="channel")
        self.assertEqual(self._cleanup(), 0)
        self.assertEqual(self._remaining(), {"broadcast"})

    def test_a_message_to_a_LIVE_agent_is_untouched(self):
        registered = self.client.post("/api/v1/agents", json={
            "agentId": "live-agent", "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "launchMode": "detached",
        })
        self.assertEqual(registered.status_code, 200, registered.text)
        self._message("to-live", "live-agent")
        self.assertEqual(self._cleanup(), 0)
        self.assertEqual(self._remaining(), {"to-live"})

    def test_THE_MIXED_CASE_deletes_only_the_removed_one(self):
        """All four kinds in one database, because each test above passes on its own for a cleanup
        that does nothing at all."""
        self._tombstone("gone-agent")
        self._message("keep-dashboard", "dashboard")
        self._message("keep-broadcast", None, source="channel")
        self._message("keep-read", "gone-agent", read=True)
        self._message("delete-me", "gone-agent")
        self.assertEqual(self._cleanup(), 1)
        self.assertEqual(self._remaining(), {"keep-dashboard", "keep-broadcast", "keep-read"})

    def test_THE_COUNT_AND_THE_DELETION_AGREE(self):
        """They had drifted, and the drift is what made this dangerous: `/stats` said 1,891 while the
        endpoint that number recommends would have removed an inbox. Same fixture, both answers."""
        self._tombstone("gone-agent")
        self._message("keep-dashboard", "dashboard")
        self._message("delete-me", "gone-agent")
        stats = self.client.get("/api/v1/stats")
        self.assertEqual(stats.status_code, 200, stats.text)
        counted = stats.json()["orphan_unread_messages"]
        self.assertEqual(counted, 1, "the stat counts something the cleanup would not delete")
        self.assertEqual(self._cleanup(), counted)

    def test_the_predicate_has_ONE_owner(self):
        """A copy of a predicate in a second file is how these two came to disagree. Both callers
        interpolate the shared constant; the deletion is verified by rendering, not by reading."""
        from service.routers.dispatch_messages.message_removal import _ORPHAN_UNREAD_WHERE

        self.assertIn(ORPHAN_UNREAD_WHERE, " ".join(_ORPHAN_UNREAD_WHERE.split()))
        self.assertIn("agent_tombstones", ORPHAN_UNREAD_WHERE)
        self.assertNotIn("a.id IS NULL", ORPHAN_UNREAD_WHERE, (
            "the absence test is back; it matches every identity that was never an agent"
        ))
