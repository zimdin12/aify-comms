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
