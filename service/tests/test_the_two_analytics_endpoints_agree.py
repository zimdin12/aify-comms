"""`/analytics` and `/analytics/pulse` count the live fleet the same way.

MEASURED AGAINST THE LIVE SERVICE, and they did not:

    GET /api/v1/analytics        onlineAgents = 30
    GET /api/v1/analytics/pulse  onlineAgents = 27

on a fleet of 47 whose statuses were offline 17, available 20, online 6, working 1, stopped 3. The
difference is exactly the stopped agents. Two endpoints in one router, disagreeing about one number.

THE CAUSE. `/analytics` counted with `not status.startswith("offline") and not
status.startswith("stale")`. It does not exclude `stopped`, and it does not exclude `misconfigured`
-- an agent the contract defines as one that can never start. It DOES exclude `stale`, a status this
engine stopped producing: the vocabulary's own comment reads "Proof-based: no time-decay states, no
`idle`, no `stale`". So one half of the condition guarded against something that cannot arrive while
the other half let through two things that should not.

IT IS NOT ONLY A HEADLINE. `online_agents` is the denominator of fleet utilization on this endpoint
too, so operator-stopped agents were dragging down the percentage that says how hard the fleet is
working.

Both now count through `is_live_agent_status`, the partition declared in `status_engine` when the
same inline rule was replaced in `analytics_series.py`.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import aiosqlite

from service.tests._base import FastApiTestCase


class TheTwoAnalyticsEndpointsAgree(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    #: The two reads the prefetch answers once for a whole loop.
    BATCHED_TABLES = ("FROM agent_status_state", "FROM agent_console_signal")

    def setUp(self):
        super().setUp()
        heartbeat = self.client.post("/api/v1/environments/heartbeat", json={
            "id": "linux:an-host:default", "machineId": "linux:an-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-an", "cwdRoots": ["/workspace"],
            "runtimes": [{"runtime": "claude-code", "modes": ["managed-warm"], "capabilities": {}}],
            "metadata": {},
        })
        self.assertEqual(heartbeat.status_code, 200, heartbeat.text)

    def _register(self, agent_id, status=None):
        body = {
            "agentId": agent_id, "role": "coder", "runtime": "claude-code",
            "sessionMode": "managed", "machineId": "linux:an-host", "bridgeId": "bridge-an",
            "capabilities": ["managed-run"],
        }
        if status:
            body["status"] = status
        response = self.client.post("/api/v1/agents", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    @staticmethod
    def _clear_cache():
        from service.reconcilers import status_cache
        status_cache._LIVE_STATE_CACHE.clear()

    def _online(self, path):
        self._clear_cache()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json().get("onlineAgents")

    def test_the_two_endpoints_report_the_SAME_live_count(self):
        for i in range(4):
            self._register(f"an-live-{i}")
        self.assertEqual(
            self._online("/api/v1/analytics"),
            self._online("/api/v1/analytics/pulse?range=1h"),
            "the two analytics endpoints disagree about how many agents are live",
        )

    def test_a_STOPPED_agent_is_counted_by_neither(self):
        """The case the live fleet exposed. `stopped` is operator-disabled: an agent the operator
        deliberately took out of service must not be counted among those doing work, and must not sit
        in the utilization denominator."""
        for i in range(3):
            self._register(f"an-live-{i}")
        before = self._online("/api/v1/analytics")
        self._register("an-stopped", status="stopped")
        after = self._online("/api/v1/analytics")
        self.assertEqual(after, before, "a stopped agent was counted among the live fleet")
        self.assertEqual(after, self._online("/api/v1/analytics/pulse?range=1h"))

    def test_the_count_is_not_simply_zero(self):
        """ANTI-VACUITY. Two endpoints that both counted nothing would agree perfectly, and a stopped
        agent would change nothing either."""
        for i in range(4):
            self._register(f"an-live-{i}")
        self.assertEqual(self._online("/api/v1/analytics"), 4)

    def test_the_batched_tables_are_read_once_for_the_whole_loop(self):
        """The other half of the same fix: this loop threaded three of four batch parameters and
        omitted `status_signals`, so it paid both reads per agent. Measured before: 7.0 round-trips
        per agent (123 at 6 agents, 165 at 12, 249 at 24). After: 5.0."""
        for i in range(6):
            self._register(f"an-live-{i}")
        self._clear_cache()

        calls = []
        orig = aiosqlite.Connection.execute

        async def spy(self, sql, *a, **k):
            calls.append(" ".join(str(sql).split()))
            return await orig(self, sql, *a, **k)

        aiosqlite.Connection.execute = spy
        try:
            response = self.client.get("/api/v1/analytics")
        finally:
            aiosqlite.Connection.execute = orig
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(calls, "POSITIVE CONTROL: the endpoint made zero round-trips")
        for table in self.BATCHED_TABLES:
            hits = [c for c in calls if table in c]
            self.assertEqual(
                len(hits), 1,
                f"{table} was read {len(hits)} times for 6 agents; it must be one batched read",
            )

    def test_the_retired_stale_status_is_not_what_decides_this(self):
        """The dead half of the old condition. `stale` is not in the vocabulary -- the contract says
        "no time-decay states, no `idle`, no `stale`" -- so excluding it guarded nothing, while
        `stopped` and `misconfigured` went uncounted-for."""
        from service.api_core.vocabulary import AGENT_STATUSES

        self.assertNotIn("stale", AGENT_STATUSES)
        self.assertIn("stopped", AGENT_STATUSES)
        self.assertIn("misconfigured", AGENT_STATUSES)


if __name__ == "__main__":
    unittest.main()
