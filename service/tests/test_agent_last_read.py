"""`GET /agents/{id}/last-read` — the endpoint that answers "have they seen it yet?".

One of 71 service functions the suite never ENTERED, which is a finer floor than the route gate:
that one requires a test to name the path, and a docstring mentioning it counts. This handler had
never been called.

WHAT IT IS FOR. An agent that sent something and got no answer wants to know whether the recipient
has read anything since — it is the difference between "still working" and "never saw it". So the
two failure modes are: reporting a read that did not happen, and reporting the WRONG one, because
"the last thing they read" is only useful if it is actually the last.

THE ORDERING IS A STRING SORT on `read_at`, which is correct exactly while every writer uses the
same ISO-8601 shape — the lexical-timestamp class this repo has been bitten by six times. The test
drives it with the format the service itself writes, and with a same-instant pair, so the query's
behaviour is pinned rather than assumed.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

READER = "lc-reader"
SENDER = "lc-sender"


class AgentLastReadTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (READER, SENDER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    def _send(self, subject: str, body: str = "hello") -> str:
        response = self.client.post(
            "/api/v1/messages/send",
            json={"from_agent": SENDER, "to": READER, "subject": subject, "body": body},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messageId"]

    def _mark_read(self, message_id: str, read_at: str = "") -> None:
        response = self.client.post(
            f"/api/v1/messages/{message_id}/read", json={"agentId": READER},
        )
        self.assertEqual(response.status_code, 200, response.text)
        if read_at:
            self._write(
                "UPDATE read_receipts SET read_at = ? WHERE message_id = ? AND agent_id = ?",
                (read_at, message_id, READER),
            )

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _last_read(self, agent_id: str = READER):
        response = self.client.get(f"/api/v1/agents/{agent_id}/last-read")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ── nothing read yet ─────────────────────────────────────────────────────────────────────

    def test_an_agent_that_has_read_nothing_answers_null_rather_than_an_error(self):
        """The common case for a fresh agent, and the caller is asking a question, not making a
        request that can fail. A 404 here would read as "no such agent"."""
        self.assertEqual(self._last_read(), {"agentId": READER, "lastRead": None})

    def test_an_agent_that_does_not_exist_also_answers_null(self):
        """Observed, and worth pinning either way: the endpoint reads RECEIPTS, not agents, so it
        cannot tell an unknown agent from one that has read nothing. A caller needing that
        distinction has `GET /agents/{id}`, which does answer it."""
        self.assertEqual(
            self._last_read("lc-never-existed"),
            {"agentId": "lc-never-existed", "lastRead": None},
        )

    def test_an_UNREAD_message_does_not_count_as_read(self):
        self._send("subject one")
        self.assertIsNone(self._last_read()["lastRead"])

    # ── what a read looks like ───────────────────────────────────────────────────────────────

    def test_a_read_message_comes_back_with_the_fields_a_sender_needs(self):
        message_id = self._send("please review")
        self._mark_read(message_id)
        last = self._last_read()["lastRead"]
        self.assertEqual(last["messageId"], message_id)
        self.assertEqual(last["from"], SENDER, "the sender identifies WHICH conversation was read")
        self.assertEqual(last["subject"], "please review")
        self.assertTrue(last["readAt"], "without a time, 'last read' cannot be compared to anything")

    def test_the_MOST_RECENT_read_wins(self):
        """The whole value of the endpoint. Reporting an older receipt tells a waiting sender their
        message is unread when it is not — or the reverse."""
        first = self._send("first")
        second = self._send("second")
        self._mark_read(first, read_at="2026-08-16T10:00:00Z")
        self._mark_read(second, read_at="2026-08-16T11:00:00Z")
        self.assertEqual(self._last_read()["lastRead"]["subject"], "second")

    def test_the_order_is_by_READ_TIME_not_by_message_order(self):
        """Reading an OLD message last is the interesting case: an agent catching up on a backlog
        has most recently read the oldest message, and that is the honest answer."""
        first = self._send("first")
        second = self._send("second")
        self._mark_read(second, read_at="2026-08-16T10:00:00Z")
        self._mark_read(first, read_at="2026-08-16T11:00:00Z")
        self.assertEqual(self._last_read()["lastRead"]["subject"], "first")

    def test_receipts_belonging_to_ANOTHER_agent_are_not_reported(self):
        """The query is scoped by agent. Without that, one agent's reading would answer for
        everyone — and the endpoint exists precisely to ask about one recipient."""
        mine = self._send("mine")
        theirs = self._send("theirs")
        self._mark_read(mine, read_at="2026-08-16T10:00:00Z")
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (theirs, SENDER, "2026-08-16T23:00:00Z"),
        )
        self.assertEqual(self._last_read()["lastRead"]["subject"], "mine")

    def test_two_receipts_at_the_SAME_instant_still_answer_with_one_of_them(self):
        """A tie is possible — the service stamps to the second — and the endpoint must not return
        two rows or none. Which one wins is not specified, so it is not asserted."""
        first = self._send("first")
        second = self._send("second")
        self._mark_read(first, read_at="2026-08-16T10:00:00Z")
        self._mark_read(second, read_at="2026-08-16T10:00:00Z")
        last = self._last_read()["lastRead"]
        self.assertIn(last["subject"], {"first", "second"})
        self.assertTrue(last["messageId"])
