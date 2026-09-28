"""With `OPERATOR_KEY` set, a message may name the operator as its sender only if it proves the key.

WHY THE SENDER NAME MATTERS. A message from `dashboard` is the operator's own: the managed wake prompt
leaves out the peer trust rule for it and calls the sender "the human/operator"
(`mcp/stdio/runtimes-prompts.js`), and the Claude channel tells the session that only a from_agent
other than `dashboard` is another agent (`mcp/stdio/claude-channel.js`). So whoever may send as
`dashboard` speaks with the operator's authority to every agent.

THE DEFECT (external review, 2026-09-29, O3). KNOWN_ISSUES.md said a host that wants more than the
API key "sets OPERATOR_KEY". Nothing on the three sending routes read it: with the key set, any holder
of the shared API key could still send as `dashboard` and be read as the operator. The key gated
unsend, channel delete and unshare only (`test_operator_privilege_must_be_proven.py`).

THE RULE NOW is the one those three endpoints already use, `authorize_operator`: with no operator key
the claim is granted (the v0.7.5 ruling, the API key is the trust boundary), and with one the sender
`dashboard` or `operator`, in any case, must present `X-Aify-Operator-Key`. The dashboard page sends
that header on every request to its own service (`service/new_dashboard/api-client.mjs`).
"""

from __future__ import annotations

import asyncio

from service.api_core.operator_authz import OPERATOR_KEY_HEADER
from service.db import get_db
from service.tests._base import FastApiTestCase

RECIPIENT = "recipient-agent"
SECRET = "s3cret-operator-key"
ROOM = "ops-room"


class SendingAsTheOperatorRequiresTheOperatorKey(FastApiTestCase):
    DB_NAME = "aify-operator-sender-test.db"

    def setUp(self):
        super().setUp()

        async def seed():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agents (id, name, role, runtime, session_mode, status,"
                    " registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (RECIPIENT, RECIPIENT, "coder", "claude-code", "resident", "online",
                     "2026-09-29T00:00:00Z", "2026-09-29T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
                    (ROOM, "", RECIPIENT, "2026-09-29T00:00:00Z"),
                )
                for member in ("dashboard", "Dashboard", "operator", RECIPIENT):
                    await db.execute(
                        "INSERT INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
                        (ROOM, member, "2026-09-29T00:00:00Z"),
                    )
                await db.commit()
            finally:
                await db.close()

        asyncio.run(seed())

    def _set_key(self, key: str):
        self.client.app.state.config.operator_key = key

    def _sends(self):
        """Every route that writes a message whose sender the caller names."""
        return {
            "messages/send": lambda sender, headers: self.client.post(
                "/api/v1/messages/send", headers=headers,
                json={"from_agent": sender, "to": RECIPIENT, "type": "info", "subject": "s", "body": "b"}),
            "dispatch": lambda sender, headers: self.client.post(
                "/api/v1/dispatch", headers=headers,
                json={"from_agent": sender, "to": RECIPIENT, "type": "request", "subject": "s", "body": "b"}),
            "channel send": lambda sender, headers: self.client.post(
                f"/api/v1/channels/{ROOM}/send", headers=headers,
                json={"from_agent": sender, "channel": ROOM, "body": "b", "trigger": False}),
        }

    def _messages_from(self, sender: str) -> int:
        async def count():
            db = await get_db()
            try:
                row = await (await db.execute(
                    "SELECT COUNT(*) FROM messages WHERE from_agent = ?", (sender,))).fetchone()
                return int(row[0])
            finally:
                await db.close()
        return asyncio.run(count())

    def test_with_the_key_set_an_unproven_operator_sender_is_refused(self):
        self._set_key(SECRET)
        for sender in ("dashboard", "Dashboard", "operator"):
            for route, send in self._sends().items():
                with self.subTest(sender=sender, route=route):
                    response = send(sender, {})
                    self.assertEqual(response.status_code, 403, f"{route}: {response.text[:200]}")
                    self.assertIn("does not grant permission", response.text)
            self.assertEqual(self._messages_from(sender), 0, f"a refused send as {sender} was still written")

    def test_with_the_key_set_a_wrong_key_is_refused(self):
        self._set_key(SECRET)
        for route, send in self._sends().items():
            with self.subTest(route=route):
                self.assertEqual(send("dashboard", {OPERATOR_KEY_HEADER: "nope"}).status_code, 403)

    def test_with_the_key_set_the_dashboard_presenting_it_still_sends(self):
        """ANTI-VACUITY: the refusals above would pass if the routes refused everything."""
        self._set_key(SECRET)
        for route, send in self._sends().items():
            with self.subTest(route=route):
                before = self._messages_from("dashboard")
                response = send("dashboard", {OPERATOR_KEY_HEADER: SECRET})
                self.assertEqual(response.status_code, 200, f"{route}: {response.text[:200]}")
                self.assertGreater(self._messages_from("dashboard"), before, f"{route} wrote nothing")

    def test_with_the_key_set_an_ordinary_agent_needs_no_operator_key(self):
        self._set_key(SECRET)
        response = self._sends()["messages/send"](RECIPIENT, {})
        self.assertEqual(response.status_code, 200, response.text[:200])

    def test_with_no_key_the_operator_sender_is_granted(self):
        """The v0.7.5 ruling holds: with no OPERATOR_KEY the API key is the trust boundary."""
        self._set_key("")
        for route, send in self._sends().items():
            with self.subTest(route=route):
                self.assertEqual(send("dashboard", {}).status_code, 200, route)
