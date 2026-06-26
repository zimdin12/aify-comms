"""API tests for the usage/quota endpoints + usageSource binding (2026-06-26)."""
from service.tests._base import FastApiTestCase
from service import usage_cache as uc


class UsageApiTests(FastApiTestCase):
    def setUp(self):
        super().setUp()
        uc._USAGE_CACHE.clear()

    def test_usage_post_then_get(self):
        r = self.client.post("/api/v1/usage", json={
            "source_id": "anthropic-claude-max",
            "five_hour": {"used_pct": 10, "left_pct": 90},
            "weekly": {"used_pct": 81, "left_pct": 19},
            "severity": "warning",
            "plan_type": "max",
        })
        self.assertEqual(r.status_code, 200, r.text)
        pools = self.client.get("/api/v1/usage").json()["pools"]
        p = next(x for x in pools if x["source_id"] == "anthropic-claude-max")
        self.assertEqual(p["weekly"]["left_pct"], 19)
        self.assertEqual(p["severity"], "warning")
        self.assertIn("updated_at", p)  # server-stamped
        self.assertIn("stale", p)

    def test_usage_post_requires_source_id(self):
        r = self.client.post("/api/v1/usage", json={"weekly": {"used_pct": 1}})
        self.assertEqual(r.status_code, 400)

    def test_register_autobinds_usage_source(self):
        for rt, src in [("claude-code", "anthropic-claude-max"), ("codex", "openai-chatgpt-codex"), ("hermes", "openai-chatgpt-codex")]:
            aid = f"u-{rt}"
            r = self.client.post("/api/v1/agents", json={"agentId": aid, "role": "coder", "runtime": rt})
            self.assertEqual(r.status_code, 200, r.text)
            agent = self.client.get(f"/api/v1/agents/{aid}").json()["agent"]
            self.assertEqual(agent.get("usageSource"), src, f"{rt} -> {src}")

    def test_usage_source_override(self):
        self.client.post("/api/v1/agents", json={"agentId": "u-ov", "role": "coder", "runtime": "hermes"})
        r = self.client.patch("/api/v1/agents/u-ov/usage-source", json={"usageSource": "local-ollama"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/v1/agents/u-ov").json()["agent"]["usageSource"], "local-ollama")

    def test_consumption_roundtrip(self):
        r = self.client.post("/api/v1/usage/consumption", json={"rows": [
            {"agent_id": "a", "source_id": "anthropic-claude-max", "model": "claude-opus-4-8", "input_tokens": 100, "output_tokens": 10, "cache_tokens": 5},
            {"agent_id": "b", "source_id": "openai-chatgpt-codex", "model": "gpt-5.5", "input_tokens": 200, "output_tokens": 20, "cache_tokens": 0},
        ]})
        self.assertEqual(r.status_code, 200, r.text)
        s = self.client.get("/api/v1/usage/consumption").json()
        self.assertEqual(s["totals"]["input_tokens"], 300)
        self.assertEqual(s["by_agent"]["a"]["output_tokens"], 10)
        self.assertEqual(s["by_source"]["openai-chatgpt-codex"]["input_tokens"], 200)

    def test_agent_info_merges_pool_pct(self):
        uc.usage_set("anthropic-claude-max", {"weekly": {"used_pct": 87, "left_pct": 13}, "severity": "warning", "updated_at": "2999-01-01T00:00:00Z"})
        self.client.post("/api/v1/agents", json={"agentId": "u-cl", "role": "coder", "runtime": "claude-code"})
        agent = self.client.get("/api/v1/agents/u-cl").json()["agent"]
        self.assertEqual(agent["poolWeeklyPctLeft"], 13)
        self.assertEqual(agent["poolSeverity"], "warning")
        self.assertFalse(agent["quotaCritical"])
