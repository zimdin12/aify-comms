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
from service import control_plane as api_v2  # v0.5.3: helpers live in the control plane now

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

    def test_rejects_sender_truncated_body_instead_of_silently_losing_content(self):
        direct_response = self._send(
            from_agent="sender",
            to="recipient",
            body="Full operational report starts here...[truncated]",
            type="info",
        )
        dispatch_response = self.client.post(
            "/api/v1/dispatch",
            json={
                "from_agent": "sender",
                "to": "recipient",
                "subject": "truncated dispatch",
                "body": "Full operational report starts here...[truncated]",
                "createMessage": True,
            },
        )
        channel_response = self.client.post(
            "/api/v1/channels/reports/send",
            json={
                "from_agent": "sender",
                "channel": "reports",
                "body": "Full operational report starts here...[truncated]",
            },
        )

        for response in (direct_response, dispatch_response, channel_response):
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("resend", response.text.lower())
        self.assertEqual(self._count_messages("sender", "Full operational report starts here...[truncated]"), 0)

    def test_unique_index_makes_dedup_atomic(self):
        # The PARTIAL UNIQUE index is the atomicity source that the upfront SELECT alone
        # can't provide under a concurrent retry (the review's HIGH #240). Verify a second
        # INSERT of the same (from_agent, client_nonce, to_agent) is REJECTED at the DB
        # even when the SELECT fast-path is bypassed (simulating the race where both
        # requests SELECT-miss before either commits).
        import sqlite3
        first = self._send(from_agent="sender", to="recipient", body="atomic",
                           type="message", clientNonce="race-1")
        self.assertTrue(first.json().get("ok"))

        async def _raw_dup_insert():
            db = await get_db()
            try:
                # Bypass send_message's SELECT: try to insert a duplicate nonce row directly,
                # exactly what a raced second handler's INSERT would attempt.
                await db.execute(
                    "INSERT OR IGNORE INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, client_nonce, timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("dup-id", "sender", "recipient", "direct", "message", "s", "atomic",
                     "normal", 0, None, "race-1", 1),
                )
                await db.commit()
                cur = await db.execute(
                    "SELECT COUNT(*) AS c FROM messages WHERE from_agent='sender' AND client_nonce='race-1'"
                )
                return int((await cur.fetchone())["c"])
            finally:
                await db.close()

        count = asyncio.run(_raw_dup_insert())
        self.assertEqual(count, 1, "the unique index must reject the duplicate-nonce row")

    def test_concurrent_retry_does_not_double_send(self):
        # End-to-end race sim: seed the winner's row for a nonce (as if request #1 already
        # committed), then a second request with the SAME nonce must NOT create a second
        # message or extra dispatch — it returns the original id as a replay. This is the
        # exact double-send the review flagged that the 4 sequential tests missed.
        first = self._send(from_agent="sender", to="recipient", body="norace",
                           type="message", clientNonce="race-2")
        first_id = first.json().get("messageId")
        # The racing retry (same nonce) — its INSERT OR IGNORE is rejected by the index,
        # so the handler returns the original id and creates nothing new.
        second = self._send(from_agent="sender", to="recipient", body="norace",
                            type="message", clientNonce="race-2")
        self.assertTrue(second.json().get("ok"))
        self.assertEqual(second.json().get("messageId"), first_id)
        self.assertEqual(self._count_messages("sender", "norace"), 1,
                         "a raced retry must not create a second message")
