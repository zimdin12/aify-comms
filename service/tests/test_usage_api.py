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
