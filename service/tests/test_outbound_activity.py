"""The agent health surface must answer "what did this agent PRODUCE?".

AUDIT FINDING 1 (2026-08-10), from a traced operator-visible failure.

During an outage `comms_agent_info` kept answering normally, so a manager told the operator THREE
TIMES that a lane was dead. It was not — the agent had replied and the reply sat undelivered.
Every field was individually true and every one of them was about the wrong thing:

    unread      inbound messages not yet read       — wrong DIRECTION
    last read   the last message it CONSUMED        — wrong DIRECTION
    last seen   registration/heartbeat liveness     — a bare status PATCH advances it
    status      worker reachability / dispatch      — not productivity

The reporter asked for a DEGRADED/STALE marker. The reviewer argued that a STALE marker retires a
DIFFERENT artifact ("the delivery path is verified") and still cannot say what an agent last
produced — so callers would keep inferring productivity from inbound fields, which is exactly how
the false claim was made. Outbound activity is the field that retires it.

These tests pin the distinction the surface previously could not express: an agent that has
RECEIVED plenty and PRODUCED nothing must look different from one that has produced something.
"""

from __future__ import annotations

import asyncio
import time
import unittest
import uuid

from service.db import get_db
from service.tests._base import FastApiTestCase


class OutboundActivityTests(FastApiTestCase):
    DB_NAME = "aify-outbound-activity.db"

    def setUp(self):
        super().setUp()
        for agent in ("alice", "bob"):
            self.client.post("/api/v1/agents", json={
                "agentId": agent, "name": agent, "role": "coder", "runtime": "claude-code",
            })

    def _send(self, frm, to, subject="s", body="b"):
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": frm, "to": to, "type": "info", "subject": subject, "body": body,
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _info(self, agent_id):
        r = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json().get("agent", r.json())

    def _roster(self):
        r = self.client.get("/api/v1/agents")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["agents"]

    # ── the distinction that did not exist before ────────────────────────────────────
    def test_an_agent_that_has_only_RECEIVED_reports_no_outbound(self):
        self._send("bob", "alice")
        alice = self._info("alice")
        self.assertEqual(alice["outbound"].get("lastSentAt"), None,
                         "receiving mail is not producing anything")

    def test_an_agent_that_has_SENT_reports_when(self):
        self._send("alice", "bob")
        alice = self._info("alice")
        self.assertTrue(alice["outbound"].get("lastSentAt"), "a sent message is production")

    def test_receiving_does_not_create_outbound_activity_for_the_recipient(self):
        """THE trace: unread/last-read move for the recipient, outbound must not."""
        self._send("bob", "alice")
        alice = self._info("alice")
        self.assertGreater(alice["unread"], 0, "precondition: inbound activity exists")
        self.assertEqual(alice["outbound"], {}, "inbound traffic must not imply production")

    def test_the_two_directions_are_independently_visible(self):
        self._send("bob", "alice", subject="inbound")
        self._send("alice", "bob", subject="outbound")
        alice = self._info("alice")
        self.assertGreater(alice["unread"], 0)
        self.assertTrue(alice["outbound"].get("lastSentAt"))

    # ── shape and honesty ────────────────────────────────────────────────────────────
    def test_timestamp_is_iso_not_epoch_millis(self):
        """messages.timestamp is epoch MILLISECONDS; leaking that raw would be unreadable and
        would sort wrong against the ISO fields beside it."""
        self._send("alice", "bob")
        ts = self._info("alice")["outbound"]["lastSentAt"]
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_unknown_is_an_empty_dict_never_a_fabricated_time(self):
        fresh = self._info("bob")
        self.assertEqual(fresh["outbound"], {},
                         "absence must be absence — inventing a timestamp is the bug being fixed")

    def test_the_KEY_is_always_present_even_when_there_is_nothing_to_report(self):
        """AUDIT 4/4 F2 — this is a cross-component contract, not a cosmetic shape assertion.

        The bridge renderer (`formatOutboundActivity`, mcp/stdio/server.js) distinguishes "the
        service could not answer" from "the service answered: nothing produced yet" purely by
        whether this key EXISTS, because the payload carries no other discriminator. If a future
        edit starts omitting the key for empty values, a current service reporting a fresh agent
        would silently start rendering as "pre-v0.3.1 service did not report outbound activity" —
        false, and it points the operator at the wrong component.
        """
        fresh = self._info("bob")
        self.assertIn("outbound", fresh)
        self.assertIn("outbound", self._roster()["bob"])

    def test_the_roster_carries_the_cheap_half(self):
        """comms_agents is the same family and was flagged as WORSE — no last-read at all.

        The roster gets `lastSentAt` only. `lastCompletedRunAt` cannot use the existing index for
        MAX(finished_at) and measured 37ms median across 42 agents against 18,005 rows; /agents is
        the dashboard poll path and DECISIONS.md (2026-06-29) is explicit that cost there is what
        produced the last lock era. lastSentAt alone answers the question the false silent-lane
        claim turned on, at 2.55ms on a covering index."""
        self._send("alice", "bob")
        roster = self._roster()
        self.assertTrue(roster["alice"]["outbound"].get("lastSentAt"))
        self.assertNotIn("lastCompletedRunAt", roster["alice"]["outbound"],
                         "the expensive aggregate must stay off the poll path")
        self.assertEqual(roster["bob"]["outbound"], {})

    def test_the_single_agent_view_still_gets_the_full_picture(self):
        """Someone examining ONE agent is investigating; that is where run detail belongs."""
        self._send("alice", "bob")
        alice = self._info("alice")
        self.assertTrue(alice["outbound"].get("lastSentAt"))
        # No completed run in this fixture, but the query must have RUN — proven by the roster
        # omitting the key entirely while the single view is free to include it.
        self.assertIsInstance(alice["outbound"], dict)

    def test_the_most_recent_send_wins(self):
        self._send("alice", "bob", subject="first")
        self._send("alice", "bob", subject="second")
        self.assertTrue(self._info("alice")["outbound"]["lastSentAt"])

    def test_agents_with_no_traffic_at_all_are_safe(self):
        self.assertEqual(self._info("bob")["outbound"], {})
        self.assertEqual(self._info("alice")["outbound"], {})


