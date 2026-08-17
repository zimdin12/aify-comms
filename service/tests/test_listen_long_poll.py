"""`GET /agents/{id}/listen` — the long poll an agent parks on while it waits for work.

It was among the 71 service functions the suite never entered, and it is the one endpoint whose
failure mode is silence: a wrong answer here is an agent that sleeps through work delivered to it,
which looks exactly like an agent with nothing to do.

UNREAD IS THE ABSENCE OF A RECEIPT — `LEFT JOIN read_receipts ... WHERE r.message_id IS NULL` — and
the join is scoped to THIS agent. Both halves matter and fail differently: an unscoped join hides a
message because somebody ELSE read it, and a missing null-check re-delivers everything the agent has
already handled on every poll.

RETURNING A MESSAGE MARKS IT READ, in the same transaction. That is what stops the next poll
returning it again, and it is why the delivery and the receipt cannot be separated: a return without
a receipt is an infinite redelivery loop, and a receipt without a return is a message nobody ever
sees.

THE TIMEOUTS ARE KEPT SHORT HERE. The endpoint's own default is 300s; every test passes `timeout=1`
so the deadline path is exercised in a second rather than five minutes, and the wake path is
asserted by the message being returned rather than by measuring elapsed time — a timing assertion in
a suite is a flake.
"""

from __future__ import annotations

import asyncio

import aiosqlite

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

AGENT = "lc-listener"
SENDER = "lc-sender"
OTHER = "lc-other"


class ListenLongPollTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (AGENT, SENDER, OTHER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        # The waiter registry is process-global; a stale event from another test would let this
        # one's poll return for a wake that was never meant for it.
        from service.routers.agents.shared import _borrowed_listen_events

        _borrowed_listen_events().pop(AGENT, None)

    # ── seeding ──────────────────────────────────────────────────────────────────────────────

    def _write(self, sql: str, params: tuple = ()) -> None:
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(sql, params)
                await db.commit()

        asyncio.run(run())

    def _rows(self, sql: str, params: tuple = ()):
        async def run():
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(sql, params)
                return [dict(r) for r in await cursor.fetchall()]

        return asyncio.run(run())

    def _seed_message(self, message_id: str, *, to_agent: str = AGENT, timestamp: int = 1700000000,
                      in_reply_to: str = "", subject: str = "s", body: str = "b") -> None:
        self._write(
            "INSERT INTO messages (id, from_agent, to_agent, subject, body, type, priority,"
            " in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (message_id, SENDER, to_agent, subject, body, "request", "normal",
             in_reply_to or None, timestamp),
        )

    def _listen(self, agent_id: str = AGENT, timeout: int = 1):
        return self.client.get(f"/api/v1/agents/{agent_id}/listen", params={"timeout": timeout})

    # ── delivery ─────────────────────────────────────────────────────────────────────────────

    def test_an_unread_message_is_returned_immediately(self):
        self._seed_message("m-1", subject="please review", body="the branch is ready")
        response = self._listen()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        message = payload["messages"][0]
        self.assertEqual(message["id"], "m-1")
        self.assertEqual(message["from"], SENDER, "without the sender the agent cannot reply")
        self.assertEqual(message["subject"], "please review")
        self.assertEqual(message["body"], "the branch is ready")

    def test_returning_a_message_MARKS_it_read_so_it_is_not_redelivered(self):
        """The delivery and the receipt are one act. A return without a receipt is an infinite
        redelivery loop — the agent handles the same request on every poll."""
        self._seed_message("m-1")
        self.assertEqual(self._listen().json()["total"], 1)
        self.assertEqual(self._rows("SELECT agent_id FROM read_receipts")[0]["agent_id"], AGENT)
        self.assertEqual(self._listen().json()["total"], 0, "the message was delivered twice")

    def test_several_messages_arrive_NEWEST_first(self):
        for index, timestamp in enumerate([1700000001, 1700000003, 1700000002]):
            self._seed_message(f"m-{index}", timestamp=timestamp)
        ids = [m["id"] for m in self._listen().json()["messages"]]
        self.assertEqual(ids, ["m-1", "m-2", "m-0"])

    def test_a_reply_carries_its_PARENT_context(self):
        """An agent woken with a bare reply has no idea what it is a reply TO — the parent is often
        hours and several turns back in a conversation it does not hold in memory."""
        self._seed_message("m-parent", subject="the question", body="x" * 200)
        self._seed_message("m-reply", in_reply_to="m-parent", timestamp=1700000005)
        message = next(m for m in self._listen().json()["messages"] if m["id"] == "m-reply")
        self.assertEqual(message["parentContext"]["from"], SENDER)
        self.assertEqual(message["parentContext"]["subject"], "the question")
        self.assertEqual(len(message["parentContext"]["preview"]), 100,
                         "the preview is a PREVIEW — inlining a whole parent body floods the wake")

    def test_a_reply_whose_parent_is_GONE_is_still_delivered(self):
        """Rotation expires old messages while replies to them survive. Dropping the reply because
        its parent expired loses the answer an agent is waiting for."""
        self._seed_message("m-reply", in_reply_to="m-vanished")
        message = self._listen().json()["messages"][0]
        self.assertEqual(message["id"], "m-reply")
        self.assertNotIn("parentContext", message)

    # ── whose messages ───────────────────────────────────────────────────────────────────────

    def test_another_agents_message_is_not_delivered(self):
        self._seed_message("m-theirs", to_agent=OTHER)
        self.assertEqual(self._listen().json()["total"], 0)
        self.assertEqual(self._rows("SELECT * FROM read_receipts"), [],
                         "listening marked someone else's message read")

    def test_a_message_ANOTHER_agent_has_read_is_still_unread_for_this_one(self):
        """The join is scoped by agent. Unscoped, one agent reading a channel message would hide it
        from everyone else — silently, because unread is an ABSENCE."""
        self._seed_message("m-1")
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("m-1", OTHER, "2026-08-16T00:00:00Z"),
        )
        self.assertEqual(self._listen().json()["total"], 1,
                         "another agent's receipt hid this agent's message")

    def test_a_MIXED_inbox_delivers_only_the_unread_one(self):
        """The count gates entry and the fetch selects — both carry the `IS NULL` clause, and only a
        mixed inbox reaches the second one. With every message read the loop never enters the fetch
        at all, so a fetch that had lost its filter looked correct."""
        self._seed_message("m-read")
        self._seed_message("m-fresh", timestamp=1700000009)
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("m-read", AGENT, "2026-08-16T00:00:00Z"),
        )
        payload = self._listen().json()
        self.assertEqual([m["id"] for m in payload["messages"]], ["m-fresh"])
        self.assertEqual(payload["total"], 1, "an already-read message was delivered again")

    def test_a_message_THIS_agent_has_read_is_not_redelivered(self):
        self._seed_message("m-1")
        self._write(
            "INSERT INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            ("m-1", AGENT, "2026-08-16T00:00:00Z"),
        )
        self.assertEqual(self._listen().json()["total"], 0)

    # ── the wait ─────────────────────────────────────────────────────────────────────────────

    def test_with_nothing_to_do_it_waits_and_then_answers_EMPTY(self):
        """The deadline path. Returning an error or hanging past it would make a polling agent look
        broken; an empty answer is what tells the bridge to poll again."""
        response = self._listen(timeout=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"total": 0, "messages": []})

    def test_the_timeout_is_bounded_by_the_endpoint_itself(self):
        """An unbounded hold is a connection an agent cannot get back. The bounds are part of the
        contract, so they are refused rather than clamped silently."""
        for timeout in (0, -1, 601, 100000):
            with self.subTest(timeout=timeout):
                self.assertEqual(self._listen(timeout=timeout).status_code, 422)

    def test_an_invalid_agent_id_is_refused_before_anything_is_written(self):
        response = self.client.get("/api/v1/agents/bad name/listen", params={"timeout": 1})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid agent ID", response.json()["detail"])

    # ── the status it leaves behind ──────────────────────────────────────────────────────────

    def test_parking_on_the_poll_marks_the_agent_IDLE(self):
        """It is the agent saying "I am waiting for work". Leaving it `working` while it sleeps is
        what makes a fleet look busy when it is not.

        THE AGENT IS PUT IN ANOTHER STATUS FIRST, and that is not decoration: registration already
        leaves an agent `idle`, so a version of this test that just registered and polled passed
        even with the status write removed entirely. Verified by mutation.
        """
        self._write("UPDATE agents SET status = 'working' WHERE id = ?", (AGENT,))
        self._listen(timeout=1)
        self.assertEqual(
            self._rows("SELECT status FROM agents WHERE id = ?", (AGENT,))[0]["status"], "idle",
        )

    def test_being_HANDED_work_marks_the_agent_working(self):
        self._seed_message("m-1")
        self._listen()
        self.assertEqual(
            self._rows("SELECT status FROM agents WHERE id = ?", (AGENT,))[0]["status"], "working",
        )

    def test_listening_refreshes_last_seen_even_with_no_work(self):
        """The poll is also a liveness signal: an agent parked here for five minutes is alive, and
        a stale `last_seen` would age it to offline while it waits."""
        self._write("UPDATE agents SET last_seen = ? WHERE id = ?", ("2020-01-01T00:00:00Z", AGENT))
        self._listen(timeout=1)
        last_seen = self._rows("SELECT last_seen FROM agents WHERE id = ?", (AGENT,))[0]["last_seen"]
        self.assertNotEqual(last_seen, "2020-01-01T00:00:00Z")

    def test_listening_as_an_unknown_agent_is_harmless(self):
        """It writes to no rows and answers empty. A 500 here would make a typo in a wrapper look
        like a service outage."""
        response = self._listen(agent_id="lc-never-existed", timeout=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["total"], 0)
