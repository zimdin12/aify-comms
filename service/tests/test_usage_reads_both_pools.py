"""The service reads the Anthropic pool itself, once per `usage_poll_minutes`, like the OpenAI one.

Nothing collected the Anthropic pool after the environment bridge was deleted in v0.6.3, so it read
`?` or a stale figure. The operator asked for both pools with no per-agent cost and no runtime started
to ask (v0.7.4): one service-side read, cached, serves every agent. No test here contacts a provider.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from service import usage_anthropic
from service.routers import usage

# The shape `api/oauth/usage` returned when read live on 2026-06-26 (memory: usage-quota-sources).
PAYLOAD = {
    "five_hour": {"utilization": 10.0, "resets_at": "2026-09-26T15:00:00Z"},
    "seven_day": {"utilization": 81.0, "resets_at": "2026-10-02T20:00:00Z"},
    "limits": [{"kind": "weekly_all", "group": "weekly", "percent": 81, "severity": "warning", "is_active": True}],
}


class TheAnthropicPoolIsNormalisedTests(unittest.TestCase):
    def test_used_left_and_resets_are_carried(self):
        pool = usage_anthropic.build_pool(PAYLOAD, {"subscriptionType": "max"})
        self.assertEqual(pool["source_id"], "anthropic-claude-max")
        self.assertEqual(pool["five_hour"], {"used_pct": 10.0, "left_pct": 90.0, "resets_at": "2026-09-26T15:00:00Z"})
        self.assertEqual(pool["weekly"]["left_pct"], 19.0)
        self.assertEqual(pool["plan_type"], "max")
        self.assertTrue(pool["verified"])

    def test_the_providers_warning_raises_the_severity(self):
        self.assertEqual(usage_anthropic.build_pool(PAYLOAD, {})["severity"], "warning", "81% alone is normal")

    def test_an_absent_window_is_unknown_never_zero(self):
        pool = usage_anthropic.build_pool({"seven_day": {"utilization": 20}}, {})
        self.assertEqual(pool["five_hour"]["used_pct"], None)
        self.assertEqual(usage_anthropic.build_pool({}, {})["unknown"], True)

    def test_no_credentials_file_means_unknown_and_no_request(self):
        empty = tempfile.mkdtemp()
        with mock.patch.object(usage_anthropic, "_credential_candidates", lambda: [Path(empty) / ".credentials.json"]), \
                mock.patch.object(usage_anthropic.httpx, "AsyncClient", side_effect=AssertionError("a request was made")):
            self.assertIsNone(asyncio.run(usage_anthropic.collect_anthropic_pool()))

    def test_the_token_is_read_from_the_mounted_claude_credentials(self):
        home = Path(tempfile.mkdtemp())
        (home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "subscriptionType": "max"}}))
        with mock.patch.object(usage_anthropic, "_credential_candidates", lambda: [home / ".credentials.json"]):
            self.assertEqual(usage_anthropic.read_claude_oauth()["accessToken"], "tok")


class BothPoolsAreReadOncePerIntervalTests(unittest.TestCase):
    def setUp(self):
        saved = {name: dict(entry) for name, entry in usage._POOL_CACHE.items()}
        for entry in usage._POOL_CACHE.values():
            entry.update(at=0.0, pool=None)
        self.addCleanup(lambda: [usage._POOL_CACHE[n].update(e) for n, e in saved.items()])
        self.calls = {"openai": 0, "anthropic": 0}

    def _get(self):
        async def openai():
            self.calls["openai"] += 1
            return {"source_id": "openai-chatgpt-codex"}

        async def anthropic():
            self.calls["anthropic"] += 1
            return usage_anthropic.build_pool(PAYLOAD, {})

        async def five_minutes():
            return 300.0

        with mock.patch.object(usage, "collect_openai_pool", openai), \
                mock.patch.object(usage, "collect_anthropic_pool", anthropic), \
                mock.patch.object(usage, "_poll_seconds", five_minutes), \
                mock.patch.object(usage, "usage_all", lambda: []):
            return {p["source_id"] for p in asyncio.run(usage.get_usage())["pools"]}

    def test_both_pools_are_served(self):
        self.assertEqual(self._get(), {"openai-chatgpt-codex", "anthropic-claude-max"})

    def test_many_reads_inside_the_interval_ask_each_provider_once(self):
        for _ in range(5):
            self._get()
        self.assertEqual(self.calls, {"openai": 1, "anthropic": 1})

    def test_control_an_expired_interval_asks_again(self):
        self._get()
        usage._POOL_CACHE["anthropic"]["at"] = 0.0
        self._get()
        self.assertEqual(self.calls["anthropic"], 2)


class SimultaneousReadersShareOnePollTests(unittest.TestCase):
    """Readers that arrive while a poll is in flight wait for it rather than starting their own
    (review of 28eb72a0: two concurrent GETs after expiry asked the provider twice)."""

    def setUp(self):
        saved = {name: dict(entry) for name, entry in usage._POOL_CACHE.items()}
        for entry in usage._POOL_CACHE.values():
            entry.update(at=0.0, pool=None)
        self.addCleanup(lambda: [usage._POOL_CACHE[n].update(e) for n, e in saved.items()])
        self.calls = 0

    def _gather(self, anthropic, readers=3):
        async def openai():
            return None

        async def five_minutes():
            return 300.0

        async def run():
            return await asyncio.gather(*(usage.get_usage() for _ in range(readers)))

        with mock.patch.object(usage, "collect_openai_pool", openai), \
                mock.patch.object(usage, "collect_anthropic_pool", anthropic), \
                mock.patch.object(usage, "_poll_seconds", five_minutes), \
                mock.patch.object(usage, "usage_all", lambda: [{"source_id": "anthropic-claude-max", "posted": True}]):
            return asyncio.run(run())

    def test_simultaneous_readers_ask_the_provider_once_and_all_get_the_reading(self):
        async def slow():
            self.calls += 1
            await asyncio.sleep(0.05)
            return usage_anthropic.build_pool(PAYLOAD, {"subscriptionType": "max"})

        responses = self._gather(slow)
        self.assertEqual(self.calls, 1)
        for response in responses:
            pool = next(p for p in response["pools"] if p["source_id"] == "anthropic-claude-max")
            self.assertEqual(pool["weekly"]["left_pct"], 19.0, "a waiting reader did not get the fresh reading")

    def test_a_reader_that_goes_away_does_not_cancel_the_poll_the_others_wait_on(self):
        async def slow():
            self.calls += 1
            await asyncio.sleep(0.05)
            return usage_anthropic.build_pool(PAYLOAD, {"subscriptionType": "max"})

        async def openai():
            return None

        async def five_minutes():
            return 300.0

        async def run():
            first = asyncio.ensure_future(usage.get_usage())
            await asyncio.sleep(0.01)
            second = asyncio.ensure_future(usage.get_usage())
            await asyncio.sleep(0.01)
            first.cancel()
            return await second

        with mock.patch.object(usage, "collect_openai_pool", openai), \
                mock.patch.object(usage, "collect_anthropic_pool", slow), \
                mock.patch.object(usage, "_poll_seconds", five_minutes), \
                mock.patch.object(usage, "usage_all", lambda: []):
            response = asyncio.run(run())
        self.assertEqual([p["source_id"] for p in response["pools"]], ["anthropic-claude-max"])
        self.assertEqual(self.calls, 1)

    def test_a_failed_poll_keeps_the_posted_pool_for_every_waiter_and_is_retried(self):
        async def failing():
            self.calls += 1
            await asyncio.sleep(0.05)
            raise RuntimeError("provider down")

        for response in self._gather(failing):
            self.assertEqual(response["pools"], [{"source_id": "anthropic-claude-max", "posted": True}])
        self.assertEqual(self.calls, 1)
        self._gather(failing, readers=1)
        self.assertEqual(self.calls, 2, "a failed poll must not be cached as a reading")

    def test_a_provider_that_answers_nothing_is_asked_once_per_interval(self):
        """How both collectors report a refused or unreachable provider: None, cached like a reading."""
        async def refused():
            self.calls += 1
            return None

        for response in self._gather(refused) + self._gather(refused, readers=1):
            self.assertEqual(response["pools"], [{"source_id": "anthropic-claude-max", "posted": True}])
        self.assertEqual(self.calls, 1)


class ThePollIntervalIsTheSettingTests(unittest.TestCase):
    def test_the_setting_is_declared_in_minutes_with_a_five_minute_default(self):
        from service.api_core.settings_spec import BY_KEY

        setting = BY_KEY["usage_poll_minutes"]
        self.assertEqual((setting.default, setting.unit, setting.min), (5, "min", 1))

    def test_the_interval_is_read_from_settings_in_seconds(self):
        async def settings(_db):
            return {"usage_poll_minutes": 7}

        class _Db:
            async def close(self):
                pass

        async def db():
            return _Db()

        with mock.patch.object(usage, "get_db", db), mock.patch.object(usage, "_load_settings", settings):
            self.assertEqual(asyncio.run(usage._poll_seconds()), 420.0)


class NoTestReadsARealLoginTests(unittest.TestCase):
    """The suite-wide seal in conftest.py: without it, a test fetching /usage sent this host's real
    Claude or Codex token to the provider (found in v0.7.4)."""

    def test_neither_credential_search_finds_anything_under_test(self):
        from service import usage_openai

        self.assertEqual(usage_openai.read_openai_token()[0], "")
        self.assertEqual(usage_anthropic.read_claude_oauth(), {})

    def test_a_usage_read_under_test_makes_no_request(self):
        for entry in usage._POOL_CACHE.values():
            entry.update(at=0.0, pool=None)
        asked = []

        def client(*args, **kwargs):
            asked.append(kwargs)  # the collectors swallow errors, so the attempt is what is recorded
            raise RuntimeError("no network under test")

        with mock.patch("httpx.AsyncClient", side_effect=client), mock.patch.object(usage, "usage_all", lambda: []):
            asyncio.run(usage.get_usage())
        self.assertEqual(asked, [], "a provider was asked for usage from a test")
