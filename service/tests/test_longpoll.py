"""Unit tests for the long-poll claim helper (service/longpoll.py)."""

import asyncio
import time
import unittest

from service import longpoll


class LongpollTests(unittest.TestCase):
    def test_notify_wakes_waiter_and_re_attempts(self):
        async def _run():
            calls = {"n": 0}

            async def attempt():
                calls["n"] += 1
                # Empty on the first attempt, work on the second.
                return {"ok": True, "run": ({"id": "r2"} if calls["n"] >= 2 else None)}

            async def fire_later():
                await asyncio.sleep(0.05)
                longpoll.notify("dispatch")

            started = time.monotonic()
            task = asyncio.ensure_future(fire_later())
            result = await longpoll.longpoll(
                25000, attempt, is_empty=lambda r: r.get("run") is None,
                scope="dispatch", fallback_s=10.0,
            )
            await task
            return result, calls["n"], time.monotonic() - started

        result, n, elapsed = asyncio.run(_run())
        self.assertEqual(result["run"]["id"], "r2")
        self.assertEqual(n, 2)
        # Woke on the notify (~0.05s), not the 10s fallback.
        self.assertLess(elapsed, 2.0)

    def test_fallback_re_attempts_without_any_notify(self):
        async def _run():
            calls = {"n": 0}

            async def attempt():
                calls["n"] += 1
                return {"ok": True, "run": ({"id": "r3"} if calls["n"] >= 2 else None)}

            # No notify ever fires; the short fallback must still re-attempt.
            result = await longpoll.longpoll(
                25000, attempt, is_empty=lambda r: r.get("run") is None,
                scope="dispatch", fallback_s=0.05,
            )
            return result, calls["n"]

        result, n = asyncio.run(_run())
        self.assertEqual(result["run"]["id"], "r3")
        self.assertGreaterEqual(n, 2)

    def test_gives_up_after_wait_ms_and_returns_last_empty(self):
        async def _run():
            calls = {"n": 0}

            async def attempt():
                calls["n"] += 1
                return {"ok": True, "run": None}  # never any work

            started = time.monotonic()
            result = await longpoll.longpoll(
                120, attempt, is_empty=lambda r: r.get("run") is None,
                scope="dispatch", fallback_s=0.03,
            )
            return result, time.monotonic() - started

        result, elapsed = asyncio.run(_run())
        self.assertIsNone(result["run"])
        # Honored the ~120ms budget and stopped (not hanging forever).
        self.assertLess(elapsed, 2.0)
        self.assertGreaterEqual(elapsed, 0.1)

    def test_disconnect_stops_waiting_early(self):
        async def _run():
            async def attempt():
                return {"ok": True, "run": None}

            async def disconnected():
                return True

            started = time.monotonic()
            result = await longpoll.longpoll(
                25000, attempt, is_empty=lambda r: r.get("run") is None,
                scope="dispatch", fallback_s=10.0, is_disconnected=disconnected,
            )
            return result, time.monotonic() - started

        result, elapsed = asyncio.run(_run())
        self.assertIsNone(result["run"])
        self.assertLess(elapsed, 2.0)  # bailed on disconnect, not the 10s fallback

    def test_notify_returns_woken_count_and_no_leak(self):
        async def _run():
            async def waiter():
                await longpoll._wait_once("scopeA", 5.0)

            t1 = asyncio.ensure_future(waiter())
            t2 = asyncio.ensure_future(waiter())
            await asyncio.sleep(0.02)  # let both register
            woken = longpoll.notify("scopeA")
            await asyncio.gather(t1, t2)
            return woken

        woken = asyncio.run(_run())
        self.assertEqual(woken, 2)
        # Waiter set is cleaned up after both resolve.
        self.assertNotIn("scopeA", longpoll._waiters)


if __name__ == "__main__":
    unittest.main()
