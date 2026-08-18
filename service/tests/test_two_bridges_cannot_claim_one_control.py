"""One environment control, one winner.

Reported by an external reviewer 2026-08-18: "environment_claim CAS not verified (two claimers win)".

The UPDATE was already a compare-and-swap — `SET status='claimed' ... WHERE id=? AND status='pending'`
is what makes it one — but nothing read whether it had WON. Two bridges long-polling the same
environment both reach it; one updates a row, the other updates none, and both returned
`{"ok": True, "control": {...}}` for the SAME control.

The consequence is not a duplicate log line. An environment control is an instruction: two bridges
honouring one `stop` means the survivor is stopped too, and two honouring one start means two workers
race for a terminal. This repo has already lost a fleet to the first shape.

WHY IT SURVIVED: the swap itself is correct, so the code READS correct, and every test that claims
once passes. A race only shows up when something claims twice — which is what this file does.
"""

from __future__ import annotations

import asyncio
import unittest

from service.db import get_db
from service.tests._base import FastApiTestCase

ENVIRONMENT_ID = "linux:test-host:default"


class TwoBridgesCannotClaimOneControl(FastApiTestCase):
    DB_NAME = "aify-control-claim-race-test.db"

    def setUp(self):
        super().setUp()
        r = self.client.post("/api/v1/environments/heartbeat", json={
            "id": ENVIRONMENT_ID, "label": "test", "machineId": "linux:test-host", "os": "linux",
            "kind": "linux", "bridgeId": "bridge-a", "cwdRoots": ["/w"],
            "runtimes": [{"runtime": "codex", "available": True}], "status": "online",
        })
        self.assertEqual(r.status_code, 200, r.text)

    def _pending_control(self, action: str = "stop") -> str:
        async def run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO environment_controls (id, environment_id, bridge_id, action,"
                    " status, requested_by, requested_at) VALUES (?,?,?,?,?,?,?)",
                    ("ctl-1", ENVIRONMENT_ID, "", action, "pending", "operator",
                     "2026-08-18T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())
        return "ctl-1"

    def _claim(self, bridge_id: str, machine_id: str = "linux:test-host"):
        return self.client.post("/api/v1/environments/controls/claim", json={
            "environmentId": ENVIRONMENT_ID, "bridgeId": bridge_id,
            "machineId": machine_id, "waitMs": 0,
        })

    def test_a_claim_that_LOSES_the_swap_returns_no_control(self):
        """THE ACTUAL RACE, made deterministic.

        The two sequential claims below cannot reach this branch: by the time the second runs, the
        control is no longer `pending`, so it is filtered out during selection and the answer is empty
        for a different reason entirely. They pass with or without the fix — measured, by mutation.

        A real race interleaves: both callers SELECT the control while it is still pending, then both
        UPDATE, and one of the two updates NOTHING. That is the only moment the rowcount matters, so
        it is simulated exactly — the claim UPDATE is forced to report zero rows, as it does for the
        loser — rather than hoping two threads collide on a timer.
        """
        import service.environment_claim as claim_module

        self._pending_control()
        real_get_db = claim_module.get_db

        class LosingConnection:
            """Forwards everything, but the claim UPDATE affects no rows — the loser's view."""

            def __init__(self, inner):
                self._inner = inner

            async def execute(self, sql, params=()):
                cursor = await self._inner.execute(sql, params)
                if "SET status = 'claimed'" in sql:
                    class Lost:
                        rowcount = 0
                    return Lost()
                return cursor

            def __getattr__(self, name):
                return getattr(self._inner, name)

        async def losing_get_db(*args, **kwargs):
            return LosingConnection(await real_get_db(*args, **kwargs))

        claim_module.get_db = losing_get_db
        try:
            response = self._claim("bridge-b")
        finally:
            claim_module.get_db = real_get_db

        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(
            response.json().get("control"),
            "a claimer whose compare-and-swap affected NO rows was still handed the control. Another "
            "bridge already owns it, so both would act on the same instruction.",
        )

    def test_only_ONE_of_two_claimers_receives_the_control(self):
        self._pending_control()
        first = self._claim("bridge-a")
        second = self._claim("bridge-b")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)

        got = [r.json().get("control") for r in (first, second)]
        winners = [c for c in got if c]
        self.assertEqual(
            len(winners), 1,
            "both bridges were handed the same control. An environment control is an INSTRUCTION: "
            "two bridges honouring one stop means the survivor is stopped too.",
        )
        self.assertEqual(winners[0]["id"], "ctl-1")

    def test_the_loser_is_told_there_is_nothing_to_claim(self):
        """Not an error — it lost a race, and the long-poll's contract is that an empty answer means
        come back later. An error here would make a normal race look like a fault."""
        self._pending_control()
        self._claim("bridge-a")
        loser = self._claim("bridge-b")
        self.assertEqual(loser.status_code, 200, loser.text)
        self.assertTrue(loser.json().get("ok"))
        self.assertIsNone(loser.json().get("control"))

    def test_a_claim_with_NOTHING_pending_is_still_an_empty_success(self):
        """ANTI-VACUITY: if the endpoint simply always returned no control, every assertion above
        would pass. The first claim in the tests above must genuinely hand one over."""
        empty = self._claim("bridge-a")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertIsNone(empty.json().get("control"))

        self._pending_control()
        got = self._claim("bridge-a")
        self.assertIsNotNone(got.json().get("control"),
                             "a pending control was never handed to anybody")

    def test_the_winning_claim_marks_the_row_claimed(self):
        self._pending_control()
        self._claim("bridge-a")

        async def read():
            db = await get_db()
            try:
                db.row_factory = __import__("aiosqlite").Row
                return await (await db.execute(
                    "SELECT status, machine_id FROM environment_controls WHERE id = 'ctl-1'")).fetchone()
            finally:
                await db.close()
        row = asyncio.run(read())
        self.assertEqual(row["status"], "claimed")
        self.assertEqual(row["machine_id"], "linux:test-host")


if __name__ == "__main__":
    unittest.main()
