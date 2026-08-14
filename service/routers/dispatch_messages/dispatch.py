"""The `dispatch` route surface: create, claim, inspect, update and repair dispatch runs.

v0.5.2l, one half of the dispatch+messages package.

`_claim_dispatch_once` (422 lines) is the largest single body moved anywhere in this series and moves
WHOLE, byte-identical. `create_dispatch` (320) likewise. Neither is method-split here; the first
method split remains `get_analytics`, and it needs characterization tests first.

Local helpers are the ones used by dispatch handlers and nothing else. Anything shared with the
message handlers lives in `shared.py`, which also owns the borrow shims.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _NATIVE_MANAGED_RUNTIMES, _normalize_runtime, _normalize_session_mode
from service.api_core.liveness import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings, _managed_terminal_backing_enabled
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.ntfy import notify_operator
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import (
    DispatchClaimRequest,
    DispatchControlClaimRequest,
    DispatchControlRequest,
    DispatchControlUpdate,
    DispatchRequest,
    DispatchRunUpdate,
)
from service.api_core.agent_sessions import (
    _agent_tombstone,
    _touch_current_agent_session,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
from service.api_core.serialization import _machine_ids_same_host
from service.api_core.dispatch_run_state import (
    _append_dispatch_control,
    _finalize_dispatch_runs,
    _mark_dispatch_run_answered,
)
from service.api_core.message_store import _delete_messages_by_ids
from service.api_core.validation import _reject_sender_truncated_body
from service.routers.dispatch_messages.shared import (
    VALID_STATUSES,
    _append_terminal_control,
    _auto_handoff_subject_for_run,
    _borrowed_unthreaded_handoff_window_ms,
    _clear_turn_busy_if_no_open_reply_owing_run,
    _close_reconcilable_delivered_runs,
    _close_steered_contracts_for_parent_run,
    _coldstart_refusal_message,
    _console_dispatch_input_body,
    _create_dispatch_runs,
    _delete_messages_where,
    _dispatch_requires_reply,
    _get_recipient_info,
    _has_live_managed_wrapper_child,
    _is_replaceable_auto_handoff_message,
    _link_reply_message_to_dispatch_run,
    _managed_via_wrapper_for_runtime,
    _message_satisfies_reply_contract,
    _message_type_expects_reply,
    _mirror_missing_dispatch_handoff,
    _preflight_live_send_recipients,
    _primary_result_message_id,
    _record_terminal_delivery_contract,
    _resolve_recipient_ids,
    _resolve_reply_parent_message_id,
    _run_contract_reminders_once,
    _touch_agent,
    _wake_agent,
)
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES, _CHANNEL_MANAGED_RUNTIMES
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
    _insert_messages_via_console,
)
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.dispatch_delivery_resolve import _resolve_dispatch_recipient_delivery
from service.api_core.dispatch_start import (
    _coldstart_spawn_request_for_dispatch,
    _ensure_managed_pty_for_dispatch,
)
from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.execution_mode import _agent_execution_mode, _auto_return_resident_to_managed_if_possible
from service.api_core.reply_contract import (
    _dispatch_reply_pending,
    _dispatch_reply_state,
)
from service.api_core.dispatch_text import _pending_dispatch_count
from service.api_core.dispatch_state import _is_delivery_only_claude_run
from service.api_core.dispatch_text import _MERGED_DISPATCH_HEADER
from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.api_core.records import _serialize_dispatch_run_row
from service.api_core.dashboard_run_report import (
    _maybe_report_async_manager_result_to_dashboard,
    _mirror_dashboard_run_summary_to_chat,
)
from service.api_core.claim_gating import (
    _bridge_claim_block_reason,
    _dispatch_conversation_context,
    _has_claimable_steerable_run,
    _release_stale_console_owner_for_claim,
    _turn_busy_holds_delivery,
)
from service.api_core.claim_gating import _mark_dispatch_source_messages_read
from service.api_core.agent_sessions import _adopt_live_resident_driver
from service.dispatch_claim import _claim_dispatch_once
from service.api_core.dispatch_hint import _dispatch_fix_hint

logger = logging.getLogger("aify_comms.routers.dispatch_messages.dispatch")

router = domain_router()


async def _apply_pending_resident_takeover_if_ready(db, agent_id: str) -> bool:
    # Manual ownership model: a resident CLI registration must not take over a
    # managed identity at a turn boundary. Operators use /session-mode.
    return False


async def _claim_dispatch_controls_once(req: DispatchControlClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        machine_id = req.machineId or ""
        if machine_id and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], machine_id):
            await db.rollback()
            return {"ok": True, "controls": []}

        # Claim pending controls for this agent. No filter on run status —
        # Claude resident runs complete immediately on delivery, so their
        # controls would never be claimable under the old ('claimed','running')
        # filter. The channel bridge polls for controls independently and
        # delivers them as notifications regardless of run state.
        controls_cursor = await db.execute(
            """
            SELECT dc.*, dr.target_agent, dr.status as run_status
            FROM dispatch_controls dc
            JOIN dispatch_runs dr ON dr.id = dc.run_id
            WHERE dr.target_agent = ? AND dc.status = 'pending'
              AND (? = '' OR dc.run_id = ?)
            ORDER BY dc.requested_at ASC, dc.id ASC
            LIMIT 20
            """,
            (req.agentId, req.runId or "", req.runId or "")
        )
        controls = await controls_cursor.fetchall()
        if not controls:
            await db.commit()
            return {"ok": True, "controls": []}

        claimed_at = _now()
        results = []
        for control in controls:
            await db.execute(
                "UPDATE dispatch_controls SET status = 'claimed', claim_machine_id = ?, claimed_at = ? WHERE id = ?",
                (machine_id, claimed_at, control["id"])
            )
            results.append({
                "id": control["id"],
                "runId": control["run_id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "requestedAt": control["requested_at"],
                "claimedAt": claimed_at,
            })

        await db.commit()
        return {"ok": True, "controls": results}
    finally:
        await db.close()


# _claim_dispatch_once moved to service/dispatch_claim.py in v0.5.4 — it OWNS its
# transaction (get_db, BEGIN IMMEDIATE, 10 commits, 3 rollbacks), so it is a service-level
# module and not an api_core leaf. This router keeps the long-poll wrapper.


# _maybe_report_async_manager_result_to_dashboard moved to service/api_core/dashboard_run_report.py in v0.5.4.


# _mirror_dashboard_run_summary_to_chat moved to service/api_core/dashboard_run_report.py in v0.5.4.


# _serialize_dispatch_run_row moved to service/api_core/records.py in v0.5.4.


@router.post("/dispatch/claim")
async def claim_dispatch(req: DispatchClaimRequest, request: Request):
    # Long-poll wrapper (2026-06-30): hold the request open until work is claimable
    # or the client-requested wait elapses, instead of the bridge re-polling every 3s.
    # `_claim_dispatch_once` is the unchanged, self-contained atomic claim — calling it
    # repeatedly here is identical to the bridge calling it repeatedly over HTTP, so
    # claim/supersession/grace semantics are untouched. waitMs defaults to 0 → legacy
    # single-attempt behaviour (old bridges keep working unchanged). The per-iteration
    # fallback (3s = the legacy poll interval) bounds latency even if a notify() is missed.
    # See service/longpoll.py + DECISIONS.md "claim endpoints are long-poll".
    def _is_empty(result: dict) -> bool:
        # Keep waiting ONLY for a pure "nothing to do" result. Any actionable signal
        # (a claimed run, or a stopped/release/blockedBy directive) returns immediately.
        return (
            result.get("run") is None
            and not result.get("stopped")
            and not result.get("release")
            and not result.get("blockedBy")
        )

    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_once(req, request),
        _is_empty,
        scope="dispatch",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "run": None},
    )


@router.post("/dispatch/controls/claim")
async def claim_dispatch_controls(req: DispatchControlClaimRequest, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only while the
    # controls list is exactly empty; any pending control returns immediately.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_controls_once(req, request),
        lambda r: r.get("controls") == [],
        scope="control",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "controls": []},
    )


@router.post("/dispatch")
async def create_dispatch(req: DispatchRequest, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    _reject_sender_truncated_body(req.body)
    if req.mode == "message_only":
        raise HTTPException(400, "Dispatch no longer supports mode='message_only'. Use comms_send for normal live messaging or comms_dispatch without message_only for tracked work.")

    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; dispatch was sent unthreaded.'
            )
        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": [], "runs": []}

        not_started = []
        launchable_recipients = []
        console_recipients = {}
        recipient_rows = {}
        settings = await _load_settings(db)
        await _resolve_dispatch_recipient_delivery(console_recipients, db, launchable_recipients, not_started, recipient_rows, recipients, req, settings)

        if req.mode == "require_start" and not_started:
            details = "; ".join(f"{item['targetAgentId']}: {item['reason']}" for item in not_started)
            return {
                "ok": False,
                "error": f"Active dispatch unavailable for: {details}",
                "recipients": recipients,
                "runs": [],
                "notStarted": not_started,
            }

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        source_message_ids = {}
        ts = int(time.time() * 1000)
        for recipient_id in recipients:
            recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
            source_message_ids[recipient_id] = recipient_message_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    recipient_message_id,
                    req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                    req.priority, 0 if recipient_id in console_recipients else 1, resolved_in_reply_to, ts
                )
            )
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=_primary_result_message_id(message_id, recipients),
                reply_type=req.type,
                reply_body=req.body,
            )

        runs = []
        if launchable_recipients:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode=req.mode,
                execution_mode="managed",
                requested_runtime=req.requestedRuntime,
                message_id=message_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=req.steer,
                require_reply=require_reply,
            )
            runs = await _finalize_dispatch_runs(db, runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, runs, settings)

        console_deliveries = []
        for recipient_id, terminal in console_recipients.items():
            terminal_id = str(terminal["terminal_id"] or "").strip()
            recipient_message_id = source_message_ids.get(recipient_id, message_id)
            terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
            control_id = await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=terminal["environment_id"],
                bridge_id=terminal["bridge_id"] or "",
                action="input",
                requested_by=req.from_agent,
                body=_console_dispatch_input_body(
                    req,
                    recipient_id=recipient_id,
                    message_id=recipient_message_id,
                    bracketed_paste=True,
                ),
            )
            submit_control_id = ""
            await _append_terminal_event(
                db,
                terminal_id,
                "terminal_input_requested",
                json.dumps({
                    "requestedBy": req.from_agent,
                    "controlId": control_id,
                    "submitControlId": submit_control_id,
                    "source": "dispatch",
                    "messageId": recipient_message_id,
                }),
            )
            contract_run_id = await _record_terminal_delivery_contract(
                db,
                source_message_id=recipient_message_id,
                from_agent=req.from_agent,
                recipient_id=recipient_id,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                terminal_id=terminal_id,
                control_id=control_id,
                runtime=terminal["runtime"] or "",
            )
            console_deliveries.append({
                "targetAgentId": recipient_id,
                "terminalId": terminal_id,
                "controlId": control_id,
                "contractRunId": contract_run_id,
                "status": "sent_to_console",
            })

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        # Wake any long-polling claim waiters now that work is committed and visible.
        # A new run was queued and/or a steer control / console delivery appended;
        # over-notifying is harmless (a woken waiter that finds nothing re-sleeps).
        longpoll.notify("dispatch")
        longpoll.notify("control")
        if console_deliveries:
            longpoll.notify("terminal-control")
        ws = await _get_ws(request)
        if ws:
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "dispatch_request", {"from": req.from_agent, "subject": req.subject})
            for run in runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        for recipient_id in recipients:
            _wake_agent(recipient_id)

        return {
            "ok": True,
            "messageId": message_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "runs": runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}")
async def get_dispatch_run(run_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")
        ec = await db.execute(
            "SELECT event_type, body, created_at FROM dispatch_events WHERE run_id = ? ORDER BY id ASC LIMIT 200",
            (run_id,)
        )
        events = [
            {"type": event["event_type"], "body": event["body"], "createdAt": event["created_at"]}
            for event in await ec.fetchall()
        ]
        cc = await db.execute(
            """
            SELECT id, from_agent, action, body, status, response_text, source_message_id, requested_at, claimed_at, handled_at
            FROM dispatch_controls WHERE run_id = ? ORDER BY requested_at ASC LIMIT 200
            """,
            (run_id,)
        )
        controls = [
            {
                "id": control["id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "status": control["status"],
                "response": control["response_text"],
                "sourceMessageId": control["source_message_id"],
                "requestedAt": control["requested_at"],
                "claimedAt": control["claimed_at"],
                "handledAt": control["handled_at"],
            }
            for control in await cc.fetchall()
        ]
        blocked_by = None
        if row["status"] == "queued":
            blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
        return {
            "run": _serialize_dispatch_run_row(
                row,
                blocked_by=blocked_by,
                include_body=True,
                include_events=events,
                include_controls=controls,
            )
        }
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}/events")
async def list_dispatch_run_events(
    run_id: str,
    limit: int = Query(50, ge=1),
    before: Optional[int] = Query(None, ge=1),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    db = await get_db()
    try:
        run_cursor = await db.execute("SELECT 1 FROM dispatch_runs WHERE id = ?", (run_id,))
        if not await run_cursor.fetchone():
            raise HTTPException(404, f"Run '{run_id}' not found")

        bounded_limit = min(limit, 50)
        params: list[Any] = [run_id]
        cursor_clause = ""
        direction = "DESC" if order == "desc" else "ASC"
        if before is not None:
            cursor_clause = "AND id < ?" if order == "desc" else "AND id > ?"
            params.append(before)
        params.append(bounded_limit + 1)
        events_cursor = await db.execute(
            f"""
            SELECT id, event_type, body, created_at
            FROM dispatch_events
            WHERE run_id = ? {cursor_clause}
            ORDER BY id {direction}
            LIMIT ?
            """,
            tuple(params),
        )
        rows = await events_cursor.fetchall()
        page = rows[:bounded_limit]
        return {
            "ok": True,
            "runId": run_id,
            "events": [
                {
                    "id": str(event["id"]),
                    "type": event["event_type"],
                    "eventType": event["event_type"],
                    "body": event["body"] or "",
                    "createdAt": event["created_at"],
                }
                for event in page
            ],
            "hasMore": len(rows) > bounded_limit,
            "nextBefore": str(page[-1]["id"]) if len(rows) > bounded_limit and page else "",
            "order": order,
            "limit": bounded_limit,
        }
    finally:
        await db.close()


@router.get("/dispatch/runs")
async def list_dispatch_runs(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        # Read-path-write fix (2026-06-29): _repair_unusable_active_runs scanned the runs table on
        # every poll of this endpoint and is already run by the 60s reconcile loop — removed here so
        # this stays a pure read (no write-txn contention on the single writer).
        # Plan 6 follow-up (2026-05-26): Section C's mode-switch audit
        # inserts synthetic `dispatch_runs` rows with dispatch_mode='audit'
        # to satisfy the dispatch_events.run_id FK constraint. Those rows
        # are never claimed/queued/started — they exist only as audit
        # anchors. Hide them from the listing endpoint so the dashboard's
        # dispatch history view doesn't fill with mode_switch_* entries.
        # Audit anchors are still queryable individually via the
        # per-id endpoint and via dispatch_events.
        query = "SELECT * FROM dispatch_runs WHERE (dispatch_mode IS NULL OR dispatch_mode != 'audit')"
        params = []
        if agentId:
            query += " AND target_agent = ?"
            params.append(agentId)
        if fromAgent:
            query += " AND from_agent = ?"
            params.append(fromAgent)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        # Perf (audit 2026-06-28): batch the per-run source-controls lookup into ONE query keyed
        # by all run_ids on the page, instead of a sub-query per row (was ~80 queries/poll on the
        # dashboard's 15s cycle → 1). Read-only; output is identical (same fields, ASC order, the
        # per-run 50 cap is applied in Python below).
        run_ids = [row["id"] for row in rows]
        controls_by_run: dict[str, list[dict[str, Any]]] = {}
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            controls_cursor = await db.execute(
                f"""
                SELECT run_id, id, action, status, source_message_id, response_text
                FROM dispatch_controls
                WHERE run_id IN ({placeholders}) AND source_message_id != ''
                ORDER BY requested_at ASC
                """,
                run_ids,
            )
            for control in await controls_cursor.fetchall():
                controls_by_run.setdefault(control["run_id"], []).append({
                    "id": control["id"],
                    "action": control["action"],
                    "status": control["status"],
                    "sourceMessageId": control["source_message_id"],
                    "response": control["response_text"] or "",
                })
        runs = []
        for row in rows:
            blocked_by = None
            if row["status"] == "queued":
                blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
            payload = _serialize_dispatch_run_row(row, blocked_by=blocked_by)
            source_controls = controls_by_run.get(row["id"], [])[:50]
            if source_controls:
                payload["sourceControls"] = source_controls
            runs.append(payload)
        return {"runs": runs}
    finally:
        await db.close()


@router.post("/dispatch/handoffs/repair")
async def repair_dispatch_handoffs(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        closed_delivered = await _close_reconcilable_delivered_runs(db, limit=limit)
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT ?
            """,
            (max(1, limit - len(closed_delivered)),),
        )
        rows = await cursor.fetchall()
        mirrored = []
        dashboard_reports = []
        skipped_delivery_only = 0
        skipped = 0
        for row in rows:
            if _is_delivery_only_claude_run(row):
                skipped_delivery_only += 1
                continue
            message_id = await _mirror_missing_dispatch_handoff(db, row)
            if message_id:
                mirrored.append({"runId": row["id"], "messageId": message_id})
            else:
                skipped += 1

        report_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 0
              AND status = 'completed'
              AND COALESCE(summary, '') != ''
            ORDER BY requested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        report_rows = await report_cursor.fetchall()
        for row in report_rows:
            message_id = await _maybe_report_async_manager_result_to_dashboard(db, row)
            if message_id:
                dashboard_reports.append({"runId": row["id"], "messageId": message_id})

        await db.commit()
        ws = await _get_ws(request)
        if ws and (mirrored or dashboard_reports or closed_delivered):
            await ws.broadcast(
                "dispatch_handoffs_repaired",
                {"mirrored": len(mirrored), "dashboardReports": len(dashboard_reports), "closedDelivered": len(closed_delivered)},
            )
        return {
            "ok": True,
            "mirrored": len(mirrored),
            "dashboardReports": len(dashboard_reports),
            "closedDelivered": len(closed_delivered),
            "skippedDeliveryOnly": skipped_delivery_only,
            "skipped": skipped,
            "runs": mirrored,
            "reports": dashboard_reports,
            "closed": closed_delivered,
        }
    finally:
        await db.close()


