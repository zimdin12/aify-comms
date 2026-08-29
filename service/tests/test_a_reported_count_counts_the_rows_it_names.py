r"""A count in a response is about the rows the response names, and comes from the same read.

TWO PLACES WHERE IT DID NOT, both fixed in the change that adds this file, and both invisible from
the outside because a wrong count looks exactly like a right one.

`GET /channels` produced `memberCount` with its own `SELECT COUNT(*) FROM channel_members` and then,
two statements later, listed the same rows for `members`. Separate reads on one connection with no
transaction around them: a join landing between them makes the card say "N members" beside a list of
N-1. `len()` is the same number for free.

`GET /messages/inbox/{agent}` produced `total` by string-replacing the SELECT clause out of the row
query and truncating at `rfind("LIMIT")`. It worked -- all four query variants started with one of
the two literals it looked for -- and adding one column to any of them makes both replaces no-op,
leaves the parameter count agreeing, and returns `m.id` as `total` with no error raised anywhere.
Reproduced in a scratch database before the fix, not argued from reading.

WHAT THIS FILE ADDS, rather than restating. `total` is already asserted through the real route by
test_unread_total_is_global_and_current.py, across the default, `messageId` and `fromAgent` views.
The `filter=read` branch is exercised by NOTHING, and it is one of the four the composition rewrote.
`memberCount` is asserted nowhere on the service side at all -- only the dashboard suite, from
fixtures that hand-set both fields and so can never disagree.
"""
from __future__ import annotations

from service.tests._base import FastApiTestCase


class AReportedCountCountsTheRowsItNamesTests(FastApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        for agent_id in ("reader", "sender"):
            registered = self.client.post("/api/v1/agents", json={
                "agentId": agent_id, "role": "coder", "runtime": "claude-code",
                "sessionMode": "resident",
            })
            self.assertEqual(registered.status_code, 200, registered.text)

    # -- channels -------------------------------------------------------------------------------

    def _channel(self, name: str, members: "list[str]") -> None:
        created = self.client.post("/api/v1/channels", json={
            "name": name, "description": "", "createdBy": "sender",
        })
        self.assertEqual(created.status_code, 200, created.text)
        for agent_id in members:
            joined = self.client.post(f"/api/v1/channels/{name}/join", json={"agentId": agent_id})
            self.assertEqual(joined.status_code, 200, joined.text)

    def _channels(self) -> "dict[str, dict]":
        response = self.client.get("/api/v1/channels")
        self.assertEqual(response.status_code, 200, response.text)
        return {c["name"]: c for c in response.json()["channels"]}

    def test_member_count_is_the_length_of_the_member_list(self):
        self._channel("crowded", ["reader", "sender"])
        listed = self._channels()
        self.assertIn("crowded", listed)
        for name, channel in listed.items():
            self.assertEqual(
                channel["memberCount"], len(channel["members"]),
                f"#{name} reports a member count that disagrees with the members it lists",
            )

    def test_the_smallest_channel_the_api_can_make_still_agrees(self):
        """`POST /channels` joins the creator in the same transaction, so a member-less channel is
        not reachable through the API and the floor is one. Asserting zero here failed, and the
        product is right: a channel its creator cannot read would be the defect."""
        self._channel("quiet", [])
        channel = self._channels()["quiet"]
        self.assertEqual(channel["members"], ["sender"])
        self.assertEqual(channel["memberCount"], 1)

    def test_the_member_count_moves_when_somebody_joins(self):
        """A count wired to a constant would pass every assertion above. Drive the join and watch
        both fields move together."""
        self._channel("growing", [])
        self.assertEqual(self._channels()["growing"]["memberCount"], 1)
        joined = self.client.post("/api/v1/channels/growing/join", json={"agentId": "reader"})
        self.assertEqual(joined.status_code, 200, joined.text)
        after = self._channels()["growing"]
        self.assertEqual(sorted(after["members"]), ["reader", "sender"])
        self.assertEqual(after["memberCount"], 2)

    # -- inbox ----------------------------------------------------------------------------------

    def _send(self, count: int) -> None:
        for index in range(count):
            sent = self.client.post("/api/v1/messages/send", json={
                "from_agent": "sender", "to": "reader", "type": "info",
                "subject": f"s-{index}", "body": "b",
            })
            self.assertEqual(sent.status_code, 200, sent.text)

    def _inbox(self, query: str) -> dict:
        response = self.client.get(f"/api/v1/messages/inbox/reader?{query}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_read_filter_totals_the_read_messages(self):
        """The one branch of the four that no test drove. A non-peek read settles what it returns,
        so reading two of three leaves exactly two in `filter=read`."""
        self._send(3)
        self.assertEqual(self._inbox("limit=2")["showing"], 2)
        read_view = self._inbox("filter=read")
        self.assertEqual(read_view["showing"], 2)
        self.assertIsInstance(read_view["total"], int,
                              "`total` is a count, and a carved-out count query returns a message id")
        self.assertEqual(read_view["total"], 2)

    def test_the_total_counts_past_the_page_it_returns(self):
        """`total` is the un-limited count, so it must NOT equal `showing` when the page is capped.
        A `total` taken from the page would pass every test that reads only one of them."""
        self._send(5)
        page = self._inbox("limit=2&peek=1")
        self.assertEqual(page["showing"], 2)
        self.assertEqual(page["total"], 5)
