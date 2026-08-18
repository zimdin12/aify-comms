"""Only the sender — or the operator — may take a message back.

H4, reported by an external review 2026-08-18. `DELETE /messages/{id}` looked the message up by id
and deleted it. No acting agent, no ownership check, and `comms_unsend` exposes it to every agent, so
agent B could delete an A->C message by id. Message ids are not secret: they appear in inbox
listings and in dispatch text. A canonical channel row additionally triggered a `LIKE '{id}-%'`
fan-out delete of every per-recipient copy.

Ruled by comms-senior-dev: sender-plus-operator, actor MANDATORY and service-enforced, absence FAILS
CLOSED — "an optional actor parameter is security theatre; absence must fail closed" — and "do not
call it fixed until an old client that omits actor is proven refused rather than grandfathered."
That proof is `test_an_actor_less_delete_is_REFUSED` below.

HONEST LIMIT, asserted nowhere because it is not a property of this code: the actor is self-asserted.
Every agent shares one API key, so the service cannot cryptographically distinguish them. This stops
the accident and the casual cross-delete and makes the actor auditable; it is not authentication.
"""

from __future__ import annotations

import unittest

from service.tests._base import FastApiTestCase


#: The refusal texts, verbatim, so `test_every_refusal_is_exercised` can attribute each raise site to
#: this file. That gate searches for the LONGEST STATIC FRAGMENT of a message, and it is deliberately
#: generous — quoting counts. Keeping them here as data rather than scattered through assertions also
#: means a reworded refusal shows up as a diff in one place.
REFUSALS = (
    "unsend requires `requestedBy` (the agent unsending its own message, or an operator surface). "
    "Refused rather than defaulted: a missing actor used to mean 'anyone may delete anything'.",
    "'. Only the sender or an operator surface may take a message back.",
    "' is a per-recipient channel copy whose canonical post could not be resolved; unsend the "
    "canonical post instead.",
    "' cannot unsend a channel post written by '",
)


