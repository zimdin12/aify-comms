"""Close dispatch controls whose run has ended. Nothing did this, ever.

MEASURED, on the operator's live database 2026-08-18: 56 controls sat `pending`, the oldest since
2026-06-06 — seventy-three days. All 56 belonged to runs that had ALREADY reached a terminal status
(53 completed, 3 failed). There is no reaper for `dispatch_controls` anywhere in the codebase; the
only cleanup is the `ON DELETE CASCADE` when the owning run row is pruned, which is why the orphan
count is zero and the stuck count is not.

THE RULE IS STATE-BASED AND NEEDS NO TIMEOUT. A control is guidance for an ACTIVE run — an interrupt
to stop it, a steer to redirect it. Once the run has ended, applying the control is not late, it is
impossible: there is no turn left to interrupt or steer. So the terminal status of the run is the
whole condition, and it is unambiguous. That matters because the alternative — "pending for longer
than N" — would have to guess at a bound, and a control legitimately waits for its agent's dispatch
loop to poll, which can be a long time for an agent that is offline and returns.

This is the "cleanup that must hold for ALL paths keys on the STATE, not on an event" rule. There are
many ways a control fails to be settled — the bridge died mid-apply, the claim was refused, the
runtime lost its controller, a settlement 400'd — and a cleanup hooked to any one of them leaves the
others stranded. The run ending is the one condition common to all of them.

WHY IT IS BEING WRITTEN NOW. The mandatory-actor change on `PATCH /dispatch/controls/{id}` refuses a
settlement from a bridge running pre-actor code, and until every wrapper is relaunched that is a
reachable state across the fleet. comms-senior-dev's condition for a release was to "make the
partial-deploy window explicit and BOUNDED" rather than weaken the guard — this is the bound. It does
not re-open the ownership hole: the sweep never accepts an unauthenticated settlement, it records that
the control was never applied.

A CLAIM I HAD BEEN REPEATING IS WRONG, and the same measurement corrects it. The endpoint's own
comment says a refused settlement "strands the run it was meant to close", and I repeated that. The
data says otherwise: 53 of the 56 stuck controls belong to runs that COMPLETED normally. An unsettled
control leaks a row and loses an instruction — the operator's stop button reports nothing — but the
run has its own backstops (`_fail_stranded_delivered_reply_runs`, the orphan reapers). The honest
consequence is a lost instruction and a permanent row, not a stranded run.
"""

from __future__ import annotations

from typing import Optional

from service.api_core.events import _append_dispatch_event
from service.clock import now as _now

#: Terminal run statuses. A control cannot be applied to a run in any of these states.
_ENDED_RUN_STATUSES = ("completed", "failed", "cancelled")

#: Control statuses that are still awaiting settlement.
_UNSETTLED_CONTROL_STATUSES = ("pending", "claimed")

#: Recorded as the settling actor. Named rather than left empty so the audit trail distinguishes "the
#: service closed this because it had become impossible" from "an actor settled it" and from the
#: pre-2026-08-18 rows whose actor is genuinely unknown.
RECONCILE_ACTOR = "reconcile"

#: What the response text says. A control closed here was NOT carried out, and the text has to say so:
#: `completed` would be a lie that reads back to the caller through the controls API verbatim, and the
#: caller's decision (did my interrupt land?) depends on the difference.
UNAPPLIED_REASON = (
    "Never applied — the run had already ended when this was reconciled, so there was no active turn "
    "left to interrupt or steer. Closed by reconcile so it does not sit unsettled forever."
)


async def _close_controls_for_ended_runs(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Settle unsettled controls whose run is terminal, as `failed` with an explicit cause.

    `failed`, not `completed`: the control was not carried out. The controls API surfaces
    `response_text` to callers verbatim, and a caller asking "did my interrupt land?" gets the wrong
    answer from a green status. This is the same defect class as the receipt that claimed
    "Delivered to Claude resident session" for a managed agent.

    Idempotent: a settled control is never re-selected. Bounded by `limit` so a live control plane is
    never held for long, and ordered oldest-first so a backlog drains deterministically rather than
    re-processing the same head every pass.
    """
    run_placeholders = ",".join("?" for _ in _ENDED_RUN_STATUSES)
    control_placeholders = ",".join("?" for _ in _UNSETTLED_CONTROL_STATUSES)
    rows = await (await db.execute(
        f"""
        SELECT c.id, c.run_id, c.action, r.status AS run_status
        FROM dispatch_controls c
        JOIN dispatch_runs r ON r.id = c.run_id
        WHERE c.status IN ({control_placeholders})
          AND r.status IN ({run_placeholders})
        ORDER BY c.requested_at ASC
        LIMIT ?
        """,
        (*_UNSETTLED_CONTROL_STATUSES, *_ENDED_RUN_STATUSES, max(1, int(limit or 200))),
    )).fetchall()

    closed: list[dict[str, str]] = []
    handled_at = _now()
    for row in (rows or []):
        control_id = str(row["id"] or "").strip()
        run_id = str(row["run_id"] or "").strip()
        if not control_id:
            continue
        # RE-CHECK THE STATUS IN THE WRITE. A settlement landing between the SELECT above and here —
        # the bridge finally answering — must win, because it carries what actually happened to the
        # control. An unconditional UPDATE would overwrite a real outcome with "never applied".
        cursor = await db.execute(
            f"""
            UPDATE dispatch_controls
            SET status = 'failed', response_text = ?, handled_at = ?, handled_by = ?
            WHERE id = ? AND status IN ({control_placeholders})
            """,
            (UNAPPLIED_REASON, handled_at, RECONCILE_ACTOR, control_id,
             *_UNSETTLED_CONTROL_STATUSES),
        )
        if not (cursor.rowcount or 0):
            continue  # a real settlement won the race
        if run_id:
            await _append_dispatch_event(
                db,
                run_id,
                f"control:{str(row['action'] or 'unknown')}:failed",
                f"[{RECONCILE_ACTOR}] {UNAPPLIED_REASON}",
            )
        closed.append({"controlId": control_id, "runId": run_id,
                       "action": str(row["action"] or ""),
                       "runStatus": str(row["run_status"] or "")})
    return closed
