"""A control whose run has ended must not sit unsettled forever. Nothing reaped them, ever.

MEASURED ON THE LIVE DATABASE, 2026-08-18: 56 `dispatch_controls` rows sat `pending`, the oldest since
2026-06-06 — seventy-three days — and every one of them belonged to a run that had ALREADY reached a
terminal status (53 completed, 3 failed). No reaper for that table existed anywhere. The only thing
that ever removed one was the `ON DELETE CASCADE` when its run row was pruned, which is exactly why
the orphan count was zero while the stuck count was 56.

WHY STATE AND NOT A TIMEOUT. A control is guidance for an ACTIVE run. Once the run is terminal,
applying it is not late — it is impossible, because there is no turn left to interrupt or steer. A
"pending for more than N minutes" rule would have to guess a bound, and a control legitimately waits
for its agent's dispatch loop to poll, which can be a long time for an agent that is offline and
returns. The run's terminal status is the whole condition and it cannot be wrong.

This is the rule 590e995 taught, applied to a second table: cleanup that must hold for ALL paths keys
on the STATE. A settlement can fail to arrive because the bridge died mid-apply, because the runtime
lost its controller, because the claim was refused, or — since the mandatory-actor change — because a
pre-actor bridge sent no actor. A cleanup hooked to any one of those leaves the rest stranded.

IT ALSO BOUNDS A DEPLOY WINDOW. comms-senior-dev's condition for release approval was to make the
pre-actor-bridge window "explicit and bounded" rather than weaken the actor guard. This is the bound,
and it does not re-open the ownership hole: the sweep never accepts an unauthenticated settlement, it
records that the control was never applied.

A CLAIM CORRECTED BY THE SAME MEASUREMENT. The endpoint's comment says a refused settlement "strands
the run it was meant to close", and I had repeated it. 53 of those 56 stuck controls belong to runs
that COMPLETED normally, so the run has its own backstops. The honest consequence of an unsettled
control is a lost instruction and a permanent row — the operator's stop button reporting nothing — not
a stranded run. Overstating it would have justified weakening the actor guard for the wrong reason.
"""

from __future__ import annotations

import asyncio
import unittest

from service.db import get_db
from service.reconcilers.stuck_controls import (
    RECONCILE_ACTOR,
    UNAPPLIED_REASON,
    _close_controls_for_ended_runs,
)
from service.tests._base import FastApiTestCase

AGENT = "sc-ctl-agent"


