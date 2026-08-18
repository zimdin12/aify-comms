"""A failure notice the target never wrote must not satisfy the target's reply contract.

H2, reported by an external review 2026-08-18 and ruled by comms-senior-dev the same day, verbatim in
substance:

    An auto-mirrored/system failure notice may NOT satisfy a `require_reply` contract.
    `result_message_id` is reserved for an actual answer by the obligated target, an explicit
    operator/admin closure, or another intentionally-authored reply event with a real actor. A
    synthetic notice that says the target never ran is evidence of NON-delivery, not fulfilment.

`_mirror_missing_dispatch_handoff` wrote `result_message_id` unconditionally, so a require_reply run
that FAILED came out marked satisfied — by a notice generated because it had failed. The contract
read closed while nobody had answered.

WHY THIS NEEDED A NEW COLUMN rather than just deleting the write. `result_message_id` was doing two
jobs: "the obligated answer arrived" AND "the sender has already been told". The sweep selects on it
being empty, so simply not writing it would have re-mirrored every swept run on every reconcile pass
— a notice storm to the sender, which is worse than the bug. `handoff_message_id` now carries the
already-told fact alone, and the two questions are asked separately.

THE VISIBLE CONSEQUENCE IS INTENDED: more contracts stay open. The ruling says so outright — that is
truth-preserving, because the work genuinely was not done.
"""

from __future__ import annotations

import asyncio
import unittest

from service.api_core.dispatch_sweeps import _already_mirrored, _mirror_missing_dispatch_handoff
from service.db import get_db
from service.reconcilers.dispatch_lifecycle import _sweep_unmirrored_failed_handoffs
from service.tests._base import FastApiTestCase


def _minutes_ago(n: int) -> str:
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


class SystemNoticeDoesNotCloseAContract(FastApiTestCase):
    DB_NAME = "aify-h2-contract-test.db"

    def _register(self, agent_id: str, **extra):
        payload = {"agentId": agent_id, "role": "coder"}
        payload.update(extra)
        r = self.client.post("/api/v1/agents", json=payload)
        self.assertEqual(r.status_code, 200, r.text)

    def _execute(self, q, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(q, params)
                await db.commit()
            finally:
                await db.close()
        asyncio.run(_run())

    def _row(self, run_id):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(
                    "SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))).fetchone()
            finally:
                await db.close()
        return asyncio.run(_run())

    def _sweep(self):
        async def _run():
            db = await get_db()
            try:
                out = await _sweep_unmirrored_failed_handoffs(db)
                await db.commit()
                return out
            finally:
                await db.close()
        return asyncio.run(_run())

    def _seed(self, run_id, *, status="failed", require_reply=1, finished_minutes_ago=5):
        self._execute(
            """
            INSERT INTO dispatch_runs (id, message_id, from_agent, target_agent, dispatch_mode,
                execution_mode, message_type, subject, body, priority, status, require_reply,
                result_message_id, handoff_message_id, requested_at, finished_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (run_id, None, "sc-manager", "sc-architect", "start_if_possible", "managed", "request",
             "do X", "please do X", "normal", status, require_reply, "", "",
             _minutes_ago(60), _minutes_ago(finished_minutes_ago)),
        )

    def setUp(self):
        super().setUp()
        self._register("sc-manager")
        self._register("sc-architect")

    def test_a_FAILED_run_is_mirrored_but_its_contract_stays_OPEN(self):
        self._seed("run-failed-1")
        self.assertEqual(self._sweep(), 1, "the sender was not told the run failed")
        row = self._row("run-failed-1")
        self.assertEqual(str(row["result_message_id"] or ""), "",
                         "a system notice closed a contract the target never answered")
        self.assertNotEqual(str(row["handoff_message_id"] or ""), "",
                            "the sender was told, but nothing recorded it — the sweep will repeat")

    def test_the_sweep_is_IDEMPOTENT_on_the_new_marker(self):
        # The reason this needed a column instead of just dropping the write. Without a marker the
        # sender receives a fresh failure notice on every reconcile pass, forever.
        self._seed("run-failed-2")
        self.assertEqual(self._sweep(), 1)
        self.assertEqual(self._sweep(), 0, "the run was mirrored twice; the sender gets a notice storm")
        self.assertEqual(self._sweep(), 0)

    def test_a_CANCELLED_run_behaves_the_same(self):
        self._seed("run-cancelled-1", status="cancelled")
        self.assertEqual(self._sweep(), 1)
        row = self._row("run-cancelled-1")
        self.assertEqual(str(row["result_message_id"] or ""), "")
        self.assertNotEqual(str(row["handoff_message_id"] or ""), "")

    def test_a_run_with_a_REAL_reply_is_never_mirrored(self):
        # ANTI-VACUITY in the other direction: the sweep must still ignore a satisfied contract.
        self._seed("run-answered")
        self._execute("UPDATE dispatch_runs SET result_message_id = 'real-reply-1' WHERE id = ?",
                      ("run-answered",))
        self.assertEqual(self._sweep(), 0, "a run the target actually answered was mirrored anyway")

    def test_a_non_reply_run_is_not_swept(self):
        self._seed("run-no-contract", require_reply=0)
        self.assertEqual(self._sweep(), 0)


class TheAlreadyMirroredPredicate(unittest.TestCase):
    """`_already_mirrored` has to ask a DIFFERENT column per status, and getting that backwards
    reintroduces one of the two bugs: read `result_message_id` on a failed run and the mirror
    re-fires forever; read `handoff_message_id` on a completed one and a real result stops
    suppressing it."""

    def test_a_failed_run_is_judged_on_the_handoff_marker(self):
        self.assertTrue(_already_mirrored(
            {"status": "failed", "handoff_message_id": "notice-1", "result_message_id": ""}))
        # EITHER marker suppresses it. A failed run carrying a real result means the target
        # answered before the run was reaped, and telling the sender it was never delivered would be
        # false. My first version checked only the handoff marker and this file's anti-vacuity test
        # caught it immediately.
        self.assertTrue(_already_mirrored(
            {"status": "failed", "handoff_message_id": "", "result_message_id": "real-reply-1"}),
            "a failed run whose target ANSWERED would be sent a non-delivery notice")

    def test_a_completed_run_is_judged_on_the_result(self):
        self.assertTrue(_already_mirrored(
            {"status": "completed", "handoff_message_id": "", "result_message_id": "reply-1"}))
        self.assertFalse(_already_mirrored(
            {"status": "completed", "handoff_message_id": "", "result_message_id": ""}))

    def test_a_missing_column_is_not_an_error(self):
        # Rows reach this from several queries, and `sqlite3.Row` raises on an absent key rather
        # than returning None.
        self.assertFalse(_already_mirrored({"status": "failed"}))


if __name__ == "__main__":
    unittest.main()
