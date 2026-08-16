"""Marking a channel read, and the membership check that decides who may.

`POST /channels/{name}/read` writes a read receipt for every channel message addressed to the caller.
Its two refusals were among the operator-facing 4xx messages in this service that nothing had ever
exercised — and this endpoint is the only one in the channel router that takes an agent id from a RAW
JSON BODY rather than a validated model, so its gate is hand-rolled.

WHY THE MEMBERSHIP CHECK MATTERS MORE THAN IT LOOKS. Unread is computed as the ABSENCE of a receipt,
so writing receipts for an agent SUPPRESSES those messages from that agent's `comms_listen`. Without
the membership check, one agent could mark another's channel mail read — the target simply stops
being told about messages it never saw. That is a silencing primitive, not a bookkeeping one, which
is why the refusal is a 403 rather than a shrug.

THE VALIDATION ORDER IS THE INTERESTING PART, and it is asserted rather than assumed:

    validate_name(name)        <- the CHANNEL name, before the body is even read
    body.get("agentId")        <- 400 when absent
    validate_name(agent_id)    <- the same hostile-name gate every id passes
    membership                 <- 403

So a hostile channel name is refused before an unknown agent id is, and both name checks are the
shared `validate_name` rather than a second, laxer copy — the thing `routers/shared.py`'s docstring
warns about: "This module should keep calling it and must never grow a second, laxer path."

NOT ASSERTED HERE: that a non-member cannot READ the channel. This endpoint only writes receipts;
`channel_read` is a different route with its own viewer check.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase


class ChannelReadMembershipGateTests(FastApiTestCase):
    def _register(self, agent_id: str) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={"agentId": agent_id, "role": "coder", "runtime": "codex"},
        )
        self.assertIn(response.status_code, (200, 201), response.text)

    def _channel(self, name: str, *, members: tuple[str, ...] = ()) -> None:
        response = self.client.post(
            "/api/v1/channels", json={"name": name, "createdBy": "creator", "description": ""},
        )
        self.assertIn(response.status_code, (200, 201), response.text)
        for member in members:
            joined = self.client.post(f"/api/v1/channels/{name}/join", json={"agentId": member})
            self.assertEqual(joined.status_code, 200, joined.text)

    def _read(self, channel: str, body: dict):
        return self.client.post(f"/api/v1/channels/{channel}/read", json=body)

    def setUp(self):
        super().setUp()
        self._register("creator")
        self._register("member-agent")
        self._register("outsider-agent")
        self._channel("general", members=("member-agent",))

    def test_a_member_may_mark_the_channel_read(self):
        """The other direction first: without it, every refusal test below would pass on an endpoint
        that refuses everyone."""
        response = self._read("general", {"agentId": "member-agent"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["channel"], "general")
        self.assertEqual(payload["agentId"], "member-agent")
        self.assertEqual(payload["read"], 0, "no channel mail yet, so nothing to receipt")

    def test_a_NON_MEMBER_is_refused_with_403(self):
        """The silencing primitive this gate exists to stop: unread is the ABSENCE of a receipt, so
        writing receipts for another agent removes those messages from its `comms_listen`."""
        response = self._read("general", {"agentId": "outsider-agent"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], 'Agent "outsider-agent" is not a member of #general')

    def test_a_missing_agentId_is_400_and_says_which_field(self):
        for body in ({}, {"agentId": ""}, {"agentId": "   "}):
            with self.subTest(body=body):
                response = self._read("general", body)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Need agentId")

    def test_a_hostile_agent_id_is_refused_by_the_SHARED_name_gate(self):
        """Not a second, laxer copy. `routers/shared.py` states the rule for this exact pattern:
        "This module should keep calling it and must never grow a second, laxer path." The body is
        raw JSON here, so nothing but that call stands between a path-traversal id and the query."""
        for hostile in ("../etc", "a/b", "a b", "a;rm", "a\nb", ".hidden"):
            with self.subTest(agent_id=hostile):
                response = self._read("general", {"agentId": hostile})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("Invalid agent ID", response.json()["detail"])

        # SURROUNDING whitespace is a different case: the handler `.strip()`s before validating, so
        # `"member-agent\n"` reaches the gate as a valid id rather than being refused. I expected a
        # 400 and the code corrected me — worth pinning, because it means the strip decides what
        # `validate_name` ever sees, and a newline in the MIDDLE (above) is still refused.
        stripped = self._read("general", {"agentId": "member-agent\n"})
        self.assertEqual(stripped.status_code, 200, stripped.text)

    def test_a_hostile_CHANNEL_name_is_refused_before_the_body_is_read(self):
        """Order, pinned: the channel name is validated first, so a hostile name plus a missing
        agentId answers the NAME refusal — not "Need agentId"."""
        # NOT `../etc`: an HTTP client collapses `..` in a URL path before sending, so that never
        # reaches the route at all and the test would be measuring the client. A space survives
        # percent-encoding and arrives as the handler sees it.
        response = self._read("bad name", {})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("Invalid channel name", response.json()["detail"])

    def test_an_unknown_channel_refuses_as_a_non_member_rather_than_404(self):
        """Current behaviour, pinned because it is a REACHABLE difference rather than a detail: this
        endpoint has no channel-existence check, so a member of nothing gets the membership refusal.
        `join` answers 404 for the same missing channel, so the two are worth being able to compare.
        """
        response = self._read("no-such-channel", {"agentId": "member-agent"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("is not a member of #no-such-channel", response.json()["detail"])

        joined = self.client.post("/api/v1/channels/no-such-channel/join", json={"agentId": "member-agent"})
        self.assertEqual(joined.status_code, 404, "join DOES check the channel exists")

    def test_membership_is_per_channel(self):
        """A member of one channel is an outsider to the next — the check keys on the pair, and a
        query that dropped the channel from its WHERE would pass every test above."""
        self._channel("secret", members=("outsider-agent",))
        self.assertEqual(self._read("secret", {"agentId": "outsider-agent"}).status_code, 200)
        self.assertEqual(self._read("secret", {"agentId": "member-agent"}).status_code, 403)
        self.assertEqual(self._read("general", {"agentId": "member-agent"}).status_code, 200)
        self.assertEqual(self._read("general", {"agentId": "outsider-agent"}).status_code, 403)

    def test_marking_read_receipts_only_the_callers_own_channel_mail(self):
        """The count in the response is the number of receipts written, and it must not include
        another member's copies — channel fan-out gives every member its OWN row."""
        self._channel("busy", members=("member-agent", "outsider-agent"))
        sent = self.client.post(
            "/api/v1/channels/busy/send",
            json={"from_agent": "creator", "channel": "busy", "type": "info", "body": "b"},
        )
        self.assertEqual(sent.status_code, 200, sent.text)

        first = self._read("busy", {"agentId": "member-agent"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["read"], 1, "its own fan-out row, not both members'")

        # Idempotent: the receipt already exists, so a second call writes nothing new — but the
        # count reports rows CONSIDERED, which is what makes re-running it safe to read.
        again = self._read("busy", {"agentId": "member-agent"})
        self.assertEqual(again.json()["read"], 1)

        other = self._read("busy", {"agentId": "outsider-agent"})
        self.assertEqual(other.json()["read"], 1, "the other member's own row is still unread to it")