@router.post("/dispatch/runs/{run_id}/control")
async def request_dispatch_control(run_id: str, req: DispatchControlRequest, request: Request):
    action = (req.action or "").strip().lower()
    if action not in {"interrupt", "steer"}:
        raise HTTPException(400, "Unsupported control action")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(404, f"Run '{run_id}' not found")
        if run["status"] not in {"claimed", "running"}:
            raise HTTPException(409, f"Run '{run_id}' is not active")

        control_id = await _append_dispatch_control(
            db,
            run_id,
            from_agent=req.from_agent or "",
            action=action,
            body=req.body or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_requested", {"runId": run_id, "controlId": control_id, "action": action})
        return {"ok": True, "controlId": control_id, "runId": run_id, "action": action, "status": "pending"}
    finally:
        await db.close()


@router.patch("/dispatch/controls/{control_id}")
async def update_dispatch_control(control_id: str, req: DispatchControlUpdate, request: Request):
    if req.status not in {"completed", "failed"}:
        raise HTTPException(400, "Unsupported control status")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        control = await cursor.fetchone()
        if not control:
            raise HTTPException(404, f"Control '{control_id}' not found")

        handled_at = _now()
        await db.execute(
            "UPDATE dispatch_controls SET status = ?, response_text = ?, handled_at = ? WHERE id = ?",
            (req.status, req.response or "", handled_at, control_id)
        )
        if req.status == "completed" and (control["source_message_id"] or "").strip():
            run_cursor = await db.execute(
                "SELECT target_agent FROM dispatch_runs WHERE id = ?",
                (control["run_id"],),
            )
            run = await run_cursor.fetchone()
            if run and (run["target_agent"] or "").strip():
                msg_cursor = await db.execute(
                    "SELECT 1 FROM messages WHERE id = ?",
                    ((control["source_message_id"] or "").strip(),),
                )
                if await msg_cursor.fetchone():
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        ((control["source_message_id"] or "").strip(), run["target_agent"], handled_at),
                    )
        await _append_dispatch_event(
            db,
            control["run_id"],
            f"control:{control['action']}:{req.status}",
            req.response or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_updated", {"controlId": control_id, "status": req.status})
        return {"ok": True, "controlId": control_id, "status": req.status}
    finally:
        await db.close()


@router.patch("/dispatch/runs/{run_id}")
async def update_dispatch_run(run_id: str, req: DispatchRunUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")

        updates = []
        params = []
        now = _now()
        current_status = str(row["status"] or "").strip().lower()
        requested_status = str(req.status or "").strip().lower()
        effective_status = req.status
        if current_status in _DISPATCH_TERMINAL_STATUSES and requested_status != current_status:
            effective_status = None

        if effective_status:
            updates.append("status = ?")
            params.append(effective_status)
            if effective_status == "running" and not row["started_at"]:
                updates.append("started_at = ?")
                params.append(now)
            if effective_status in _DISPATCH_TERMINAL_STATUSES:
                updates.append("finished_at = ?")
                params.append(now)
        if req.summary is not None:
            updates.append("summary = ?")
            params.append(req.summary)
        if req.error is not None:
            updates.append("error_text = ?")
            params.append(req.error)
        if req.resultMessageId is not None:
            normalized_result_message_id = str(req.resultMessageId or "").strip()
            if normalized_result_message_id or not str(row["result_message_id"] or "").strip():
                updates.append("result_message_id = ?")
                params.append(normalized_result_message_id)
        if req.externalThreadId is not None:
            updates.append("external_thread_id = ?")
            params.append(req.externalThreadId)
        if req.externalTurnId is not None:
            updates.append("external_turn_id = ?")
            params.append(req.externalTurnId)
        if req.runtime is not None:
            updates.append("runtime = ?")
            params.append(req.runtime)
        if req.requireReply is not None:
            updates.append("require_reply = ?")
            params.append(1 if req.requireReply else 0)

        if updates:
            params.append(run_id)
            await db.execute(f"UPDATE dispatch_runs SET {', '.join(updates)} WHERE id = ?", params)
            await _invalidate_agent_live_state(db, row["target_agent"])
            if effective_status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{effective_status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                mirrored_message_id = await _mirror_missing_dispatch_handoff(db, refreshed_row)
                dashboard_message_id = await _mirror_dashboard_run_summary_to_chat(db, refreshed_row)
                result_message_id = str((refreshed_row["result_message_id"] if refreshed_row else "") or mirrored_message_id or dashboard_message_id or "").strip()
                await _close_steered_contracts_for_parent_run(
                    db,
                    refreshed_row,
                    result_message_id=result_message_id,
                )
                await _maybe_report_async_manager_result_to_dashboard(db, refreshed_row)
                if refreshed_row:
                    # Send-deadlock fix (2026-06-02): an rr=0 channel/resident
                    # delivery that the bridge just marked completed is NOT
                    # sustained work — clear the recipient's turn_busy (which the
                    # delivery re-pulse left stamped) so a queued send isn't held
                    # behind a phantom turn for up to 120s. rr=1 runs keep their
                    # turn_busy and clear via _mark_dispatch_run_answered when the
                    # reply lands; the guard ensures we never clear while another
                    # rr=1 turn is still open (anti-feedback-loop invariant).
                    if (
                        effective_status == "completed"
                        and not _row_require_reply(refreshed_row)
                        and str((refreshed_row["execution_mode"] or "")).strip().lower() in {"channel", "resident"}
                    ):
                        await _clear_turn_busy_if_no_open_reply_owing_run(
                            db, refreshed_row["target_agent"], run_id
                        )
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if effective_status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )

        # MC1 (2026-06-06): only persist a status that is in the 8-status vocabulary.
        # Delivery PATCHes historically sent a non-vocab agentStatus:"active", which got
        # written raw into agents.status and leaked to the dashboard as a 9th status. Now an
        # out-of-vocab value is ignored (status is DERIVED from turn/liveness signals anyway);
        # only an explicit valid operator/runtime status is written. last_seen still refreshes.
        if req.agentStatus and req.agentStatus in VALID_STATUSES:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
                (req.agentStatus, now, row["target_agent"])
            )
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            await _touch_current_agent_session(
                db,
                row["target_agent"],
                _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {},
                now,
            )

        if req.appendEvent:
            await _append_dispatch_event(db, run_id, req.eventType or "info", req.appendEvent)

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_updated", {"runId": run_id, "status": effective_status or row["status"]})
        return {"ok": True, "runId": run_id}
    finally:
        await db.close()
