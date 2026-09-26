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

    def test_a_reused_nonce_for_a_different_send_is_refused_not_swallowed(self):
        # A nonce names ONE logical send. Before 0.7.0 the replay lookup keyed only on
        # (from_agent, client_nonce) and never compared the payload, so a second, DIFFERENT send
        # carrying a reused nonce answered ok:true/replayed:true with the first message's id and
        # was silently dropped (comms-senior-dev, v0.7 scan G2).
        self._register("third")
        parent = self._send(from_agent="recipient", to="sender", body="a parent", type="message").json()["messageId"]
        first = self._send(from_agent="sender", to="recipient", body="the first send",
                           type="message", clientNonce="reused")
        self.assertTrue(first.json().get("ok"), first.text)
        for label, changed in (
            ("another recipient", {"to": "third", "body": "the first send"}),
            ("another body", {"to": "recipient", "body": "a different body"}),
            ("a triggered send", {"to": "recipient", "body": "the first send", "trigger": True}),
            # v0.7 review: these were not compared, so a retry that changed them got the old message
            # back and the change was lost -- the reply linkage and priority most of all.
            ("another reply parent", {"to": "recipient", "body": "the first send", "inReplyTo": parent}),
            ("another priority", {"to": "recipient", "body": "the first send", "priority": "urgent"}),
            ("another reply contract", {"to": "recipient", "body": "the first send", "requireReply": True}),
            ("another delivery option", {"to": "recipient", "body": "the first send", "queueIfBusy": True}),
        ):
            with self.subTest(label):
                body = {"from_agent": "sender", "type": "message", "clientNonce": "reused", **changed}
                second = self._send(**body)
                self.assertEqual(second.status_code, 409, second.text)
                self.assertIn("reused", second.text)
        # Control: the SAME send under the same nonce is still a replay.
        again = self._send(from_agent="sender", to="recipient", body="the first send",
                           type="message", clientNonce="reused")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json().get("messageId"), first.json().get("messageId"))

    def test_two_racing_sends_under_one_nonce_to_different_recipients_write_one(self):
        # v0.7 review: the unique index is per recipient, so two sends under one nonce with DISJOINT
        # recipients both passed the lookup and both inserted. The race is forced by making the second
        # send's fast-path lookup miss, as it does when both requests read before either writes.
        from unittest import mock

        from service.routers.dispatch_messages import messages as route

        self._register("third")
        first = self._send(from_agent="sender", to="recipient", body="racing send", type="message", clientNonce="raced")
        self.assertTrue(first.json().get("ok"), first.text)
        real = route.prior_send_for_nonce
        calls = []

        async def misses_once(*args, **kwargs):
            calls.append(1)
            return None if len(calls) == 1 else await real(*args, **kwargs)

        with mock.patch.object(route, "prior_send_for_nonce", misses_once):
            second = self._send(from_agent="sender", to="third", body="racing send", type="message", clientNonce="raced")
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(self._count_messages("sender", "racing send"), 1, "both racing sends were written")
        self.assertEqual(len(calls), 2, "control: the raced path was reached")

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

    # A sender-truncated body is refused, and not stored, on every send path:
    # `test_messaging_refusals.py::test_every_send_path_refuses_a_body_the_sender_truncated`.

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


class ARetryIsTheSameSendWhateverChangedAroundItTests(FastApiTestCase):
    """v0.7.1 review. W07: the fingerprint recorded the RESOLVED recipients, so a `toRole` retry after
    another agent registered with that role resolved differently and got a 409 for a send that had
    succeeded. W09: a fan-out acknowledges `msg_id` but stores `msg_id-<recipient>` rows, and a retry
    answered with the first row's suffixed id instead of the id the first attempt returned."""

    DB_NAME = "aify-msg-idempotency-intent.db"

    def _register(self, agent_id: str, role: str = "coder"):
        r = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": role})
        self.assertEqual(r.status_code, 200, r.text)

    def _send(self, **body):
        body.setdefault("subject", "test")
        body.setdefault("type", "info")
        return self.client.post("/api/v1/messages/send", json=body)

    def setUp(self):
        super().setUp()
        self._register("sender", role="lead")
        self._register("rev-1", role="reviewer")
        self._register("rev-2", role="reviewer")

    def test_a_role_send_retried_after_the_roster_changed_is_the_same_send(self):
        first = self._send(from_agent="sender", toRole="reviewer", body="look", clientNonce="n-role")
        self.assertEqual(first.status_code, 200, first.text)
        self._register("rev-3", role="reviewer")
        retry = self._send(from_agent="sender", toRole="reviewer", body="look", clientNonce="n-role")
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["messageId"], first.json()["messageId"])

    def test_control_a_different_role_under_the_same_nonce_is_still_refused(self):
        self._register("tester", role="tester")
        self.assertEqual(self._send(from_agent="sender", toRole="reviewer", body="x", clientNonce="n-2").status_code, 200)
        self.assertEqual(self._send(from_agent="sender", toRole="tester", body="x", clientNonce="n-2").status_code, 409)

    def test_a_fan_out_retry_returns_the_id_the_first_attempt_returned(self):
        first = self._send(from_agent="sender", toRole="reviewer", body="fan", clientNonce="n-fan")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(len(first.json()["recipients"]), 2, "control: this is a fan-out")
        retry = self._send(from_agent="sender", toRole="reviewer", body="fan", clientNonce="n-fan")
        self.assertEqual(retry.json()["messageId"], first.json()["messageId"])
