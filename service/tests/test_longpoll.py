"""Unit tests for the long-poll claim helper (service/longpoll.py)."""

import asyncio
import time
import unittest

from service import longpoll


class LongpollTests(unittest.TestCase):
    def test_returns_immediately_when_first_attempt_is_non_empty(self):
        async def _run():
            calls = []

            async def attempt():
                calls.append(1)
                return {"ok": True, "run": {"id": "r1"}}

            result = await longpoll.longpoll(
                25000, attempt, is_empty=lambda r: r.get("run") is None
            )
            return result, calls

        result, calls = asyncio.run(_run())
        self.assertEqual(result["run"]["id"], "r1")
        self.assertEqual(len(calls), 1)  # no waiting, single attempt

    def test_wait_ms_zero_is_legacy_single_attempt(self):
        async def _run():
            calls = []

            async def attempt():
                calls.append(1)
                return {"ok": True, "run": None}

            result = await longpoll.longpoll(
                0, attempt, is_empty=lambda r: r.get("run") is None
            )
            return result, calls

        result, calls = asyncio.run(_run())
        self.assertIsNone(result["run"])
        self.assertEqual(len(calls), 1)  # wait_ms=0 -> behaves exactly like today

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

    def test_lock_error_becomes_empty_result_not_a_raise(self):
        async def _run():
            async def attempt():
                raise Exception("database is locked")
            # With lock_result set, a lock contention yields the empty shape (no 503),
            # and the short budget elapses without raising.
            return await longpoll.longpoll(
                80, attempt, is_empty=lambda r: r.get("run") is None,
                scope="dispatch", fallback_s=0.02,
                lock_result={"ok": True, "run": None},
            )

        result = asyncio.run(_run())
        self.assertEqual(result, {"ok": True, "run": None})

    def test_non_lock_error_still_raises(self):
        async def _run():
            async def attempt():
                raise ValueError("boom")
            return await longpoll.longpoll(
                0, attempt, is_empty=lambda r: True,
                lock_result={"ok": True, "run": None},
            )

        with self.assertRaises(ValueError):
            asyncio.run(_run())

    def test_lock_without_lock_result_propagates(self):
        async def _run():
            async def attempt():
                raise Exception("database is locked")
            return await longpoll.longpoll(0, attempt, is_empty=lambda r: True)

        with self.assertRaises(Exception):
            asyncio.run(_run())

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
