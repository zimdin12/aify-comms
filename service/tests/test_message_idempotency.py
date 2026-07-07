"""Task #240 regression: /messages/send is idempotent under a client nonce.

A send that hits a transient socket error may have actually landed server-side.
Because /messages/send minted a fresh msg_id per call and carried no client key,
the bridge could not safely retry — so it excluded /messages/send from its retry
list and DROPPED the send on a transient error (stranding an owed reply /
require_reply run). With an optional `clientNonce`, a retry of the same logical
send collapses to the original message instead of creating a duplicate, so the
bridge can retry safely.
"""
from service.db import get_db
from service.routers import api_v2

from service.tests._base import FastApiTestCase

import asyncio


class MessageIdempotencyTests(FastApiTestCase):
    DB_NAME = "aify-msg-idempotency-test.db"

    def _register(self, agent_id: str, *, role: str = "coder", **extra):
        payload = {"agentId": agent_id, "role": role}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _send(self, **body):
        body.setdefault("subject", "test")
        return self.client.post("/api/v1/messages/send", json=body)

    def _count_messages(self, from_agent: str, body_text: str) -> int:
        async def _run():
            db = await get_db()
            try:
                cur = await db.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE from_agent = ? AND body = ?",
                    (from_agent, body_text),
                )
                row = await cur.fetchone()
                return int(row["c"])
            finally:
                await db.close()

        return asyncio.run(_run())

    def setUp(self):
        super().setUp()
        self._register("sender")
        self._register("recipient")

    def test_same_nonce_collapses_to_one_message(self):
        first = self._send(from_agent="sender", to="recipient", body="hello once",
                           type="message", clientNonce="nonce-A")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(first.json().get("ok"), first.text)
        first_id = first.json().get("messageId")

        # Simulate the bridge retrying the exact same logical send after a
        # transient socket blip (same nonce).
        second = self._send(from_agent="sender", to="recipient", body="hello once",
                            type="message", clientNonce="nonce-A")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json().get("ok"), second.text)
        second_id = second.json().get("messageId")

        self.assertEqual(first_id, second_id, "replay must return the ORIGINAL messageId")
        self.assertEqual(
            self._count_messages("sender", "hello once"), 1,
            "same nonce must not create a duplicate message row",
        )

    def test_distinct_nonces_create_distinct_messages(self):
        a = self._send(from_agent="sender", to="recipient", body="twice",
                       type="message", clientNonce="nonce-1")
        b = self._send(from_agent="sender", to="recipient", body="twice",
                       type="message", clientNonce="nonce-2")
        self.assertTrue(a.json().get("ok"))
        self.assertTrue(b.json().get("ok"))
        self.assertNotEqual(a.json().get("messageId"), b.json().get("messageId"))
        self.assertEqual(self._count_messages("sender", "twice"), 2)

    def test_no_nonce_behaves_as_before(self):
        # Without a nonce, two identical sends are two distinct messages (today's
        # behavior — backward compatible for old bridges that omit clientNonce).
        a = self._send(from_agent="sender", to="recipient", body="nokey", type="message")
        b = self._send(from_agent="sender", to="recipient", body="nokey", type="message")
        self.assertTrue(a.json().get("ok"))
        self.assertTrue(b.json().get("ok"))
        self.assertNotEqual(a.json().get("messageId"), b.json().get("messageId"))
        self.assertEqual(self._count_messages("sender", "nokey"), 2)

    def test_nonce_scoped_per_sender(self):
        # The same nonce string from a DIFFERENT sender is a different logical send.
        self._register("other")
        a = self._send(from_agent="sender", to="recipient", body="scoped",
                       type="message", clientNonce="shared")
        b = self._send(from_agent="other", to="recipient", body="scoped",
                       type="message", clientNonce="shared")
        self.assertNotEqual(a.json().get("messageId"), b.json().get("messageId"))
        self.assertEqual(self._count_messages("sender", "scoped"), 1)
        self.assertEqual(self._count_messages("other", "scoped"), 1)
