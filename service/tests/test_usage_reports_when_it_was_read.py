"""`GET /usage` reports the time each pool was READ, not the time it was served.

The route caches each reading for `usage_poll_minutes`. Until 0.7.0 it stamped `updated_at` on every
GET, so the dashboard showed an old quota as read this second (v0.7 scan A14).
"""

import asyncio
import unittest
from unittest import mock

from service.routers import usage


class UsageReportsWhenItWasReadTests(unittest.TestCase):
    def setUp(self):
        saved = {name: dict(entry) for name, entry in usage._POOL_CACHE.items()}
        for entry in usage._POOL_CACHE.values():
            entry.update(at=0.0, pool=None)
        self.addCleanup(lambda: [usage._POOL_CACHE[n].update(e) for n, e in saved.items()])

    def _served(self, clock):
        async def collect():
            return {"source_id": "openai", "remaining_pct": 40}

        async def nothing():
            return None  # never the real Anthropic endpoint from a test

        async def five_minutes():
            return 300.0

        with mock.patch.object(usage, "collect_openai_pool", collect), \
                mock.patch.object(usage, "collect_anthropic_pool", nothing), \
                mock.patch.object(usage, "_poll_seconds", five_minutes), \
                mock.patch.object(usage, "usage_all", lambda: []), \
                mock.patch.object(usage, "_now", lambda: clock):
            pools = asyncio.run(usage.get_usage())["pools"]
        return next(p for p in pools if p["source_id"] == "openai")

    def test_a_cached_reading_keeps_the_time_it_was_read(self):
        self.assertEqual(self._served("2026-09-26T10:00:00Z")["updated_at"], "2026-09-26T10:00:00Z")
        self.assertEqual(self._served("2026-09-26T10:01:30Z")["updated_at"], "2026-09-26T10:00:00Z",
                         "a reading served from the cache was re-stamped as fresh")

    def test_control_an_expired_cache_is_read_again_and_restamped(self):
        self._served("2026-09-26T10:00:00Z")
        usage._POOL_CACHE["openai"]["at"] = 0.0
        self.assertEqual(self._served("2026-09-26T10:05:00Z")["updated_at"], "2026-09-26T10:05:00Z")
