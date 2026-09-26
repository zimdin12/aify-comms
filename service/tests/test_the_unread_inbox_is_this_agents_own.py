"""An agent's unread messages are the ones IT has no receipt for: `GET /messages/inbox?filter=unread`.

UNREAD IS THE ABSENCE OF A RECEIPT -- `LEFT JOIN read_receipts ... WHERE r.message_id IS NULL` -- and
the join is scoped to THIS agent. Both halves fail differently: an unscoped join hides a message
because somebody ELSE read it, and a missing null-check hands back everything the agent has already
handled.

These two properties were pinned on the agent long-poll until it was removed in 0.7.5 (DECISIONS.md).
The inbox computes unread with the same join and is now the one surface an agent reads it from, so
they are pinned here instead. Every read peeks, so the reads themselves write no receipts.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "ui-reader"
SENDER = "ui-sender"
OTHER = "ui-other"


class TheUnreadInboxIsThisAgentsOwnTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, SENDER, OTHER):
            response = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
            self.assertEqual(response.status_code, 200, response.text)

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _seed_message(self, message_id: str, *, timestamp: int = 1700000000) -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body, type, priority, timestamp)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (message_id, SENDER, AGENT, "s", "b", "request", "normal", timestamp),
        )

    def _receipt(self, message_id: str, agent_id: str) -> None:
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, "2026-09-27T00:00:00Z"),
        )

    def _unread(self) -> dict:
        response = self.client.get(f"/api/v1/messages/inbox/{AGENT}", params={"filter": "unread", "peek": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_message_ANOTHER_agent_has_read_is_still_unread_for_this_one(self):
        """Unscoped, one agent's receipt would hide the message from everyone else -- silently,
        because unread is an ABSENCE."""
        self._seed_message("m-1")
        self._receipt("m-1", OTHER)
        payload = self._unread()
        self.assertEqual([m["id"] for m in payload["messages"]], ["m-1"],
                         "another agent's receipt hid this agent's message")
        # CONTROL: this agent's own receipt does hide it, so the read above could have said no.
        self._receipt("m-1", AGENT)
        self.assertEqual(self._unread()["total"], 0, "this agent's own receipt did not hide the message")

    def test_a_MIXED_inbox_returns_only_the_unread_one(self):
        """The count and the page both carry the `IS NULL` clause, and only a mixed inbox tells a
        page that lost it from one that kept it."""
        self._seed_message("m-read")
        self._seed_message("m-fresh", timestamp=1700000009)
        self._receipt("m-read", AGENT)
        payload = self._unread()
        self.assertEqual([m["id"] for m in payload["messages"]], ["m-fresh"])
        self.assertEqual(payload["total"], 1, "an already-read message was counted as unread")
