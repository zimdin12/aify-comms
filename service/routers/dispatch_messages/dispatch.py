"""The `dispatch` route surface: create, claim, inspect, update and repair dispatch runs.

v0.5.2l, one half of the dispatch+messages package.

`_claim_dispatch_once` (422 lines) is the largest single body moved anywhere in this series and moves
WHOLE, byte-identical. `create_dispatch` (320) likewise. Neither is method-split here; the first
method split remains `get_analytics`, and it needs characterization tests first.

Local helpers are the ones used by dispatch handlers and nothing else. Anything shared with the
message handlers lives in `shared.py`, which also owns the borrow shims.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import HTTPException, Request

from service import longpoll
from service.api_core.claim_emptiness import dispatch_claim_is_empty
from service.api_core.events import _append_dispatch_event
from service.api_core.dispatch_run_settlement import _settle_terminated_dispatch_run
from service.api_core.reply_expectation import (
    _dispatch_requires_reply,
    _message_type_expects_reply,
)
from service.api_core.reply_linking import _link_reply_message_to_dispatch_run
from service.api_core.console_input_queue import _queue_console_inputs_for_dispatch
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime
from service.api_core.live_process_probes import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.serialization import (
    _json_loads_or,
)
from service.api_core.settings import _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import (
    DispatchClaimRequest,
    DispatchRequest,
    DispatchRunUpdate,
)
from service.api_core.agent_sessions import (
    _touch_current_agent_session,
)
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.validation import _reject_sender_truncated_body
from service.api_core.agent_sessions import _touch_agent
from service.api_core.dispatch_runs import _create_dispatch_runs
from service.api_core.status_refresh import _get_recipient_info
from service.longpoll import _wake_agent
from service.status_engine import VALID_STATUSES
from service.routers.dispatch_messages.shared import (
    _primary_result_message_id,
    _resolve_recipient_ids,
    _resolve_reply_parent_message_id,
)
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
)
from service.api_core.dispatch_delivery_resolve import _resolve_dispatch_recipient_delivery
from service.api_core.dispatch_state import _DISPATCH_TERMINAL_STATUSES
from service.dispatch_claim import _claim_dispatch_once

logger = logging.getLogger("aify_comms.routers.dispatch_messages.dispatch")

router = domain_router()


# _apply_pending_resident_takeover_if_ready moved to service/api_core/dispatch_run_settlement.py in
# v0.5.4 — it travelled with the terminal-status settlement block, which was its only caller.


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
    # The emptiness predicate moved to service/api_core/claim_emptiness.py in v0.5.4. It was a
    # nested `def` here, so nothing could import it and nothing ever ran it: `longpoll` reads
    # `if wait_ms <= 0 or not is_empty(result)`, and every test used the default waitMs=0.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_once(req, request),
        dispatch_claim_is_empty,
        scope="dispatch",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "run": None},
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
        await _queue_console_inputs_for_dispatch(
            db, req, message_id, console_recipients, console_deliveries, source_message_ids,
            resolved_in_reply_to,
        )

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
        # NORMALISED, because everything downstream assumes it already is. The guard below has always
        # lowercased for its comparison; the value that gets WRITTEN did not, and it has three
        # consumers that each test it against a lowercase literal: the column itself, the
        # `== "running"` check that stamps started_at, and the `in _DISPATCH_TERMINAL_STATUSES`
        # membership that settles the run. A status of "Completed" passed the guard, was written
        # verbatim, matched neither check, and then matched no reconciler either -- every dispatch
        # sweep in `service/reconcilers/dispatch_lifecycle.py` and `dispatch_queue.py` selects on the
        # lowercase members of `_DISPATCH_TERMINAL_STATUSES` and its siblings. The run is stranded:
        # require_reply never settles and cleanup never deletes it.
        #
        # The members are NAMED rather than quoted here on purpose: a comment that spells a status set
        # out is a second copy of it, which is how the `lost` incident happened, and
        # `test_status_set_literal_twins_are_frozen.py` catches exactly that -- it caught this comment.
        #
        # No live defect today -- the bridge sends five lowercase literals (completed, delivered,
        # failed, queued, running) and nothing else writes here. But `status` is `Optional[str]` on
        # the model with no validator, the bridge is host-side and routinely a different build, and
        # the guard one line up already proves the author expected case to vary. This is the `lost`
        # incident's exact shape on a table that has no status vocabulary gate to catch it.
        effective_status = requested_status or None
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
            await _settle_terminated_dispatch_run(db, run_id, effective_status, now, request)

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