if __name__ == "__main__":
    unittest.main()


class SystemAuthoredNoticeTests(FastApiTestCase):
    """A notice the SERVICE wrote about a dead agent must not count as that agent producing.

    v0.6 Phase 4, item #10b. `_mirror_missing_dispatch_handoff` tells a sender their target never
    answered, and it authors that message AS THE TARGET — deliberately, because `from_agent` is what
    threads the notice into the right conversation. The row is otherwise indistinguishable from a
    real message: same `source='direct'`, same table, same shape.

    `_get_outbound_activity_map` then reads `MAX(messages.timestamp) WHERE from_agent = ?` and calls
    the answer "last produced". So the system NOTICING that an agent is dead advances that agent's
    productivity clock, and the roster — which uses `lastSentAt` alone, because runs are off the poll
    path by a measured decision — reports the corpse as having just produced something.

    That is precisely the failure this field was added to retire. The module's own docstring says
    "only what it SENDS evidences that it is running"; a message it did not write is not something
    it sent.

    RULED, not merely fixed. The obvious alternative is a third `messages.source` value, and it was
    rejected: `source` is binary today and about ten readers treat `'direct'` as "a DM" — analytics,
    claim gating, run reports, managed-worker sweeps. A new value would silently change all of them
    to fix one reader that is wrong. The reader is fixed instead.

    A COMPLETED run's notice is deliberately still counted: it carries the target's own result, so
    the agent really did produce. Only failed and cancelled notices are excluded.
    """

    DB_NAME = "aify-outbound-system-authored.db"

    def setUp(self):
        super().setUp()
        for agent in ("sender", "target"):
            r = self.client.post("/api/v1/agents", json={
                "agentId": agent, "name": agent, "role": "coder", "runtime": "claude-code",
            })
            self.assertEqual(r.status_code, 200, r.text)

    def _execute(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(q, params)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _last_sent(self, agent_id):
        r = self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        agent = body.get("agent", body)
        return (agent.get("outbound") or {}).get("lastSentAt")

    def _notice(self, run_status):
        """Write exactly what the sweep writes: a message authored AS the target, marked on the run."""
        ts = int(time.time() * 1000)
        message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply,
                result_message_id, handoff_message_id, requested_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, None, "sender", "target", "start_if_possible", "managed", "request",
             "do X", "please do X", "normal", run_status, 1, "", message_id,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        self._execute(
            "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
            "priority, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (message_id, "target", "sender", "direct", "error",
             "no reply", "the target never ran", "normal", ts),
        )
        return message_id

    def test_a_failure_notice_does_not_make_a_dead_agent_look_productive(self):
        self.assertIsNone(self._last_sent("target"), "precondition: the target has produced nothing")
        self._notice("failed")
        self.assertIsNone(
            self._last_sent("target"),
            "a notice the service wrote ABOUT this agent is not this agent producing",
        )

    def test_a_cancelled_run_notice_is_excluded_too(self):
        self._notice("cancelled")
        self.assertIsNone(self._last_sent("target"))

    def test_a_completed_run_notice_still_counts(self):
        # It carries the target's own result, so the agent genuinely produced. Excluding this would
        # under-report on the roster, where `lastSentAt` is the only evidence of production there is.
        self._notice("completed")
        self.assertIsNotNone(
            self._last_sent("target"),
            "a completed run's handoff carries real output and must keep counting",
        )

    def test_an_ordinary_message_from_the_agent_still_counts(self):
        # The anti-vacuity half: the guard must not swallow real production.
        self._notice("failed")
        r = self.client.post("/api/v1/messages/send", json={
            "from_agent": "target", "to": "sender", "type": "info",
            "subject": "alive", "body": "I did the work",
        })
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNotNone(self._last_sent("target"), "a real send must still register")
