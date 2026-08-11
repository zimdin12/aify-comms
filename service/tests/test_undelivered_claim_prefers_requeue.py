"""The restart bug's second path — a claimed-but-never-delivered run must be RESCUED, not failed.

TRACE (KNOWN_ISSUES, `claimed_at` set; `run_1785537062959_4da30337`, 2026-07-31T22:31): a
restart's new initial-brief run is CLAIMED by the OLD worker's channel sidecar one second
before that sidecar is torn down. The sidecar dies holding the claim, the run is never
delivered, and the spawn fails with it — the operator hits Restart and gets no worker.

Two existing paths share a trigger and disagree on the outcome, both gated on the same
`ACTIVE_RUN_BRIDGE_STALE_SECONDS`:

    _requeue_orphaned_claimed_runs     RECOVERS
    _discard_unclaimable_active_run    FAILS the run, taking the spawn with it

KNOWN_ISSUES called it a race. Verified 2026-08-07 it is a DETERMINISTIC loss, and the cause
is ordering: in one reconcile sweep the failing path is reached at `main.py:113` and the
recovery runs at `main.py:171`, with a commit between. `test_the_sweep_orders_failure_before_recovery`
pins that ordering fact, because it is the whole reason this fix has to live in the failing
path rather than in the recovery path.
"""

import asyncio
import re
import unittest
from pathlib import Path

from service.db import get_db
from service.routers import api_v2
from service.tests._base import FastApiTestCase

REPO_ROOT = Path(__file__).resolve().parents[2]


