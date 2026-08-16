"""Sending, channelling and marking read — the messaging refusals, and one guard in three files.

Seven of these had no test, and all seven read as exercised until fe1e22ad because
`service/tests/data/` holds pre-split copies of the handlers:

    POST /dispatch              400 Need 'to' or 'toRole'
                                400 Dispatch no longer supports mode='message_only'. …
    POST /messages/send         400 Need 'to' or 'toRole'          (the SAME sentence, a second file)
    POST /channels/{n}/send     403 Agent '<a>' is not a member of #<n>. Join first.
    POST /channels              409 Channel '<n>' already exists
    POST /messages/{id}/read    400 Need agentId
                                403 Message "<id>" is not addressed to "<a>"
    (any of the three sends)    422 Message body was already truncated by the sender; …

`_reject_sender_truncated_body` IS ONE GUARD WITH THREE CALL SITES, which is the shape that fails
by omission: a fourth send path added without the call is invisible from every green run. So it is
tested at ALL THREE rather than once — the same reason the `..` traversal needed fixing at both ends
of the wire rather than at whichever end was noticed first.

WHY A TRUNCATED BODY IS REFUSED AT ALL. The sender's own client cut the message short and stamped it
"…[truncated]". Delivering that gives the recipient a message whose missing half nobody can recover:
the sender believes it sent the whole thing, and the recipient cannot tell what is missing. A 422
sends the problem back to the only participant that still has the text.

THE 403s ARE ABOUT ADDRESSING, NOT SECRECY. Neither hides anything — a channel's messages are
readable by anyone who joins, and the read-receipt one only refuses writing SOMEONE ELSE's receipt.
That is worth stating because it is why they are 403 and not 404: the caller is known and the target
exists, they are simply not the party entitled to act.
"""

from __future__ import annotations

from service.routers.api_v2 import router  # noqa: F401 — the base builds the app from it
from service.tests._base import FastApiTestCase

SENDER = "lc-sender"
TARGET = "lc-target"
OTHER = "lc-other"

#: Every spelling the guard recognises, from its own pattern: both ellipsis forms, optional trailing
#: fence, optional trailing whitespace. Asserted as a set because a client emits exactly one of them
#: and which one is not something this repo controls.
TRUNCATED_BODIES = (
    "here is the plan ...[truncated]",
    "here is the plan …[truncated]",
    "```\ncode\n...[truncated]\n```",
    "here is the plan ...[TRUNCATED]   ",
)

#: Bodies that merely MENTION truncation, or truncate somewhere other than the end. The guard is
#: about a body the sender cut off, not about a word.
COMPLETE_BODIES = (
    "the log was ...[truncated] before I read the rest, so I re-ran it",
    "please avoid [truncated] markers in your reports",
    "a normal message",
)


class MessagingRefusalTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for agent_id in (SENDER, TARGET, OTHER):
            response = self.client.post(
                "/api/v1/agents", json={"agentId": agent_id, "role": "coder"},
            )
            self.assertEqual(response.status_code, 200, response.text)

    # ── a message needs somewhere to go ──────────────────────────────────────────────────────

    def test_both_send_paths_require_a_recipient(self):
        """THE SAME SENTENCE IN TWO FILES. `/dispatch` and `/messages/send` each carry their own
        copy of this check, so testing one proves nothing about the other — and a message with no
        recipient is not a partial success, it is a message that goes nowhere."""
        for path in ("/api/v1/dispatch", "/api/v1/messages/send"):
            for body in ({}, {"to": ""}, {"toRole": ""}, {"to": "", "toRole": ""}):
                with self.subTest(path=path, body=body):
                    payload = {"from_agent": SENDER, "subject": "s", "body": "hello", **body}
                    response = self.client.post(path, json=payload)
                    self.assertEqual(response.status_code, 400, response.text)
                    self.assertEqual(response.json()["detail"], "Need 'to' or 'toRole'")

    def test_either_field_alone_is_enough(self):
        """The accepting side, on both paths. A gate tested only from the refusing side passes just
        as well when it refuses everything."""
        for path in ("/api/v1/dispatch", "/api/v1/messages/send"):
            with self.subTest(path=path, addressed_by="to"):
                response = self.client.post(
                    path, json={"from_agent": SENDER, "to": TARGET, "subject": "s", "body": "hello"},
                )
                self.assertEqual(response.status_code, 200, response.text)
            with self.subTest(path=path, addressed_by="toRole"):
                response = self.client.post(
                    path, json={"from_agent": SENDER, "toRole": "coder", "subject": "s", "body": "hello"},
                )
                self.assertEqual(response.status_code, 200, response.text)

    def test_dispatch_no_longer_takes_message_only_and_says_what_to_use(self):
        """A retired mode. The refusal names both replacements because the right one depends on
        what the caller wanted — live messaging or tracked work — and a bare "unsupported" would
        leave them guessing."""
        response = self.client.post(
            "/api/v1/dispatch",
            json={"from_agent": SENDER, "to": TARGET, "subject": "s", "body": "hi", "mode": "message_only"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"],
            "Dispatch no longer supports mode='message_only'. Use comms_send for normal live "
            "messaging or comms_dispatch without message_only for tracked work.",
        )

    # ── a body the sender already cut short ──────────────────────────────────────────────────

    def test_every_send_path_refuses_a_body_the_sender_truncated(self):
        """ALL THREE call sites of one guard. A fourth send path added without the call is invisible
        from a green run, which is exactly how a shared guard fails."""
        for body in TRUNCATED_BODIES:
            with self.subTest(path="/dispatch", body=body[:30]):
                response = self.client.post(
                    "/api/v1/dispatch",
                    json={"from_agent": SENDER, "to": TARGET, "subject": "s", "body": body},
                )
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(
                    response.json()["detail"],
                    "Message body was already truncated by the sender; resend a complete concise "
                    "body or link a durable artifact.",
                )
            with self.subTest(path="/messages/send", body=body[:30]):
                response = self.client.post(
                    "/api/v1/messages/send",
                    json={"from_agent": SENDER, "to": TARGET, "subject": "s", "body": body},
                )
                self.assertEqual(response.status_code, 422, response.text)
            with self.subTest(path="/channels/send", body=body[:30]):
                self._create_channel("general")
                response = self.client.post(
                    "/api/v1/channels/general/send",
                    json={"from_agent": SENDER, "channel": "general", "body": body},
                )
                self.assertEqual(response.status_code, 422, response.text)

    def test_a_body_that_merely_mentions_truncation_is_delivered(self):
        """The guard keys on a marker at the END, not on a word. Refusing a message that discusses
        truncated output would make the failure mode impossible to talk about."""
        for body in COMPLETE_BODIES:
            with self.subTest(body=body[:40]):
                response = self.client.post(
                    "/api/v1/messages/send",
                    json={"from_agent": SENDER, "to": TARGET, "subject": "s", "body": body},
                )
                self.assertEqual(response.status_code, 200, response.text)

    # ── channels ─────────────────────────────────────────────────────────────────────────────

    def _create_channel(self, name: str, created_by: str = SENDER):
        return self.client.post(
            "/api/v1/channels", json={"name": name, "createdBy": created_by},
        )

    def test_a_non_member_cannot_send_to_a_channel_and_is_told_to_join(self):
        """403 rather than 404: the channel exists and the caller is known, they are simply not a
        member. The remedy is in the message because joining is a single call away."""
        self._create_channel("general")
        response = self.client.post(
            "/api/v1/channels/general/send",
            json={"from_agent": OTHER, "channel": "general", "body": "hello"},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["detail"],
            f"Agent '{OTHER}' is not a member of #general. Join first.",
        )

    def test_the_creator_is_a_member_without_joining(self):
        """The mirror, and the reason the refusal above is not simply "everyone is refused"."""
        self._create_channel("general")
        response = self.client.post(
            "/api/v1/channels/general/send",
            json={"from_agent": SENDER, "channel": "general", "body": "hello"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_joined_member_may_send(self):
        self._create_channel("general")
        joined = self.client.post(
            "/api/v1/channels/general/join", json={"agentId": OTHER},
        )
        self.assertEqual(joined.status_code, 200, joined.text)
        response = self.client.post(
            "/api/v1/channels/general/send",
            json={"from_agent": OTHER, "channel": "general", "body": "hello"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_creating_a_channel_that_already_exists_is_refused(self):
        self.assertEqual(self._create_channel("general").status_code, 200)
        response = self._create_channel("general", created_by=OTHER)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "Channel 'general' already exists")

    # ── read receipts belong to their addressee ──────────────────────────────────────────────

    def _send_to(self, target: str) -> str:
        response = self.client.post(
            "/api/v1/messages/send",
            json={"from_agent": SENDER, "to": target, "subject": "s", "body": "hello"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["messageId"]

    def test_marking_read_requires_saying_who_is_reading(self):
        message_id = self._send_to(TARGET)
        for body in ({}, {"agentId": ""}, {"agentId": "   "}):
            with self.subTest(body=body):
                response = self.client.post(
                    f"/api/v1/messages/{message_id}/read", json=body,
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "Need agentId")

    def test_an_agent_cannot_mark_someone_elses_message_read(self):
        """A read receipt is a claim about who has seen what — the dispatcher uses it to decide
        whether a message still needs delivering. Writing one for another agent would silently
        close their unread item."""
        message_id = self._send_to(TARGET)
        response = self.client.post(
            f"/api/v1/messages/{message_id}/read", json={"agentId": OTHER},
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            response.json()["detail"],
            f'Message "{message_id}" is not addressed to "{OTHER}"',
        )

    def test_the_addressee_may_mark_it_read(self):
        message_id = self._send_to(TARGET)
        response = self.client.post(
            f"/api/v1/messages/{message_id}/read", json={"agentId": TARGET},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_an_unknown_message_is_404_before_the_403(self):
        """Order: "there is no such message" outranks "it is not yours". The reverse would tell a
        caller a message exists whenever they guessed an id."""
        response = self.client.post(
            "/api/v1/messages/no-such-message/read", json={"agentId": TARGET},
        )
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Message 'no-such-message' not found")
