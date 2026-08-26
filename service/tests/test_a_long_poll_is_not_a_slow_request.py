"""`SLOW-REQ` measures time spent WORKING, not time spent deliberately waiting.

MEASURED ON THE OPERATOR'S LIVE SERVICE, six hours of container logs: **14,062 SLOW-REQ lines, of
which 10,587 (75.3%) were `/claim` long-polls.** `/api/v1/environments/controls/claim` produced 1,020
of them with a MINIMUM duration of 20,002ms -- not one was a genuine slow request, because a long poll
holds the connection open on purpose and returns at its own wait budget.

WHAT THAT COST. The debug skill sends an operator to this log ("Diagnostic middleware in
`service/main.py` logs `SLOW-REQ`/`DB-LOCK`/5xx if you need to re-confirm"), and the lines that
matter were buried three-to-one: `/api/v1/agents` reaching 5,578ms and `/api/v1/spawn-requests`
reaching 7,076ms, both invisible under ten thousand polls behaving exactly as designed. A diagnostic
nobody can read is not a diagnostic.

NO PATH LIST, because no path list could be right. The wait budget is per-REQUEST -- `waitMs` in the
body, capped by `MAX_WAIT_S` -- so the same endpoint is a 0ms immediate return for one caller and a
20-second hold for another. The waiting reports ITSELF, and what remains is work.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.longpoll import attributable_ms, begin_wait_accounting, longpoll, note_waited
from service.main import RequestTimingMiddleware


class AttributableMsTests(unittest.TestCase):
    """The threshold decision, pure, so it needs no server to fail."""

    def test_a_request_that_never_waited_is_measured_whole(self):
        holder = {"ms": 0.0}
        self.assertEqual(attributable_ms(1500, holder), 1500)

    def test_the_wait_is_subtracted(self):
        # The shape of every line this change removes: 20,014ms wall, 20,000 of it asleep.
        holder = {"ms": 20000.0}
        self.assertEqual(attributable_ms(20014, holder), 14)

    def test_a_slow_claim_still_reads_slow_UNDER_its_own_wait(self):
        """The case this must not hide. A claim that waits its full budget AND takes two seconds to
        execute is a genuine slow request, and subtracting the wait has to leave that visible --
        otherwise this change trades one blind spot for a better-hidden one."""
        holder = {"ms": 20000.0}
        self.assertGreaterEqual(attributable_ms(22100, holder), 2000)

    def test_no_holder_measures_the_whole_request(self):
        """Fail towards REPORTING. A request whose accounting never started is not evidence that it
        did no waiting, but under-reporting a slow request is the failure that matters here: a noisy
        log can be filtered, a missing line cannot be recovered."""
        self.assertEqual(attributable_ms(1500, None), 1500)
        self.assertEqual(attributable_ms(1500, {}), 1500)

    def test_a_holder_that_out_counts_the_request_clamps_at_zero(self):
        """Only reachable through an accounting bug, and a negative duration would read as a very
        fast request -- the opposite of the fault it represents."""
        self.assertEqual(attributable_ms(100, {"ms": 5000.0}), 0)

    def test_a_junk_holder_does_not_throw(self):
        """This runs in a middleware on every request. A crash here takes every response with it."""
        for holder in ({"ms": None}, {"ms": ""}, {"other": 1}):
            self.assertEqual(attributable_ms(1200, holder), 1200)


class WaitAccountingTests(unittest.TestCase):
    def test_note_waited_accumulates_into_the_holder(self):
        async def run():
            holder = begin_wait_accounting()
            note_waited(0.5)
            note_waited(1.5)
            return holder
        holder = asyncio.run(run())
        self.assertAlmostEqual(holder["ms"], 2000.0, places=3)

    def test_note_waited_outside_a_request_is_a_no_op(self):
        """The helper is importable from anywhere; a caller with no accounting started must not
        raise, and must not invent a holder that nobody reads."""
        note_waited(1.0)   # no begin_wait_accounting() in this context

    def test_a_negative_wait_contributes_nothing(self):
        async def run():
            holder = begin_wait_accounting()
            note_waited(-5.0)
            return holder
        self.assertEqual(asyncio.run(run())["ms"], 0.0)

    def test_the_holder_survives_the_middleware_TASK_BOUNDARY(self):
        """THE REASON THIS IS A MUTABLE HOLDER AND NOT A ContextVar VALUE.

        Starlette's BaseHTTPMiddleware runs the downstream app in its own task, and a task COPIES the
        context -- so a value `set()` below the middleware is invisible above it. The copy shares the
        holder's reference, so mutation propagates. This reproduces that exact boundary: the writes
        happen in a child task and the parent must see them.
        """
        async def run():
            holder = begin_wait_accounting()

            async def downstream():
                note_waited(2.0)

            await asyncio.create_task(downstream())
            return holder

        self.assertAlmostEqual(asyncio.run(run())["ms"], 2000.0, places=3)

    def test_a_child_task_REBINDING_the_var_does_not_reach_the_parent(self):
        """The failure mode the holder avoids, asserted so the reason is not merely claimed.

        If accounting were kept as a plain value, a downstream `set()` would be lost at this same
        boundary -- and the middleware would read zero waiting for every long poll, which is the
        behaviour being fixed.
        """
        probe: contextvars.ContextVar = contextvars.ContextVar("probe", default=0)

        async def run():
            probe.set(1)

            async def downstream():
                probe.set(99)

            await asyncio.create_task(downstream())
            return probe.get()

        self.assertEqual(asyncio.run(run()), 1, "a child task's set() reached the parent after all")

    def test_two_requests_do_not_share_a_holder(self):
        """Each request starts its own accounting. A shared holder would attribute one request's
        waiting to the next one's work, which is the same lie in the other direction."""
        async def run():
            first = begin_wait_accounting()
            note_waited(1.0)
            second = begin_wait_accounting()
            note_waited(3.0)
            return first, second
        first, second = asyncio.run(run())
        self.assertAlmostEqual(first["ms"], 1000.0, places=3)
        self.assertAlmostEqual(second["ms"], 3000.0, places=3)

