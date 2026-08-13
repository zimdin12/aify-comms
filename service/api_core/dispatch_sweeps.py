"""Two sweeps that CREATE dispatch runs on something else's behalf.

Both sit above `dispatch_runs.py` and call into it, which is why they are not in it: that module is
the send path, and these two are periodic repair.

  _mirror_missing_dispatch_handoff   a run finished owing a handoff that never arrived; synthesise it
  _run_contract_reminders_once       a reply-contract is overdue; nudge the owing agent

`_run_contract_reminders_once` takes an optional `Request` because it is reachable BOTH from the
periodic sweep and from an operator-triggered route. That dual entry is the reason it lives here
rather than under `service/reconcilers/`: a reconciler in this codebase is loop-only, and this is not.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from fastapi import Request

from service.api_core.channel_delivery import _apply_channel_routing_to_claude_runs
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.dispatch_runs import (
    _create_dispatch_runs,
    _preflight_live_send_recipients,
)
from service.api_core.dispatch_state import (
    _DISPATCH_TERMINAL_STATUSES,
    _get_dispatch_state_for_agent,
    _is_delivery_only_claude_run,
)
from service.api_core.dispatch_text import (
    _auto_handoff_body_for_run,
    _auto_handoff_subject_for_run,
)
from service.api_core.events import _append_dispatch_event
from service.api_core.reply_contract import (
    _contract_list_query,
    _contract_reminder_body,
    _contract_reminder_due,
    _contract_reminder_is_full,
)
from service.api_core.serialization import _row_require_reply
from service.api_core.settings import _load_settings
from service.api_core.status_inputs import _compute_live_status_cache
from service.api_core.turn_state import _turn_busy_state
from service.api_core.ws import _get_ws


async def _mirror_missing_dispatch_handoff(db, row) -> Optional[str]:
    if not row or not _row_require_reply(row) or str(row["result_message_id"] or "").strip():
        return None
    if _is_delivery_only_claude_run(row):
        return None

    status = str(row["status"] or "").strip().lower()
    if status not in _DISPATCH_TERMINAL_STATUSES:
        return None

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    message_type = "error" if status == "failed" else "response"
    from_agent = str(row["target_agent"] or "").strip()
    to_agent = str(row["from_agent"] or "").strip()
    subject = _auto_handoff_subject_for_run(row)
    body = _auto_handoff_body_for_run(row)
    priority = row["priority"] or "normal"
    launchable_recipients: list[tuple[str, str]] = []
    not_started: list[dict[str, Any]] = []
    if to_agent and to_agent != "dashboard":
        launchable_recipients, not_started = await _preflight_live_send_recipients(
            db,
            [to_agent],
            allow_steer=True,
            allow_queue_busy=True,
        )

    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            from_agent,
            to_agent,
            "direct",
            message_type,
            subject,
            body,
            priority,
            1 if launchable_recipients else 0,
            row["message_id"],
            ts,
        ),
    )
    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (message_id, row["id"]),
    )
    await _append_dispatch_event(
        db,
        row["id"],
        "handoff",
        f"Auto-mirrored missing handoff to {to_agent}",
    )
    if launchable_recipients:
        delivery_runs = await _create_dispatch_runs(
            db,
            [recipient_id for recipient_id, _ in launchable_recipients],
            from_agent=from_agent,
            message_type=message_type,
            subject=subject,
            body=body,
            priority=priority,
            in_reply_to=row["message_id"],
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            steer=True,
            require_reply=False,
        )
        # Auto-mirrored handoff dispatches for managed claude must also
        # honor insert_messages_via_console=false (channel-route default).
        settings_for_handoff = await _load_settings(db)
        await _apply_channel_routing_to_claude_runs(db, delivery_runs, settings_for_handoff)
        delivery_runs = await _finalize_dispatch_runs(
            db,
            delivery_runs,
            launchable_recipients,
            not_started,
        )
        run_ids = [str(run.get("runId") or "") for run in delivery_runs if run.get("runId")]
        if run_ids:
            await _append_dispatch_event(
                db,
                row["id"],
                "handoff",
                f"Queued mirrored handoff delivery to {to_agent}: {', '.join(run_ids)}",
            )
    elif not_started:
        reasons = "; ".join(str(item.get("reason") or "not startable") for item in not_started)
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Mirrored handoff stored for {to_agent}; live delivery not queued: {reasons}",
        )
    return message_id


async def _run_contract_reminders_once(
    db,
    *,
    request: Optional[Request] = None,
    run_id: Optional[str] = None,
    dry_run: bool = False,
    limit: int = 50,
    now_s: Optional[float] = None,
    recent_only: bool = False,
    target_agent_id: Optional[str] = None,
    ignore_repeat: bool = False,
) -> dict[str, Any]:
    settings = await _load_settings(db)
    where = [
        "AND COALESCE(r.result_message_id, '') = ''",
        "AND r.status NOT IN ('completed','failed','cancelled')",
        "AND r.from_agent != r.target_agent",
        "AND r.target_agent != 'dashboard'",
    ]
    params: list[Any] = []
    if run_id:
        where.append("AND r.id = ?")
        params.append(run_id)
    if target_agent_id:
        where.append("AND r.target_agent = ?")
        params.append(str(target_agent_id).strip())
    if recent_only:
        stale_hours = max(1, int(settings.get("contract_stale_hours", 24) or 24))
        where.append("AND datetime(r.requested_at) >= datetime('now', ?)")
        params.append(f"-{stale_hours} hours")
    params.append(limit)
    cursor = await db.execute(_contract_list_query(where_sql="\n".join(where), order_sql="ORDER BY r.requested_at ASC"), params)
    candidates = await cursor.fetchall()
    reminded = []
    skipped = []
    now_s = now_s or time.time()
    for row in candidates:
        due, reason = _contract_reminder_due(row, settings=settings, now_s=now_s, ignore_repeat=ignore_repeat)
        if not due:
            skipped.append({"runId": row["id"], "reason": reason})
            continue

        terminal_blocked_without_live_backing = False
        if (
            str(row["dispatch_mode"] or "").strip().lower() == "terminal"
            and str(row["status"] or "").strip().lower() in {"claimed", "running"}
        ):
            agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            live_state = await _compute_live_status_cache(db, agent_row, settings=settings) if agent_row else {}
            if str(live_state.get("status") or "").strip().lower() == "blocked":
                live_reason = str(live_state.get("reason") or "").strip().lower()
                if live_reason.startswith("awaiting console"):
                    reason = "target is blocked awaiting operator input"
                    skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": reason})
                    await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", reason)
                    continue
                if "no live terminal backing" in live_reason:
                    terminal_blocked_without_live_backing = True

        active_state = await _get_dispatch_state_for_agent(db, row["target_agent"])
        # Busy = a claimed/running dispatch run OR a fresh turn_busy (the same
        # definition the status engine + claim-gate use). Without the turn_busy
        # half, a mid-turn agent with no tracked run (resident claude on its own
        # turn) was reminder-nagged while it was clearly working.
        #
        # BUT: a delivered require_reply run sets turn_busy with turn_run_id =
        # THAT run on its own delivery re-pulse. If we treat that as "busy" we
        # skip THIS run's own reminder — forever — and the handoff never gets
        # nudged, so the agent never replies and the run closes stale (confirmed
        # deadlock: ~24 consecutive reply_reminder_skipped "target is busy" then
        # "Closed stale delivered run requiring a reply"). So turn_busy only
        # counts as busy-for-skip when it is for OTHER work — a DIFFERENT run id
        # than the one we are about to remind. A claimed/running dispatch run
        # (hasActiveRun) always counts: the agent is genuinely executing.
        turn_fresh, turn_run_id = await _turn_busy_state(db, row["target_agent"])
        busy_for_other_work = turn_fresh and turn_run_id != row["id"]
        target_busy = bool(active_state.get("hasActiveRun")) or busy_for_other_work
        if target_busy and not terminal_blocked_without_live_backing:
            reason = "target is busy; reminder will be retried when the agent is idle"
            skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": reason})
            await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", reason)
            continue

        subject = f"Reminder: reply overdue - {str(row['subject'] or row['id'])[:96]}"
        # The reminder about to be sent is ordinal reminder_count + 1 (the
        # contract query counts prior 'reply_reminder' events for this run).
        prior_reminders = int((row["reminder_count"] if "reminder_count" in row.keys() else 0) or 0)
        body = _contract_reminder_body(
            row,
            full=_contract_reminder_is_full(prior_reminders + 1, settings=settings),
        )
        if dry_run:
            reminded.append({"runId": row["id"], "targetAgentId": row["target_agent"], "subject": subject, "dryRun": True})
            continue

        launchable, not_started = await _preflight_live_send_recipients(
            db,
            [row["target_agent"]],
            allow_steer=True,
            allow_queue_busy=True,
        )
        if not launchable:
            skipped.append({"runId": row["id"], "targetAgentId": row["target_agent"], "reason": "target cannot receive live reminder", "notStarted": not_started})
            await _append_dispatch_event(db, row["id"], "reply_reminder_skipped", json.dumps(not_started))
            continue

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        timestamp_ms = int(time.time() * 1000)
        await db.execute(
            """
            INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                message_id,
                row["from_agent"],
                row["target_agent"],
                "direct",
                "info",
                subject,
                body,
                "high" if str(row["priority"] or "").lower() == "urgent" else "normal",
                1,
                row["message_id"] or None,
                timestamp_ms,
            ),
        )
        runs = await _create_dispatch_runs(
            db,
            [target for target, _ in launchable],
            from_agent=row["from_agent"],
            message_type="info",
            subject=subject,
            body=body,
            priority="high" if str(row["priority"] or "").lower() == "urgent" else "normal",
            in_reply_to=row["message_id"] or None,
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            source_message_ids={row["target_agent"]: message_id},
            steer=True,
            require_reply=False,
        )
        finalized = await _finalize_dispatch_runs(db, runs, launchable, not_started)
        await _append_dispatch_event(db, row["id"], "reply_reminder", f"Sent reminder message {message_id}")
        reminded.append({
            "runId": row["id"],
            "targetAgentId": row["target_agent"],
            "messageId": message_id,
            "dispatchRuns": finalized,
        })

    ws = await _get_ws(request) if request else None
    if ws and reminded and not dry_run:
        await ws.broadcast("contract_reminders_sent", {"count": len(reminded)})
    return {"ok": True, "dryRun": dry_run, "reminded": reminded, "skipped": skipped}
