"""`GET /usage` reports the time the OpenAI pool was READ, not the time it was served.

The route caches that reading for two minutes. Until 0.7.0 it stamped `updated_at` on every GET, so
the dashboard showed a two-minute-old quota as read this second (v0.7 scan A14).
"""

import asyncio
import unittest
from unittest import mock

from service.routers import usage


class UsageReportsWhenItWasReadTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(usage._OPENAI_POOL_CACHE)
        usage._OPENAI_POOL_CACHE.update(at=0.0, pool=None)
        self.addCleanup(usage._OPENAI_POOL_CACHE.update, self._saved)

    def _served(self, clock):
        async def collect():
            return {"source_id": "openai", "remaining_pct": 40}

        with mock.patch.object(usage, "collect_openai_pool", collect), \
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
        usage._OPENAI_POOL_CACHE["at"] = 0.0
        self.assertEqual(self._served("2026-09-26T10:05:00Z")["updated_at"], "2026-09-26T10:05:00Z")
