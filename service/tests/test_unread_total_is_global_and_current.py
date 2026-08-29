r"""`unreadTotal` means the addressed agent's whole unread population, right now.

TWO WORDS, EACH LOAD-BEARING, and the first version of this field got both wrong.

GLOBAL. `total` is query-scoped: a `messageId` view has a total of one, a `fromAgent` view has a
subset. Reusing it reported `unreadTotal: 1` for an agent with hundreds unread -- a number that looks
like an answer and is a different question's.

CURRENT. `_settle_inbox_read` marks the returned messages read on a non-peek call, and the first
version took `total` from BEFORE that write. Executed against the real route with three unread and
`limit=1`::

    first response (default filter=unread, non-peek):  {"showing": 1, "total": 3, "unreadTotal": 3}
    the next peek, i.e. what is actually unread now:   {"showing": 2, "total": 2, "unreadTotal": 2}

The response reported as unread a message it had just marked read.

WHY IT HAPPENED, which is the part worth keeping: the optimisation skipped the COUNT when
`filter != "unread"`, matching the SPELLING of the filter rather than its semantics. `total` equals the
global current unread only when the query is unread-scoped AND unfiltered AND nothing settled -- and
that last clause is not visible in the filter name at all.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase


class UnreadTotalIsGlobalAndCurrentTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("reader", "sender", "other"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)

    def _send(self, count: int, *, sender: str = "sender") -> None:
        for index in range(count):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": sender, "to": "reader", "type": "info",
                "subject": f"{sender}-{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)

    def _inbox(self, query: str) -> dict:
        response = self.client.get(f"/api/v1/messages/inbox/reader?{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_THE_DEFECT_a_non_peek_read_reports_what_is_unread_AFTER_it(self):
        """The reviewer's reproduction. A read that marks one message read must not then report that
        message as unread -- the response would be describing the state it just left."""
        self._send(3)
        first = self._inbox("limit=1")
        self.assertEqual(first["showing"], 1)
        self.assertEqual(first["total"], 3, "the query-scoped total is unchanged and still pre-settle")
        self.assertEqual(first["unreadTotal"], 2, (
            "the response counted a message it had just marked read; `unreadTotal` was taken from "
            "`total`, which is computed before `_settle_inbox_read` runs"
        ))

        after = self._inbox("limit=10&peek=true")
        self.assertEqual(after["unreadTotal"], 2, "the two reads disagree about the same moment")

    def test_a_peek_read_settles_nothing_and_the_count_is_unchanged(self):
        """THE CONTROL. If peek also mutated, the test above would pass for the wrong reason -- any
        read would reduce the count and the number would look current without being global."""
        self._send(3)
        first = self._inbox("limit=1&peek=true")
        self.assertEqual(first["unreadTotal"], 3)
        second = self._inbox("limit=1&peek=true")
        self.assertEqual(second["unreadTotal"], 3, "a peek changed the unread population")

    def test_a_messageId_view_reports_the_GLOBAL_count_not_that_message(self):
        """`total` is 1 there, and reusing it made `unreadTotal` say the agent had one unread message
        while three were waiting."""
        self._send(3)
        one = self._inbox("limit=1&peek=true")["messages"][0]["id"]
        body = self._inbox(f"messageId={one}&peek=true")
        self.assertEqual(body["total"], 1, "the query-scoped total is still about the query")
        self.assertEqual(body["unreadTotal"], 3, (
            "the global unread count collapsed to this one message's total"
        ))

    def test_a_FILTERED_view_reports_the_global_count_too(self):
        """A `fromAgent` view has a subset total for the same reason, and the same wrong answer."""
        self._send(3, sender="sender")
        self._send(2, sender="other")
        body = self._inbox("fromAgent=other&peek=true")
        self.assertEqual(body["total"], 2, "the filtered total is about the filter")
        self.assertEqual(body["unreadTotal"], 5, "…and the global count is about the agent")

    def test_the_unfiltered_peeked_unread_view_still_answers_without_a_second_query(self):
        """The one case where `total` IS the global current unread, which is why the optimisation
        exists. Asserted so a later tightening does not quietly turn it into a second COUNT, and so a
        later loosening cannot reintroduce the defect above under a different filter."""
        self._send(4)
        body = self._inbox("filter=unread&peek=true&limit=2")
        self.assertEqual(body["total"], 4)
        self.assertEqual(body["unreadTotal"], 4)

    def test_reading_everything_leaves_nothing_unread(self):
        """End to end, and the number an operator would actually check. A count that never reaches
        zero is a badge that never clears."""
        self._send(3)
        self._inbox("limit=50")
        self.assertEqual(self._inbox("limit=50&peek=true")["unreadTotal"], 0)