class ControlsForEndedRunsAreClosed(FastApiTestCase):
    DB_NAME = "aify-stuck-controls-test.db"

    def setUp(self):
        super().setUp()
        self._seed_agent()

    # ── fixture ──────────────────────────────────────────────────────────────────────────────

    def _seed_agent(self):
        async def run():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO agents (id, name, role, runtime, session_mode, status,"
                    " registered_at, last_seen) VALUES (?,?,?,?,?,?,?,?)",
                    (AGENT, AGENT, "coder", "claude-code", "managed", "online",
                     "2026-08-18T00:00:00Z", "2026-08-18T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(run())

    def _seed(self, run_id: str, run_status: str, control_id: str, control_status: str = "pending",
              action: str = "interrupt"):
        async def go():
            db = await get_db()
            try:
                await db.execute(
                    "INSERT INTO dispatch_runs (id, from_agent, target_agent, status, requested_at)"
                    " VALUES (?,?,?,?,?)",
                    (run_id, "operator", AGENT, run_status, "2026-08-18T00:00:00Z"),
                )
                await db.execute(
                    "INSERT INTO dispatch_controls (id, run_id, from_agent, action, body, status,"
                    " requested_at) VALUES (?,?,?,?,?,?,?)",
                    (control_id, run_id, "operator", action, "", control_status,
                     "2026-08-18T00:00:00Z"),
                )
                await db.commit()
            finally:
                await db.close()
        asyncio.run(go())

    def _sweep(self, limit: int = 200):
        async def go():
            db = await get_db()
            try:
                out = await _close_controls_for_ended_runs(db, limit=limit)
                await db.commit()
                return out
            finally:
                await db.close()
        return asyncio.run(go())

    def _control(self, control_id: str):
        async def go():
            db = await get_db()
            try:
                import aiosqlite
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
                return await cur.fetchone()
            finally:
                await db.close()
        return asyncio.run(go())

    def _events(self, run_id: str) -> str:
        async def go():
            db = await get_db()
            try:
                cur = await db.execute(
                    "SELECT event_type, body FROM dispatch_events WHERE run_id = ?", (run_id,))
                return " | ".join(f"{r[0]}::{r[1] or ''}" for r in await cur.fetchall())
            finally:
                await db.close()
        return asyncio.run(go())

    # ── the rule ─────────────────────────────────────────────────────────────────────────────

    def test_a_pending_control_on_an_ENDED_run_is_closed(self):
        for run_status in ("completed", "failed", "cancelled"):
            with self.subTest(run_status=run_status):
                run_id, control_id = f"r-{run_status}", f"c-{run_status}"
                self._seed(run_id, run_status, control_id)
                closed = self._sweep()
                self.assertEqual(
                    [c["controlId"] for c in closed], [control_id],
                    f"a pending control on a {run_status} run was not closed. Nothing else reaps this "
                    "table, so it sits forever — measured at seventy-three days on the live database.",
                )
                self.assertEqual(self._control(control_id)["status"], "failed")

    def test_a_CLAIMED_control_on_an_ended_run_is_closed_too(self):
        """Claimed is the more common stuck state after a bridge dies mid-apply: it took the control
        and never answered. Leaving it out would miss exactly the settlement-never-arrived case."""
        self._seed("r-claimed", "completed", "c-claimed", control_status="claimed")
        self.assertEqual([c["controlId"] for c in self._sweep()], ["c-claimed"])

    def test_it_is_recorded_as_FAILED_not_completed(self):
        """`completed` would be a lie, and the controls API surfaces `response_text` to callers
        verbatim — a caller asking "did my interrupt land?" gets the wrong answer from a green status.
        Same defect class as the receipt that claimed "Delivered to Claude resident session" for a
        managed agent."""
        self._seed("r-honest", "completed", "c-honest")
        self._sweep()
        row = self._control("c-honest")
        self.assertEqual(row["status"], "failed",
                         "a control that was never applied must not read as completed")
        self.assertEqual(row["response_text"], UNAPPLIED_REASON)
        self.assertIn("Never applied", row["response_text"])

    def test_the_closing_actor_is_named(self):
        """So the audit trail separates "the service closed this because it had become impossible" from
        "an actor settled it" and from the pre-2026-08-18 rows whose actor is genuinely unknown."""
        self._seed("r-actor", "completed", "c-actor")
        self._sweep()
        actor = self._control("c-actor")["handled_by"]
        # THE LITERAL, not the constant. Asserting `== RECONCILE_ACTOR` reads the same value the
        # product does, so it moves with any change to it — a mutation blanking the constant to ""
        # SURVIVED that version of this test. An assertion that imports its own expected value proves
        # only that the constant equals itself.
        self.assertEqual(actor, "reconcile",
                         "the closing actor is no longer recorded as 'reconcile'")
        self.assertTrue(actor.strip(),
                        "the closing actor is blank, so a reconcile-closed control is "
                        "indistinguishable from the pre-2026-08-18 rows whose actor is unknown")
        self.assertEqual(RECONCILE_ACTOR, "reconcile",
                         "the exported constant and the stored value have drifted apart")

    def test_it_appears_in_the_run_audit_trail(self):
        self._seed("r-event", "completed", "c-event", action="steer")
        self._sweep()
        events = self._events("r-event")
        self.assertIn("control:steer:failed", events,
                      f"the closure is not in the run's event list: {events!r}")
        self.assertIn(RECONCILE_ACTOR, events)

    # ── what it must NOT touch ───────────────────────────────────────────────────────────────

    def test_a_control_on_a_LIVE_run_is_left_alone(self):
        """ANTI-VACUITY and the safety property together. Every assertion above would pass if the sweep
        closed everything — and that version would cancel the operator's interrupt on every run that
        was still executing, which is the opposite of this feature."""
        for run_status in ("queued", "claimed", "delivered", "running"):
            with self.subTest(run_status=run_status):
                run_id, control_id = f"live-{run_status}", f"cl-{run_status}"
                self._seed(run_id, run_status, control_id)
                self.assertEqual(
                    self._sweep(), [],
                    f"a control on a {run_status} run was closed. That run is still live and the "
                    "control is still applicable — closing it discards the instruction.",
                )
                self.assertEqual(self._control(control_id)["status"], "pending")

    def test_an_ALREADY_SETTLED_control_is_not_touched(self):
        """Idempotence, and it protects a real outcome: a control the bridge genuinely answered must
        keep what it reported."""
        for settled in ("completed", "failed"):
            with self.subTest(control_status=settled):
                run_id, control_id = f"r-set-{settled}", f"c-set-{settled}"
                self._seed(run_id, "completed", control_id, control_status=settled)
                self.assertEqual(self._sweep(), [])
                self.assertEqual(self._control(control_id)["status"], settled)

    def test_a_second_pass_closes_nothing_new(self):
        self._seed("r-idem", "completed", "c-idem")
        self.assertEqual(len(self._sweep()), 1)
        self.assertEqual(self._sweep(), [], "the sweep re-closed a control it had already closed")

    def test_the_limit_is_respected_and_drains_oldest_first(self):
        """Bounded so a live control plane is never held for long, and ordered so a backlog drains
        deterministically instead of re-processing the same head every pass."""
        for i in range(4):
            self._seed(f"r-lim-{i}", "completed", f"c-lim-{i}")
        first = self._sweep(limit=2)
        self.assertEqual(len(first), 2, "the limit was not respected")
        second = self._sweep(limit=2)
        self.assertEqual(len(second), 2)
        self.assertEqual(
            sorted(c["controlId"] for c in first + second),
            [f"c-lim-{i}" for i in range(4)],
            "two bounded passes did not drain the whole backlog",
        )

    def test_the_sweep_is_reported_in_the_reconcile_result(self):
        """A sweep whose count never reaches the reconcile summary is a sweep nobody can see working —
        and the log line is how this repo has diagnosed every reaper it has."""
        from pathlib import Path
        sweep_src = (Path(__file__).resolve().parents[1] / "reconcilers" / "sweep.py").read_text(
            encoding="utf-8")
        self.assertIn("_close_controls_for_ended_runs", sweep_src,
                      "the reconciler is not called by the sweep, so it never runs in production")
        self.assertIn("ended_run_controls_closed", sweep_src,
                      "the sweep's count is not reported in the reconcile result")


if __name__ == "__main__":
    unittest.main()