class UndeliveredClaimPrefersRequeueTests(FastApiTestCase):
    DB_NAME = "aify-undelivered-claim.db"

    OLD = "2020-01-01T00:00:00Z"

    def _execute(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                await db.execute(query, params)
                await db.commit()
            finally:
                await db.close()

        asyncio.run(_run())

    def _fetchone(self, query, params=()):
        async def _run():
            db = await get_db()
            try:
                return await (await db.execute(query, params)).fetchone()
            finally:
                await db.close()

        return asyncio.run(_run())

    def _fail_stale(self, run_id):
        """Drive the real funnel every failing branch goes through."""
        async def _run():
            db = await get_db()
            try:
                result = await api_v2._fail_stale_active_run(
                    db,
                    {"runId": run_id},
                    reason="owner bridge stopped heartbeating",
                    summary="Active run failed because the bridge stopped heartbeating.",
                    event_body="Stale active run cleaned before send",
                )
                await db.commit()
                return result
            finally:
                await db.close()

        return asyncio.run(_run())

    def _seed_run(self, run_id, *, status="claimed", events=()):
        self._execute(
            """INSERT INTO agents (id, name, role, runtime, session_mode, status, registered_at, last_seen)
               VALUES (?,?,?,?,?,?,?,?)""",
            (f"agent_{run_id}", "a", "coder", "claude-code", "managed", "idle", self.OLD, self.OLD),
        )
        self._execute(
            """INSERT INTO dispatch_runs
                 (id, from_agent, target_agent, status, require_reply, runtime,
                  claim_bridge_id, claim_machine_id, claimed_at, requested_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, "sender", f"agent_{run_id}", status, 1, "claude-code",
                "dead-sidecar-bridge", "win32:test", self.OLD, self.OLD,
            ),
        )
        for event_type in events:
            self._execute(
                "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
                (run_id, event_type, "", self.OLD),
            )

    def _run_row(self, run_id):
        return self._fetchone(
            "SELECT status, claim_bridge_id, claimed_at, summary FROM dispatch_runs WHERE id = ?",
            (run_id,),
        )

    def _event_count(self, run_id, event_type):
        return self._fetchone(
            "SELECT COUNT(*) c FROM dispatch_events WHERE run_id = ? AND event_type = ?",
            (run_id, event_type),
        )["c"]

    # ---- the fix -----------------------------------------------------------
    def test_a_claimed_never_delivered_run_is_requeued_not_failed(self):
        self._seed_run("run_rescue")
        self.assertTrue(self._fail_stale("run_rescue"), "the funnel must report it handled the run")
        row = self._run_row("run_rescue")
        self.assertEqual(row["status"], "queued", "it must be recoverable by a live bridge")
        self.assertEqual(row["claim_bridge_id"], "", "the dead claim must be released")
        self.assertEqual(row["claimed_at"], "")
        self.assertEqual(self._event_count("run_rescue", "requeued_orphaned_claim"), 1)

    def test_the_rescue_records_the_failure_it_replaced(self):
        self._seed_run("run_audit")
        self._fail_stale("run_audit")
        body = self._fetchone(
            "SELECT body FROM dispatch_events WHERE run_id = ? AND event_type = 'requeued_orphaned_claim'",
            ("run_audit",),
        )["body"]
        self.assertIn("never delivered", body)
        self.assertIn("owner bridge stopped heartbeating", body)

    # ---- bounded, so nothing becomes immortal -------------------------------
    def test_the_rescue_is_bounded_and_then_the_run_fails(self):
        self._seed_run(
            "run_bounded",
            events=("requeued_orphaned_claim",) * api_v2.UNDELIVERED_CLAIM_REQUEUE_LIMIT,
        )
        self.assertTrue(self._fail_stale("run_bounded"))
        self.assertEqual(
            self._run_row("run_bounded")["status"], "failed",
            "past the bound it must terminate, not cycle forever",
        )

    def test_the_bound_is_reached_by_repeated_rescue_not_only_by_seeding(self):
        self._seed_run("run_cycle")
        for attempt in range(api_v2.UNDELIVERED_CLAIM_REQUEUE_LIMIT):
            self.assertTrue(self._fail_stale("run_cycle"))
            self.assertEqual(self._run_row("run_cycle")["status"], "queued", f"attempt {attempt}")
            # A live bridge re-claims it; the sidecar dies again.
            self._execute(
                "UPDATE dispatch_runs SET status = 'claimed', claimed_at = ? WHERE id = ?",
                (self.OLD, "run_cycle"),
            )
        self.assertTrue(self._fail_stale("run_cycle"))
        self.assertEqual(self._run_row("run_cycle")["status"], "failed")

    # ---- runs that DID reach the agent must still fail -----------------------
    def test_a_delivered_run_still_fails(self):
        self._seed_run("run_delivered", events=("delivered",))
        self.assertTrue(self._fail_stale("run_delivered"))
        self.assertEqual(
            self._run_row("run_delivered")["status"], "failed",
            "a run that reached the agent must keep the old behaviour",
        )

    def test_a_running_run_still_fails(self):
        self._seed_run("run_running", status="running")
        self.assertTrue(self._fail_stale("run_running"))
        self.assertEqual(self._run_row("run_running")["status"], "failed")

    def test_an_already_terminal_run_is_untouched_by_the_rescue(self):
        self._seed_run("run_done", status="completed")
        self._fail_stale("run_done")
        self.assertEqual(self._event_count("run_done", "requeued_orphaned_claim"), 0)

    def test_a_failed_run_keeps_its_failure_summary(self):
        self._seed_run("run_failing", events=("delivered",))
        self._fail_stale("run_failing")
        self.assertIn("stopped heartbeating", self._run_row("run_failing")["summary"])

    def test_missing_run_id_is_safe(self):
        self.assertFalse(self._fail_stale(""))

    # ---- the ordering fact this fix depends on ------------------------------
    def test_the_sweep_orders_failure_before_recovery(self):
        """Pins WHY the tie must be broken in the failing path.

        If someone reorders the sweep so recovery runs first, this test fails — and that is
        the moment to re-read whether this fix is still the right shape.
        """
        source = (REPO_ROOT / "service" / "main.py").read_text(encoding="utf-8")
        fail_at = source.index("_repair_unusable_active_runs(db")
        requeue_at = source.index("_requeue_orphaned_claimed_runs(db")
        self.assertLess(
            fail_at,
            requeue_at,
            "the failing path runs BEFORE recovery in the same sweep — that ordering is the "
            "reason recovery could never win, and the reason this fix lives in the funnel",
        )

    def test_both_paths_still_share_one_staleness_ceiling(self):
        """The two paths becoming eligible together is the trigger. If they ever stop
        sharing the bound, the deterministic-loss analysis needs redoing."""
        source = (REPO_ROOT / "service" / "reconcilers" / "dispatch_queue.py").read_text(encoding="utf-8")
        requeue_body = source[source.index("async def _requeue_orphaned_claimed_runs"):][:4000]
        # v0.5 slice 7: the requeue reconciler moved to service/reconcilers/dispatch_queue.py and now
        # reads the ceiling through `_active_run_bridge_stale_seconds()`, a shim over the ONE
        # owner in the router. The property under test is unchanged — both paths still derive from
        # a single ACTIVE_RUN_BRIDGE_STALE_SECONDS — but it is reached by call rather than by name,
        # which is deliberate: a second copy of that constant is the drift this test guards.
        self.assertIn("_active_run_bridge_stale_seconds()", requeue_body)
        # Window must reach the FINAL gate — the heartbeat check is the last branch in the
        # function, and a window one character short of it made this test pass for the wrong
        # reason on the first run.
        # `_discard_unclaimable_active_run` did NOT move in slice 7 — this agreement now spans two
        # files, which is precisely why it is asserted rather than assumed.
        router = (REPO_ROOT / "service" / "routers" / "api_v2.py").read_text(encoding="utf-8")
        unclaimable_at = router.index("async def _discard_unclaimable_active_run")
        unclaimable = router[unclaimable_at:router.index("async def _discard_unusable_active_run")]
        self.assertIn("ACTIVE_RUN_BRIDGE_STALE_SECONDS", unclaimable)

    def test_the_reverted_shape_is_not_reintroduced(self):
        """`0b948d2` → `70e03aa`: superseding the sidecar at terminal death cannot work (the
        claim precedes the death by a second) and DELETED the rescue window. This fix must
        widen the window, never close it — so the funnel must prefer requeue BEFORE it writes
        a failure."""
        # `_fail_stale_active_run` stayed in the router; only the requeue reconciler moved.
        source = (REPO_ROOT / "service" / "routers" / "api_v2.py").read_text(encoding="utf-8")
        funnel = source[source.index("async def _fail_stale_active_run"):][:2000]
        rescue_at = funnel.index("_requeue_instead_of_failing_undelivered_claim")
        fail_write_at = funnel.index("SET status = 'failed'")
        self.assertLess(rescue_at, fail_write_at, "the rescue must be attempted before the failure is written")


if __name__ == "__main__":
    unittest.main()