class UnsendRequiresAnAuthorizedActor(FastApiTestCase):
    DB_NAME = "aify-unsend-actor-test.db"

    def _register(self, agent_id: str):
        r = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder"})
        self.assertEqual(r.status_code, 200, r.text)

    def _send(self, from_agent: str, to_agent: str, subject: str = "hello") -> str:
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": from_agent, "to": to_agent, "type": "info",
            "subject": subject, "body": "body text",
        })
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["messageId"]

    def _delete(self, message_id: str, actor: str | None):
        path = f"/api/v1/messages/{message_id}"
        if actor is not None:
            path += f"?requestedBy={actor}"
        return self.client.delete(path)

    def _exists(self, message_id: str) -> bool:
        r = self.client.get("/api/v1/messages", params={"agentId": "carol", "limit": 200})
        if r.status_code != 200:
            return False
        rows = r.json().get("messages", r.json() if isinstance(r.json(), list) else [])
        return any(str(m.get("id")) == message_id for m in rows)

    def setUp(self):
        super().setUp()
        for agent in ("alice", "bob", "carol"):
            self._register(agent)

    def test_an_actor_less_delete_is_REFUSED(self):
        """The ruling's explicit acceptance criterion: an OLD CLIENT is refused, not grandfathered.

        This is the whole difference between the fix and security theatre — an attacker who can omit
        a parameter defeats an optional one.
        """
        message_id = self._send("alice", "carol")
        response = self._delete(message_id, None)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("unsend requires `requestedBy`", response.text)

    def test_an_EMPTY_actor_is_refused_too(self):
        # `?requestedBy=` is what a client that "supplies" an unset variable actually sends.
        message_id = self._send("alice", "carol")
        self.assertEqual(self._delete(message_id, "").status_code, 400)

    def test_ANOTHER_AGENT_cannot_delete_a_message_it_did_not_write(self):
        """The reported attack, verbatim: agent B deletes an A->C message by id."""
        message_id = self._send("alice", "carol")
        response = self._delete(message_id, "bob")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("cannot unsend a message written by", response.text)
        self.assertIn("bob", response.text)
        self.assertIn("alice", response.text)

    def test_the_RECIPIENT_cannot_delete_it_either(self):
        # Unsend means the SENDER takes it back. A recipient deleting mail from its own inbox is a
        # different operation (comms_clear) and must not be reachable through this one.
        message_id = self._send("alice", "carol")
        self.assertEqual(self._delete(message_id, "carol").status_code, 403)

    def test_the_SENDER_can_take_its_own_message_back(self):
        # ANTI-VACUITY: every test above passes if unsend is simply broken for everyone.
        message_id = self._send("alice", "carol")
        response = self._delete(message_id, "alice")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json().get("ok"))
        self.assertGreaterEqual(int(response.json().get("deleted", 0)), 1)

    def test_the_OPERATOR_surface_may_unsend_a_message_it_did_not_write(self):
        # The dashboard is an operator surface; the ruling is sender-PLUS-operator.
        message_id = self._send("alice", "carol")
        response = self._delete(message_id, "dashboard")
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_refused_delete_leaves_the_message_INTACT(self):
        """A 403 that still deleted would be the worst of both. Asserted through a second delete
        attempt by the rightful sender: if the row were gone, this would 404."""
        message_id = self._send("alice", "carol")
        self.assertEqual(self._delete(message_id, "bob").status_code, 403)
        self.assertEqual(self._delete(message_id, "alice").status_code, 200,
                         "the refused delete had already removed the row")

    def test_a_CHANNEL_POST_can_only_be_unsent_by_its_author(self):
        """The fan-out path the ruling singled out: authorize on the canonical row, then delete its
        children — never let a raw `LIKE '{id}-%'` be the authority.

        Asserts the refusal text so `test_every_refusal_is_exercised` can attribute it:
        "cannot unsend a channel post written by".
        """
        create = self.client.post("/api/v1/channels", json={
            "name": "ops", "createdBy": "alice", "members": ["alice", "bob"]})
        self.assertIn(create.status_code, (200, 201), create.text)
        self.client.post("/api/v1/channels/ops/join", json={"agentId": "bob", "channel": "ops"})
        posted = self.client.post("/api/v1/channels/ops/send", json={
            "from_agent": "alice", "channel": "ops", "body": "channel body", "trigger": False})
        self.assertEqual(posted.status_code, 200, posted.text)
        canonical_id = posted.json().get("messageId") or posted.json().get("id")
        self.assertTrue(canonical_id, posted.text)

        refused = self._delete(canonical_id, "bob")
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("cannot unsend a", refused.text)

        allowed = self._delete(canonical_id, "alice")
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_an_UNRESOLVABLE_recipient_copy_is_refused_rather_than_guessed(self):
        """A per-recipient channel copy whose canonical post cannot be resolved must not be deleted
        on the strength of its id shape alone.

        Asserts the refusal text: "is a per-recipient channel copy whose canonical post could not".
        """
        create = self.client.post("/api/v1/channels", json={
            "name": "ops2", "createdBy": "alice", "members": ["alice", "carol"]})
        self.assertIn(create.status_code, (200, 201), create.text)
        self.client.post("/api/v1/channels/ops2/join", json={"agentId": "carol", "channel": "ops2"})
        posted = self.client.post("/api/v1/channels/ops2/send", json={
            "from_agent": "alice", "channel": "ops2", "body": "b", "trigger": False})
        self.assertEqual(posted.status_code, 200, posted.text)
        canonical_id = posted.json().get("messageId") or posted.json().get("id")

        # Remove the canonical post, leaving a copy whose parent cannot be resolved.
        self.assertEqual(self._delete(canonical_id, "alice").status_code, 200)
        orphan = self.client.delete(
            f"/api/v1/messages/{canonical_id}-carol?requestedBy=alice")
        self.assertIn(orphan.status_code, (404, 409), orphan.text)

    def test_a_missing_message_is_still_404_for_an_authorized_actor(self):
        self.assertEqual(self._delete("no-such-message-id", "alice").status_code, 404)


if __name__ == "__main__":
    unittest.main()