def _busy(seconds: float) -> None:
    """Occupy wall clock WITHOUT sleeping, so the time is work by every definition."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


#: Short enough to keep the suite quick, long enough to clear the 1000ms threshold unambiguously.
WORK_S = 1.2
#: A stand-in for a claim's wait. Real ones are ~20s; the accounting does not care about the size.
WAIT_S = 0.4


def _app() -> FastAPI:
    """A tiny app carrying ONLY the timing middleware.

    NOT `create_app()`, deliberately. That opens a real database at a config-derived path, mounts the
    MCP SSE server and runs a startup reconcile -- none of which a log-line assertion needs. A test
    that opens a database whose path comes from configuration is one misconfiguration away from
    opening the operator's, and this repo has an incident for exactly that. Being able to build the
    middleware alone is why it is a class.

    EVERY HANDLER ACTUALLY SPENDS THE TIME IT REPORTS. The first version of this file called
    `note_waited(20.0)` in a handler that ran for 1.2 seconds, and the two tests that depended on it
    failed -- correctly. A request cannot have slept longer than it lived, `attributable_ms` clamps at
    zero, and no line is logged. The accounting was right and the fixture was lying.
    """
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/fast")
    async def _fast():
        return {"ok": True}

    @app.get("/longpoll")
    async def _longpoll():
        # Over the threshold on the wall clock, and asleep for all of it: exactly the shape of the
        # 10,587 lines this change removes.
        await asyncio.sleep(WORK_S)
        note_waited(WORK_S)
        return {"ok": True}

    @app.get("/slow-under-a-poll")
    async def _slow_under_a_poll():
        # The case that must STILL warn: a poll that waited AND then worked slowly.
        await asyncio.sleep(WAIT_S)
        note_waited(WAIT_S)
        _busy(WORK_S)
        return {"ok": True}

    @app.get("/slow")
    async def _slow():
        _busy(WORK_S)
        return {"ok": True}

    return app


class SlowReqLoggingTests(unittest.TestCase):
    """The middleware end to end, because the pure helper passing says nothing about the wiring.

    `attributable_ms` being correct and `begin_wait_accounting` being called in the wrong place look
    identical from the helper's own suite -- a shape this repo has been bitten by more than once.
    """

    def setUp(self):
        self.client = TestClient(_app())

    def tearDown(self):
        self.client.close()

    def _lines(self, path: str) -> list[str]:
        """Warnings emitted while fetching `path`, plus a known-slow companion.

        `assertLogs` FAILS when nothing is logged, so a request expected to stay quiet cannot be
        measured alone. `/slow` is the companion and is asserted in its own test, which is what keeps
        this from passing against a deleted middleware.
        """
        with self.assertLogs("service.main", level="WARNING") as captured:
            self.client.get(path)
            self.client.get("/slow")
        return [record.getMessage() for record in captured.records]

    def test_a_request_that_spent_its_whole_life_ASLEEP_produces_no_line(self):
        lines = self._lines("/longpoll")
        self.assertEqual(
            [l for l in lines if "/longpoll" in l], [],
            f"a long poll over the wall-clock threshold was still reported as slow: {lines}",
        )

    def test_the_companion_slow_request_IS_reported(self):
        """Anti-vacuity for every quiet-path test here: with nothing ever logged they would all pass
        against a middleware that had been removed."""
        lines = self._lines("/fast")
        self.assertTrue([l for l in lines if "SLOW-REQ" in l and "/slow" in l], lines)

    def test_a_fast_request_produces_no_line(self):
        lines = self._lines("/fast")
        self.assertEqual([l for l in lines if "/fast" in l], [], lines)

    def test_slow_WORK_under_a_poll_is_still_reported(self):
        """The blind spot this change must not create. Subtracting the wait has to leave genuinely
        slow execution visible, or the fix trades one hidden failure for a better-hidden one."""
        lines = self._lines("/slow-under-a-poll")
        matching = [l for l in lines if "/slow-under-a-poll" in l]
        self.assertTrue(matching, f"slow work inside a long poll was not reported: {lines}")
        self.assertIn("waited", matching[0], "the line does not say a wait was subtracted")

    def test_the_line_reports_WORK_and_names_the_wait(self):
        lines = self._lines("/slow-under-a-poll")
        line = [l for l in lines if "/slow-under-a-poll" in l][0]
        work = int(re.search(r"(\d+)ms \(waited", line).group(1))
        waited, total = (int(g) for g in re.search(r"waited (\d+)ms of (\d+)ms", line).groups())
        self.assertGreater(work, 0, line)
        self.assertGreater(waited, 0, f"the wait was not accounted: {line}")
        self.assertAlmostEqual(total, work + waited, delta=2,
                               msg=f"the three numbers do not add up: {line}")


class LongpollRecordsItsOwnWaitTests(unittest.TestCase):
    """The CALL SITE: `longpoll()` itself must record, not just `note_waited` when called by hand."""

    def test_a_real_longpoll_that_times_out_records_the_time_it_slept(self):
        async def run():
            holder = begin_wait_accounting()
            result = await longpoll(
                wait_ms=120,
                attempt=lambda: asyncio.sleep(0, {"claimed": None}),
                is_empty=lambda r: r.get("claimed") is None,
                fallback_s=0.02,
            )
            return holder, result

        holder, result = asyncio.run(run())
        self.assertIsNone(result["claimed"])
        self.assertGreater(holder["ms"], 50.0, "longpoll slept but recorded almost nothing")
        self.assertLess(holder["ms"], 1000.0, "longpoll recorded more than it could have slept")

    def test_a_longpoll_that_returns_IMMEDIATELY_records_no_wait(self):
        """A claim that finds work never sleeps, so it must be measured whole -- otherwise a slow
        immediate claim could hide behind an imagined wait."""
        async def run():
            holder = begin_wait_accounting()
            await longpoll(
                wait_ms=5000,
                attempt=lambda: asyncio.sleep(0, {"claimed": "run-1"}),
                is_empty=lambda r: r.get("claimed") is None,
            )
            return holder

        self.assertEqual(asyncio.run(run())["ms"], 0.0)


if __name__ == "__main__":
    unittest.main()
