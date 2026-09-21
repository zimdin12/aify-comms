"""A message from an agent this instance has never registered must not look like a colleague's.

ASKED BY THE OPERATOR, 2026-09-21: an agent on their second PC sent a message here, and the message
arrived looking exactly like every other. Nothing on the send path validates `from_agent` --
`_touch_agent` is a bare `UPDATE agents SET ... WHERE id = ?` that matches no rows for an unknown
sender, and the message is inserted anyway -- so an id this service has never seen is stored, listed
and rendered with no hint that nobody here can vouch for it.

NOT REJECTED, DELIBERATELY. Refusing it would break the cross-machine flow the operator wants; the
fleet already spans two hosts (29 agents on one machine id, six on another). The message arrives, and
it arrives labelled.

WHAT IS NOT HERE, and why: the client IP. Measured on this deployment the same day -- every peer the
service sees is 172.27.0.1, the Docker bridge gateway, or a sibling container. Traffic from this
host and traffic from the other PC are both NAT'd to that one address, so an IP in the warning would
read 172.27.0.1 for everything and would be a confident answer to a question it cannot answer.
"""

from __future__ import annotations

import time

from service.tests._base import FastApiTestCase

HOME = "local-agent"
STRANGER = "agent-from-another-pc"


class AMessageFromASenderWeDoNotKnowSaysSo(FastApiTestCase):
    DB_NAME = "aify-unknown-sender-test.db"

    def _register(self, agent_id: str) -> None:
        response = self.client.post("/api/v1/agents", json={
            "agentId": agent_id, "role": "coder", "runtime": "generic", "sessionMode": "resident",
        })
        self.assertEqual(response.status_code, 200, response.text)

    def _send(self, sender: str, subject: str, origin: str | None = None) -> None:
        payload = {"from_agent": sender, "to": HOME, "type": "info", "subject": subject, "body": "hello"}
        if origin is not None:
            payload["origin"] = origin
        response = self.client.post("/api/v1/messages/send", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

    def _inbox(self) -> list[dict]:
        response = self.client.get(f"/api/v1/messages/inbox/{HOME}?filter=all&peek=true")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json().get("messages", [])

    def setUp(self) -> None:
        super().setUp()
        self._register(HOME)

    def test_an_unregistered_sender_is_marked_as_one(self) -> None:
        self._send(STRANGER, "from the other pc")
        [message] = [m for m in self._inbox() if m["subject"] == "from the other pc"]
        self.assertEqual(message["from"], STRANGER, "the id is still shown, it is not hidden")
        self.assertIs(message["fromRegistered"], False)

    def test_CONTROL_a_registered_sender_is_not_accused(self) -> None:
        """In the same run: a badge that appears on everything is not a warning, it is decoration."""
        self._register("teammate")
        self._send("teammate", "from a colleague")
        [message] = [m for m in self._inbox() if m["subject"] == "from a colleague"]
        self.assertIs(message["fromRegistered"], True)

    def test_both_are_answered_in_one_listing(self) -> None:
        """The flag is decided per message from one query, so a mixed page must come back mixed."""
        self._register("teammate")
        self._send("teammate", "known")
        self._send(STRANGER, "unknown")
        by_subject = {m["subject"]: m["fromRegistered"] for m in self._inbox()}
        self.assertEqual(by_subject.get("known"), True)
        self.assertEqual(by_subject.get("unknown"), False)

    def test_a_sender_that_is_REMOVED_later_reads_as_unknown_from_then_on(self) -> None:
        """Derived at read time, not stamped at write time -- which is what makes it true later.

        The operator's case is an agent that never registered here; the same question is asked by a
        message whose sender has since been removed, and a flag frozen into the row at send time
        would still vouch for it.
        """
        self._register("departing")
        self._send("departing", "before it left")
        self.assertIs([m for m in self._inbox() if m["subject"] == "before it left"][0]["fromRegistered"], True)
        response = self.client.delete("/api/v1/agents/departing")
        self.assertIn(response.status_code, (200, 204), response.text)
        self.assertIs([m for m in self._inbox() if m["subject"] == "before it left"][0]["fromRegistered"], False)


class AnExternalSenderCanSayWhereItIs(FastApiTestCase):
    """An agent on another machine sends here WITHOUT registering, and says who to answer.

    THE OPERATOR SETTLED THE SHAPE, 2026-09-22: "i want external to be able to send message if
    external knows the ip ... but in that sense it should include ip and manager name so you could
    send message back", and "that external should not register here. he is agent in another pc and
    it would not make sense if he would register here."

    So the sender is NOT expected to appear in this roster, and the return address travels on the
    message instead. DECLARED, NEVER MEASURED: the service cannot see a remote address -- every peer
    it observes is the Docker bridge gateway or a sibling container -- so this is the sender's claim,
    stored and shown as one.
    """

    DB_NAME = "aify-external-origin-test.db"

    _register = AMessageFromASenderWeDoNotKnowSaysSo._register
    _send = AMessageFromASenderWeDoNotKnowSaysSo._send
    _inbox = AMessageFromASenderWeDoNotKnowSaysSo._inbox

    def setUp(self) -> None:
        super().setUp()
        self._register(HOME)

    def test_an_external_sender_needs_no_row_here_and_its_origin_survives(self) -> None:
        self._send(STRANGER, "from the other pc", origin="192.168.1.50:8800, manager mp-manager")
        [message] = [m for m in self._inbox() if m["subject"] == "from the other pc"]
        self.assertIs(message["fromRegistered"], False, "it must NOT have been registered by sending")
        self.assertEqual(message["origin"], "192.168.1.50:8800, manager mp-manager")

    def test_sending_does_not_create_an_agent_row(self) -> None:
        """The anomaly that started this: a second PC's agent appearing in the roster.

        Sending must never be a back door into it -- the operator's words, "it would not make sense
        if he would register here".
        """
        self._send(STRANGER, "still not ours", origin="10.0.0.9:8800")
        response = self.client.get(f"/api/v1/agents/{STRANGER}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_CONTROL_a_sender_that_declares_nothing_carries_nothing(self) -> None:
        """Absent is absent: no default, no guess, no address invented for it."""
        self._send(STRANGER, "said nothing")
        [message] = [m for m in self._inbox() if m["subject"] == "said nothing"]
        self.assertEqual(message["origin"], "")

    def test_a_declared_origin_is_bounded_and_trimmed(self) -> None:
        """It is attacker-controlled text from an unauthenticated-by-identity sender, so it is
        capped and whitespace-trimmed before it is stored, and escaped where it is drawn."""
        self._send(STRANGER, "long one", origin="  " + ("x" * 500) + "  ")
        [message] = [m for m in self._inbox() if m["subject"] == "long one"]
        self.assertEqual(len(message["origin"]), 200)
        self.assertFalse(message["origin"].startswith(" "))
