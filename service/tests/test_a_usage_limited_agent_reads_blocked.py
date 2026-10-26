"""An agent whose quota pool is at its limit reads `blocked`, with when the limit resets (0.9.9).

The operator saw usage-limited agents read `working` or `online` while no turn could run. comms already reads both
pools from the providers (`usage_pools`); the view turns a live word `blocked` while the agent's pool is at its limit,
and the note says when it resets. A stale or unknown reading changes nothing, and neither does a stop, an offline or
an unknown, which already say the agent cannot work.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from service import usage_cache, usage_pools
from service.clock import now as _now
from service.tests import env_records
from service.tests._base import FastApiTestCase

CLAUDE = usage_cache.SOURCE_ANTHROPIC_CLAUDE_MAX
CODEX = usage_cache.SOURCE_OPENAI_CHATGPT_CODEX


def _at(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pool(used: float, resets_in_hours: float = 2, **extra) -> dict:
    return {"five_hour": {"used_pct": used, "left_pct": 100 - used, "resets_at": _at(resets_in_hours)},
            "weekly": {"used_pct": 40.0, "left_pct": 60.0, "resets_at": _at(72)}, "updated_at": _now(), **extra}


class AUsageLimitedAgentReadsBlocked(FastApiTestCase):
    def setUp(self):
        super().setUp()
        saved = dict(usage_cache._USAGE_CACHE)
        usage_cache._USAGE_CACHE.clear()
        self.addCleanup(lambda: (usage_cache._USAGE_CACHE.clear(), usage_cache._USAGE_CACHE.update(saved)))

    def _agent(self, agent_id: str, runtime: str = "claude-code", **report) -> None:
        answer = self.client.post("/api/v1/agents", json={"agentId": agent_id, "role": "coder", "runtime": runtime,
                                                          "sessionMode": "managed", "machineId": env_records.TEST_HOST})
        self.assertEqual(answer.status_code, 200, answer.text)
        env_records.report(self, agent_id, **report)

    def _read(self, agent_id: str) -> tuple:
        agent = self.client.get(f"/api/v1/agents/{agent_id}").json()["agent"]
        listed = self.client.get("/api/v1/agents").json()["agents"][agent_id]
        self.assertEqual(listed["status"], agent["status"], "the roster and the agent read must agree")
        return agent["status"], agent["statusNote"]

    def test_an_agent_on_an_exhausted_pool_reads_blocked_with_its_reset(self):
        self._agent("u-idle")
        self._agent("u-busy", busy=True)
        pool = _pool(100.0)
        usage_pools.record_pool(CLAUDE, pool)
        reset = pool["five_hour"]["resets_at"]
        for agent_id in ("u-idle", "u-busy"):
            self.assertEqual(self._read(agent_id), ("blocked", f"its model usage limit is reached; resets by {reset}"))

    def test_CONTROL_a_pool_below_its_limit_changes_nothing(self):
        self._agent("u-headroom")
        usage_pools.record_pool(CLAUDE, _pool(99.0))
        self.assertEqual(self._read("u-headroom"), ("online", ""))

    def test_an_old_reading_or_a_passed_reset_is_no_evidence(self):
        self._agent("u-stale")
        ten_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        usage_pools.record_pool(CLAUDE, dict(_pool(100.0), updated_at=ten_minutes_ago))  # stale, not yet expired
        self.assertEqual(self._read("u-stale")[0], "online", "a stale reading must not block")
        self._agent("u-reset")
        usage_pools.record_pool(CLAUDE, _pool(100.0, resets_in_hours=-0.01))
        self.assertEqual(self._read("u-reset")[0], "online", "a window whose reset has passed is not exhausted")

    def test_only_the_agents_on_that_pool_are_blocked(self):
        self._agent("u-claude")
        self._agent("u-codex", runtime="codex")
        usage_pools.record_pool(CLAUDE, _pool(100.0))
        self.assertEqual((self._read("u-claude")[0], self._read("u-codex")[0]), ("blocked", "online"))

    def test_a_word_that_already_says_it_cannot_work_is_kept(self):
        answer = self.client.post("/api/v1/agents", json={"agentId": "u-none", "role": "coder", "runtime": "claude-code",
                                                          "machineId": env_records.TEST_HOST})
        self.assertEqual(answer.status_code, 200, answer.text)
        usage_pools.record_pool(CLAUDE, _pool(100.0))
        self.assertEqual(self._read("u-none")[0], "unknown", "no record stays unknown")

    def test_a_reading_that_crosses_the_limit_is_served_at_once(self):
        # The word is cached for a liveness interval; a reading that moves the agent across its limit expires it,
        # so `blocked` comes and goes with the reading rather than with the cache.
        self._agent("u-flip")
        self.assertEqual(self._read("u-flip")[0], "online")
        usage_pools.record_pool(CLAUDE, _pool(100.0))
        self.assertEqual(self._read("u-flip")[0], "blocked")
        usage_pools.record_pool(CLAUDE, _pool(10.0))
        self.assertEqual(self._read("u-flip")[0], "online")


class LimitedUntil(FastApiTestCase):
    def test_openai_says_limit_reached_for_the_account(self):
        at = _at(30)
        pool = {"five_hour": {"used_pct": None}, "weekly": {"used_pct": 80.0, "resets_at": at}, "limit_reached": True}
        self.assertEqual(usage_cache.limited_until(pool), at)
        self.assertEqual(usage_cache.limited_until({"limit_reached": True}), "", "at its limit, reset not known")
        self.assertIsNone(usage_cache.limited_until(dict(pool, limit_reached=False)))
        self.assertIsNone(usage_cache.limited_until(dict(pool, unknown=True)), "an unknown reading proves nothing")
        self.assertIsNone(usage_cache.limited_until(None))

    def test_the_latest_reset_among_exhausted_windows(self):
        later, sooner = _at(50), _at(2)
        pool = {"five_hour": {"used_pct": 100.0, "resets_at": sooner}, "weekly": {"used_pct": 100.0, "resets_at": later}}
        self.assertEqual(usage_cache.limited_until(pool), later, "usable again only once every exhausted window resets")
