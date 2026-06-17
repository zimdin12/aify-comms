"""Per-agent chat analytics endpoint (additive): GET /api/v1/analytics/agent/{agent_id}.

Returns, for a single agent's DIRECT messages:
  - messageTotal              total direct messages to/from the agent
  - messagesPerHourOfDay      24 buckets keyed on hour-of-day (UTC), via
                              strftime('%H', datetime(timestamp/1000,'unixepoch'))
  - byPeer                    direct message counts grouped by the OTHER party
  - workingMinutes            SUM over dispatch_runs where this agent is the
                              target, computed with julianday() (NOT epoch math —
                              messages.timestamp is epoch-ms INT but
                              dispatch_runs.*_at are ISO TEXT), NULL-guarded,
                              clamped >= 0.

This is purely additive; the existing GET /analytics is untouched.
"""

import sqlite3

from service.tests._base import FastApiTestCase


class ChatAnalyticsTests(FastApiTestCase):
    def _register(self, agent_id, **extra):
        payload = {"agentId": agent_id, "role": "coder"}
        payload.update(extra)
        resp = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)

    def _seed(self, target):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            # A real environment row first (FK-enforced tables in production
            # always have a live environment); mirrors the sibling seeding test.
            env_id = f"env_{target}"
            now_iso = "2099-01-01T00:00:00Z"
            conn.execute(
                "INSERT INTO environments (id, machine_id, bridge_id, registered_at, last_seen) VALUES (?,?,?,?,?)",
                (env_id, "test-host", f"bridge_{target}", now_iso, now_iso),
            )

            # Direct messages between `target` and two peers, at known epoch-ms
            # timestamps. 2021-06-07 01:00:00 UTC == 1623027600000 (hour '01').
            base_ms = 1623027600000  # 2021-06-07T01:00:00Z
            hour_ms = 3600 * 1000
            rows = [
                # (id, from, to, ts)
                ("m1", target, "peerA", base_ms),               # hour 01
                ("m2", "peerA", target, base_ms + 60_000),      # hour 01
                ("m3", target, "peerB", base_ms + hour_ms),     # hour 02
                ("m4", "peerB", target, base_ms + 2 * hour_ms), # hour 03
            ]
            for mid, frm, to, ts in rows:
                conn.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
                    "priority, dispatch_requested, in_reply_to, timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, frm, to, "direct", "info", "", "", "normal", 0, None, ts),
                )

            # A channel message that must NOT count toward the direct total.
            conn.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
                "priority, dispatch_requested, in_reply_to, timestamp) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("c1", target, None, "channel", "info", "", "", "normal", 0, None, base_ms),
            )

            # A dispatch_runs row targeting the agent: 30 minutes of work.
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, "
                "requested_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                ("run1", None, "peerA", target, "completed",
                 "2021-06-07T01:00:00Z", "2021-06-07T01:00:00Z", "2021-06-07T01:30:00Z"),
            )
            # A run with NULL finished_at must be skipped (NULL guard), not error.
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, "
                "requested_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                ("run2", None, "peerA", target, "running",
                 "2021-06-07T02:00:00Z", "2021-06-07T02:00:00Z", None),
            )
            conn.commit()
        finally:
            conn.close()

    def test_agent_analytics_shape(self):
        self._register("ca-agent")
        self._seed("ca-agent")

        resp = self.client.get("/api/v1/analytics/agent/ca-agent")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()

        # 4 direct messages, channel message excluded.
        self.assertEqual(data["messageTotal"], 4)

        # 24 hour-of-day buckets.
        self.assertEqual(len(data["messagesPerHourOfDay"]), 24)
        by_hour = {b["hour"]: b["count"] for b in data["messagesPerHourOfDay"]}
        self.assertEqual(by_hour[1], 2)  # m1, m2
        self.assertEqual(by_hour[2], 1)  # m3
        self.assertEqual(by_hour[3], 1)  # m4
        self.assertEqual(sum(by_hour.values()), 4)

        # byPeer counts grouped by the OTHER party.
        peers = {p["peer"]: p["count"] for p in data["byPeer"]}
        self.assertEqual(peers.get("peerA"), 2)
        self.assertEqual(peers.get("peerB"), 2)

        # workingMinutes: 30 from run1; run2 (NULL finished_at) skipped.
        self.assertAlmostEqual(data["workingMinutes"], 30.0, places=3)

    def test_agent_analytics_revamp_fields(self):
        """2026-06-12 revamp: sent/received split, 14-day dailyActivity, runs7d,
        reply latency, and openContracts. Old fields stay (back-compat)."""
        self._register("ca-rev")
        conn = sqlite3.connect(str(self._db_path))
        try:
            import time as _t
            now_ms = int(_t.time() * 1000)
            for mid, frm, to in (("r1", "ca-rev", "peerA"), ("r2", "peerA", "ca-rev"), ("r3", "peerA", "ca-rev")):
                conn.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, "
                    "priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, frm, to, "direct", "info", "", "", "normal", 0, None, now_ms),
                )
            from datetime import datetime, timezone, timedelta
            t0 = datetime.now(timezone.utc) - timedelta(hours=1)
            iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
            # Completed rr=1 run: 10 min request→finish (reply latency), 6 min started→finish.
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, require_reply, "
                "result_message_id, requested_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("rr1", None, "peerA", "ca-rev", "completed", 1, "msg-x",
                 iso(t0), iso(t0 + timedelta(minutes=4)), iso(t0 + timedelta(minutes=10))),
            )
            # Failed run + an OPEN rr=1 contract (delivered, no reply yet).
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, require_reply, "
                "requested_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                ("rr2", None, "peerA", "ca-rev", "failed", 0, iso(t0), iso(t0 + timedelta(minutes=1))),
            )
            conn.execute(
                "INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, status, require_reply, "
                "requested_at) VALUES (?,?,?,?,?,?,?)",
                ("rr3", None, "peerA", "ca-rev", "delivered", 1, iso(t0)),
            )
            conn.commit()
        finally:
            conn.close()
        data = self.client.get("/api/v1/analytics/agent/ca-rev").json()
        self.assertEqual(data["messagesSent"], 1)
        self.assertEqual(data["messagesReceived"], 2)
        self.assertEqual(len(data["dailyActivity"]), 14)
        today = data["dailyActivity"][-1]
        self.assertEqual(today["sent"], 1)
        self.assertEqual(today["received"], 2)
        self.assertEqual(data["runs7d"]["completed"], 1)
        self.assertEqual(data["runs7d"]["failed"], 1)
        self.assertEqual(data["runs7d"]["open"], 1)
        self.assertAlmostEqual(data["avgRunMinutes7d"], 6.0, places=1)
        self.assertAlmostEqual(data["medianReplyMinutes7d"], 10.0, places=1)
        self.assertEqual(data["openContracts"], 1)

    def test_fleet_analytics_operational_fields(self):
        """2026-06-17 round: GET /analytics gains fleet operational metrics —
        success rate, open/overdue reply contracts, fleet median reply, dispatch
        outcomes over time, agent leaderboard, busiest channels, failure reasons."""
        self._register("fa-alpha")
        self._register("fa-beta")
        conn = sqlite3.connect(str(self._db_path))
        try:
            from datetime import datetime, timezone, timedelta
            now = datetime.now(timezone.utc)
            iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
            now_ms = int(now.timestamp() * 1000)
            # Channel traffic (busiest channels).
            for mid, chan in (("ch1", "general"), ("ch2", "general"), ("ch3", "ops")):
                conn.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, "
                    "priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (mid, "fa-alpha", None, chan, "channel", "info", "", "", "normal", 0, None, now_ms),
                )
            # Runs: 2 completed (one rr=1 with 10-min reply), 1 failed w/ error text, 1 overdue contract.
            t0 = now - timedelta(hours=2)
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, result_message_id, "
                "requested_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("fr1", "fa-beta", "fa-alpha", "completed", 1, "rmsg", iso(t0), iso(t0 + timedelta(minutes=2)), iso(t0 + timedelta(minutes=10))),
            )
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, "
                "requested_at, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                ("fr2", "fa-beta", "fa-alpha", "completed", 0, iso(t0), iso(t0), iso(t0 + timedelta(minutes=3))),
            )
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, error_text, "
                "requested_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
                ("fr3", "fa-beta", "fa-beta", "failed", 0, "timeout waiting for worker", iso(t0), iso(t0 + timedelta(minutes=1))),
            )
            # Overdue open contract: rr=1, delivered, no reply, requested > 30 min ago.
            conn.execute(
                "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, require_reply, "
                "requested_at) VALUES (?,?,?,?,?,?)",
                ("fr4", "fa-beta", "fa-alpha", "delivered", 1, iso(now - timedelta(hours=1))),
            )
            conn.commit()
        finally:
            conn.close()

        data = self.client.get("/api/v1/analytics?range=all").json()
        self.assertTrue(data["ok"])
        # success rate = 2 completed / (2 completed + 1 failed) = 66.7%
        self.assertEqual(data["runsCompleted"], 2)
        self.assertEqual(data["runsFailed"], 1)
        self.assertAlmostEqual(data["successRate"], 66.7, places=1)
        # one overdue open reply contract (fr4).
        self.assertEqual(data["openReplyContracts"], 1)
        self.assertEqual(data["overdueReplyContracts"], 1)
        # fleet median reply over completed rr=1 runs = 10 minutes (fr1 only).
        self.assertAlmostEqual(data["fleetMedianReplyMinutes"], 10.0, places=1)
        # dispatch outcomes: 14 zero-filled days, today has 2 completed + 1 failed.
        self.assertEqual(len(data["dispatchOutcomes"]), 14)
        self.assertEqual(data["dispatchOutcomes"][-1]["completed"], 2)
        self.assertEqual(data["dispatchOutcomes"][-1]["failed"], 1)
        # leaderboard: fa-alpha has 2 completed.
        leaders = {r["agent"]: r for r in data["agentLeaderboard"]}
        self.assertEqual(leaders["fa-alpha"]["completed"], 2)
        self.assertEqual(leaders["fa-alpha"]["successRate"], 100.0)
        # busiest channels: general (2) before ops (1).
        chans = data["busiestChannels"]
        self.assertEqual(chans[0]["channel"], "general")
        self.assertEqual(chans[0]["count"], 2)
        # failure reasons surface the error text.
        reasons = {r["reason"]: r["count"] for r in data["failureReasons"]}
        self.assertEqual(reasons.get("timeout waiting for worker"), 1)

    def test_agent_analytics_empty_agent(self):
        # An agent with no messages/runs must return a valid zeroed shape,
        # not 500.
        self._register("ca-empty")
        resp = self.client.get("/api/v1/analytics/agent/ca-empty")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["messageTotal"], 0)
        self.assertEqual(len(data["messagesPerHourOfDay"]), 24)
        self.assertEqual(data["byPeer"], [])
        self.assertEqual(data["workingMinutes"], 0)
