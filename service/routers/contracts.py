"""Work-contract routes: list open contracts, repair read receipts, run reminders.

v0.5.2c. A route domain extracted from `service/routers/api_v2.py`, built with `domain_router()` so
it cannot be missing the `JsonApiRoute` lock-retry.

NO TAGS ON THIS ROUTER. The parent applies `tags=["api"]` when it includes this one and FastAPI
COMBINES them — declaring the tag here too produced `tags=["api","api"]` on the first domain, which
is visible in the OpenAPI spec and invisible to everything except the route metadata gate.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router
from service.api_core.reply_contract import _contract_list_query
from service.api_core.serialization import _iso_from_ms, _row_require_reply
from service.api_core.settings import DEFAULT_SETTINGS, _invalidate_settings_cache, _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.models import validate_model_shape

logger = logging.getLogger("aify_comms.routers.contracts")

router = domain_router()


def _contract_state(*a, **k):
    """BORROWED: also used by `_contract_reminder_due`, which has not moved."""
    from service.control_plane import _contract_state as _impl

    return _impl(*a, **k)


def _dispatch_reply_state(*a, **k):
    """BORROWED: also used by `_dispatch_reply_pending` and `_serialize_dispatch_run_row`."""
    from service.control_plane import _dispatch_reply_state as _impl

    return _impl(*a, **k)




async def _run_contract_reminders_once(*a, **k):
    """BORROWED from the router: still used by handlers that have not moved yet.

    Function-scope import, so there is no module-level cycle — `api_v2` imports this domain at
    import time, and this reaches back only when called, long after the router is loaded.
    """
    from service.control_plane import _run_contract_reminders_once as _impl

    return await _impl(*a, **k)


async def _mark_dispatch_source_messages_read(*a, **k):
    """BORROWED from the router: still used by handlers that have not moved yet.

    Function-scope import, so there is no module-level cycle — `api_v2` imports this domain at
    import time, and this reaches back only when called, long after the router is loaded.
    """
    from service.control_plane import _mark_dispatch_source_messages_read as _impl

    return await _impl(*a, **k)


def _contract_row_to_dict(row, *, settings: dict[str, Any], now_s: Optional[float] = None) -> dict[str, Any]:
    state = _contract_state(row, settings=settings, now_s=now_s)
    body = str((row["message_body"] if row and "message_body" in row.keys() else "") or row["body"] or "")
    result_body = str((row["result_body"] if row and "result_body" in row.keys() else "") or "")
    result_message_id = str(row["result_message_id"] or "").strip()
    reply_state = _dispatch_reply_state(row)
    if state["replyExpected"] and reply_state == "not_required":
        reply_state = "sent" if result_message_id else "awaiting"
    return {
        "id": row["id"],
        "messageId": row["message_id"] or "",
        "from": row["from_agent"],
        "targetAgentId": row["target_agent"],
        "type": row["message_type"],
        "subject": row["subject"] or "",
        "preview": body[:420],
        "priority": row["priority"] or "normal",
        "status": row["status"],
        "runtime": row["runtime"] or "",
        "requireReply": _row_require_reply(row),
        "replyState": reply_state,
        "resultMessageId": result_message_id,
        "resultPreview": result_body[:420],
        "requestedAt": row["requested_at"],
        "claimedAt": row["claimed_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "sourceReadAt": row["source_read_at"] or "",
        "lastReminderAt": row["last_reminder_at"] or "",
        **state,
    }


@router.get("/contracts")
async def list_work_contracts(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    state: Optional[str] = Query(None, pattern="^(open|overdue|working|queued|seen|sent|missing_reply|failed|answered|closed)$"),
    category: Optional[str] = Query(None, pattern="^(direct|channel|self_wake)$"),
    includeClosed: bool = Query(False),
    limit: int = Query(120, ge=1, le=500),
):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        where = []
        params: list[Any] = []
        if agentId:
            where.append("AND r.target_agent = ?")
            params.append(agentId)
        if fromAgent:
            where.append("AND r.from_agent = ?")
            params.append(fromAgent)
        if category == "direct":
            where.append("AND r.from_agent != r.target_agent AND COALESCE(m.source, 'direct') != 'channel'")
        elif category == "channel":
            where.append("AND COALESCE(m.source, '') = 'channel'")
        elif category == "self_wake":
            where.append("AND r.from_agent = r.target_agent")
        stale_hours = max(1, int(settings.get("contract_stale_hours", 24) or 24))
        normalized_state = str(state or "").strip().lower()
        if normalized_state == "open":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('completed','failed','cancelled')")
        elif normalized_state == "answered":
            where.append("AND COALESCE(r.result_message_id, '') != ''")
        elif normalized_state == "closed":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'completed' AND r.require_reply = 0")
        elif normalized_state == "missing_reply":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'completed'")
        elif normalized_state == "failed":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status IN ('failed','cancelled')")
        elif normalized_state == "working":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status IN ('claimed','running')")
        elif normalized_state == "queued":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status = 'queued'")
        elif normalized_state == "overdue":
            reminder_minutes = max(1, int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]))
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('completed','failed','cancelled') AND datetime(r.requested_at) <= datetime('now', ?)")
            params.append(f"-{reminder_minutes} minutes")
        elif normalized_state == "seen":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('queued','claimed','running','completed','failed','cancelled') AND COALESCE(rr.read_at, '') != ''")
        elif normalized_state == "sent":
            where.append("AND COALESCE(r.result_message_id, '') = '' AND r.status NOT IN ('queued','claimed','running','completed','failed','cancelled') AND COALESCE(rr.read_at, '') = ''")

        closed_state_requested = normalized_state in {"answered", "closed", "missing_reply", "failed"}
        if includeClosed or closed_state_requested:
            where.append(
                """
                AND (
                    COALESCE(r.result_message_id, '') = ''
                    OR r.status IN ('queued','claimed','running')
                    OR datetime(COALESCE(r.finished_at, r.requested_at)) >= datetime('now', ?)
                )
                """
            )
            params.append(f"-{stale_hours} hours")
        else:
            where.append(
                """
                AND COALESCE(r.result_message_id, '') = ''
                AND r.status NOT IN ('completed','failed','cancelled')
                """
            )
        params.append(limit)
        cursor = await db.execute(_contract_list_query(where_sql="\n".join(where)), params)
        now_s = time.time()
        rows = [_contract_row_to_dict(row, settings=settings, now_s=now_s) for row in await cursor.fetchall()]
        if normalized_state == "open":
            rows = [row for row in rows if row["state"] in {"sent", "seen", "queued", "working", "overdue"}]
        elif normalized_state:
            rows = [row for row in rows if row["state"] == normalized_state]

        summary = {
            "total": len(rows),
            "open": sum(1 for row in rows if row["state"] in {"sent", "seen", "queued", "working", "overdue"}),
            "overdue": sum(1 for row in rows if row["overdue"]),
            "working": sum(1 for row in rows if row["state"] == "working"),
            "queued": sum(1 for row in rows if row["state"] == "queued"),
            "missingReply": sum(1 for row in rows if row["state"] == "missing_reply"),
            "answered": sum(1 for row in rows if row["state"] == "answered"),
            "selfWake": sum(1 for row in rows if row["category"] == "self_wake"),
            "channel": sum(1 for row in rows if row["category"] == "channel"),
        }
        return {"ok": True, "summary": summary, "contracts": rows, "settings": {
            "replyContractsEnabled": bool(settings.get("reply_contracts_enabled", True)),
            "replyReminderMinutes": int(settings.get("reply_reminder_minutes", DEFAULT_SETTINGS["reply_reminder_minutes"]) or DEFAULT_SETTINGS["reply_reminder_minutes"]),
            "replyReminderRepeatMinutes": int(settings.get("reply_reminder_repeat_minutes", DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]) or DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]),
            "replyReminderMaxCount": max(0, int(settings.get("reply_reminder_max_count", 0) or 0)),
            "contractStaleHours": int(settings.get("contract_stale_hours", 24) or 24),
        }}
    finally:
        await db.close()


@router.post("/contracts/hygiene/repair-read-receipts")
async def repair_contract_read_receipts(request: Request, limit: int = Query(500, ge=1, le=2000)):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE COALESCE(message_id, '') != ''
              AND status IN ('claimed','running','completed','failed','cancelled')
            ORDER BY requested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        now = _now()
        repaired = 0
        for row in await cursor.fetchall():
            repaired += await _mark_dispatch_source_messages_read(db, row, row["target_agent"], now)
        await db.commit()
        ws = await _get_ws(request)
        if ws and repaired:
            await ws.broadcast("contract_read_receipts_repaired", {"count": repaired})
        return {"ok": True, "repaired": repaired}
    finally:
        await db.close()


@router.post("/contracts/reminders/run")
async def run_contract_reminders(
    request: Request,
    runId: Optional[str] = None,
    dryRun: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        payload = await _run_contract_reminders_once(db, request=request, run_id=runId, dry_run=dryRun, limit=limit)
        await db.commit()
        return payload
    finally:
        await db.close()
