"""Advancing a dispatch run's state: append a control, finalize a batch, mark one answered.

v0.5.4 layer 0. Three writes against `dispatch_runs` and its control/event tables, moved out of the
control plane where they were reached through SEVEN borrow shims across the routers and reconcilers —
the all-consumers-through-a-shim shape that says the carrier was a hiding place rather than an owner.

WHY THESE THREE ARE ONE MODULE: each advances a run along the same lifecycle, and a reader working out
why a run reached a given status needs them together. `_auto_handoff_body_for_run` came out of the
carrier in the same slice and deliberately did NOT come here — it composes MESSAGE TEXT, so it went to
`api_core/dispatch_text.py`, beside `_is_provider_rate_limit_error`, which is its only dependency.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction. Verified for all three before the move, not assumed.

A LEAF: imports api_core siblings and `service/clock.py`, never a router and never the control plane.
The control plane is now a CALLER.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.serialization import _dedupe_preserve
from service.api_core.events import _append_dispatch_event
from service.api_core.turn_state import _clear_turn_busy_if_no_open_reply_owing_run
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _append_dispatch_control(
    db,
    run_id: str,
    *,
    from_agent: str,
    action: str,
    body: str = "",
    source_message_id: str = "",
):
    control_id = f"ctl_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        """
        INSERT INTO dispatch_controls (
            id, run_id, from_agent, source_message_id, action, body, status, requested_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (control_id, run_id, from_agent or "", source_message_id or "", action, body or "", "pending", _now())
    )
    await _append_dispatch_event(db, run_id, f"control:{action}", f"requested by {from_agent or 'unknown'}")
    return control_id


async def _finalize_dispatch_runs(
    db,
    runs: list[dict[str, Any]],
    launchable_recipients: list[tuple[str, str]],
    not_started: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    finalized = []
    for run, (_, execution_mode) in zip(runs, launchable_recipients):
        if run.get("rejected"):
            not_started.append(run["rejectionHint"])
            continue

        if run.get("steered"):
            dispatch_state = await _get_dispatch_state_for_agent(db, run["targetAgentId"])
            run["queuedRunsForTarget"] = dispatch_state.get("queuedRuns", 0)
            finalized.append(run)
            continue

        await db.execute(
            "UPDATE dispatch_runs SET execution_mode = ? WHERE id = ?",
            (execution_mode, run["runId"])
        )
        active = await _get_blocking_active_run(db, run["targetAgentId"], exclude_run_id=run["runId"])
        if active:
            run["queuedBehindActiveRun"] = {
                "runId": active["runId"],
                "status": active["status"],
                "subject": active["subject"],
            }
        dispatch_state = await _get_dispatch_state_for_agent(db, run["targetAgentId"])
        run["queuedRunsForTarget"] = dispatch_state.get("queuedRuns", 0)
        finalized.append(run)
    return finalized


async def _mark_dispatch_run_answered(
    db,
    run_id: str,
    reply_message_id: str,
    current_status: str = "",
    execution_mode: str = "",
):
    status = str(current_status or "").strip().lower()
    mode = str(execution_mode or "").strip().lower()
    target_cursor = await db.execute("SELECT target_agent, dispatch_mode FROM dispatch_runs WHERE id = ?", (run_id,))
    target_row = await target_cursor.fetchone()
    target_agent = str((target_row["target_agent"] if target_row else "") or "").strip()
    dispatch_mode = str((target_row["dispatch_mode"] if target_row and "dispatch_mode" in target_row.keys() else "") or "").strip().lower()
    if (
        status in {"queued", "delivered"}
        or (mode in {"channel", "resident"} and status in {"claimed", "running"})
        or (dispatch_mode == "terminal" and status in {"claimed", "running"})
    ):
        await db.execute(
            """
            UPDATE dispatch_runs
            SET result_message_id = ?,
                status = 'completed',
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
            """,
            (reply_message_id, _now(), run_id),
        )
        # Event-based working-state clear. claude-channel.js pulses
        # turn_busy=true on every delivery and relies on the 120s
        # TURN_BUSY_STALE_SECONDS window for cleanup. That window is too
        # long after the agent's reply lands — operator sees "working"
        # linger when the actual work is done. Clear it here for any
        # channel-or-resident dispatch that just got answered AND has
        # no other in-flight rr=1 runs for the same agent (so we don't
        # clear while real reply-owing work is still in flight).
        if mode in {"channel", "resident"} and target_agent:
            await _clear_turn_busy_if_no_open_reply_owing_run(db, target_agent, run_id)
        await _invalidate_agent_live_state(db, target_agent)
        return
    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (reply_message_id, run_id),
    )
    await _invalidate_agent_live_state(db, target_agent)


# --- cancelling runs a deleted message can no longer be answered by ------------------------------
#
# Moved here from `service/routers/dispatch_messages/messages.py` in v0.5.4, byte-identical. A router
# should hold routes, and this is dispatch-run STATE — the same subject `_mark_dispatch_run_answered`
# above already owns.

async def _cancel_queued_dispatch_runs_for_message_ids(db, message_ids: list[str], *, chunk_size: int = 250) -> list[str]:
    pending = _dedupe_preserve([str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()])
    if not pending:
        return []

    cancelled_ids = []
    finished_at = _now()
    summary = "Cancelled because source message was unsent."
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = await db.execute(
            f"SELECT id FROM dispatch_runs WHERE status = 'queued' AND message_id IN ({placeholders})",
            chunk,
        )
        run_ids = [str(row["id"]) for row in await cursor.fetchall()]
        if not run_ids:
            continue
        run_placeholders = ",".join("?" for _ in run_ids)
        await db.execute(
            f"UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id IN ({run_placeholders})",
            (summary, finished_at, *run_ids),
        )
        for run_id in run_ids:
            await _append_dispatch_event(db, run_id, "cancelled", summary)
        cancelled_ids.extend(run_ids)
    return cancelled_ids
