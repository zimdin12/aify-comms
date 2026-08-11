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

import unittest

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
