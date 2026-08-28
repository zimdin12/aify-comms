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
import time
from typing import Any, Optional

from fastapi import Query, Request

from service.api_core.routing import domain_router
from service.api_core.reply_contract import _contract_list_query
from service.api_core.serialization import _row_require_reply
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.models import validate_model_shape
from service.api_core.reply_contract import _dispatch_reply_state
from service.api_core.claim_gating import _mark_dispatch_source_messages_read
from service.api_core.reply_contract import _contract_state

logger = logging.getLogger("aify_comms.routers.contracts")

router = domain_router()








# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.
from service.api_core.dispatch_sweeps import _run_contract_reminders_once  # noqa: E402




#: How many pre-filter rows a state-filtered query may scan before it stops and says so.
#:
#: A ceiling on work, not a page size. The SQL predicate for a state is wider than the derivation it
#: stands in for, so the scan has to read past the requested page to fill it. 500 is the endpoint's
#: own documented maximum `limit`, which makes the worst case here the same as the worst case a
#: caller could already ask for.
CONTRACT_STATE_SCAN_LIMIT = 500


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
        # THE LIMIT IS APPLIED AFTER THE DERIVED-STATE FILTER, not before it.
        #
        # A state is decided by `_contract_state`, in Python, from settings and several columns --
        # "what is owed an answer is wider than the flag", as reply_contract.py puts it. The SQL
        # above cannot express that, so it is a PRE-FILTER: deliberately wider than the state it is
        # standing in for. Applying `LIMIT` to that wider set and then filtering means the rows the
        # caller asked for can be discarded before they are ever counted.
        #
        # MEASURED on the live service, 2026-08-28, one query at five page sizes:
        #
        #     state=missing_reply   limit=80  -> 0 rows    summary.total 0
        #                           limit=120 -> 20 rows   summary.total 20
        #                           limit=200 -> 62 rows   summary.total 62
        #
        # 62 is the true count. `missing_reply` and `closed` share a SQL predicate -- both are
        # `result_message_id = '' AND status = 'completed'` -- so the newest 80 rows matching it were
        # all `closed`, and the answer to "what is missing a reply" was zero. The summary was wrong
        # by the same mechanism, which is worse than the list being short: a caller asking only for a
        # COUNT got a number that depended entirely on the page size they happened to pass.
        #
        # So: scan a bounded superset, filter, then truncate. `scan_limit` is a ceiling on work, not
        # a page size, and when it is reached the response says so rather than quietly implying the
        # list is complete -- a truncated answer that looks whole is the thing being fixed.
        scan_limit = max(limit, CONTRACT_STATE_SCAN_LIMIT) if normalized_state else limit
        params.append(scan_limit)
        cursor = await db.execute(_contract_list_query(where_sql="\n".join(where)), params)
        now_s = time.time()
        fetched = await cursor.fetchall()
        rows = [_contract_row_to_dict(row, settings=settings, now_s=now_s) for row in fetched]
        if normalized_state == "open":
            rows = [row for row in rows if row["state"] in {"sent", "seen", "queued", "working", "overdue"}]
        elif normalized_state:
            rows = [row for row in rows if row["state"] == normalized_state]
        scan_exhausted = len(fetched) >= scan_limit
        # The SUMMARY describes everything the filter matched; `contracts` is the page. Counting only
        # the page would leave `summary.total` moving with the page size, which is half of the defect
        # being fixed -- a caller who wants a count should not have to ask for every row to get one.
        matched = rows
        rows = rows[:limit]

        summary = {
            "total": len(matched),
            "open": sum(1 for row in matched if row["state"] in {"sent", "seen", "queued", "working", "overdue"}),
            "overdue": sum(1 for row in matched if row["overdue"]),
            "working": sum(1 for row in matched if row["state"] == "working"),
            "queued": sum(1 for row in matched if row["state"] == "queued"),
            "missingReply": sum(1 for row in matched if row["state"] == "missing_reply"),
            "answered": sum(1 for row in matched if row["state"] == "answered"),
            "selfWake": sum(1 for row in matched if row["category"] == "self_wake"),
            "channel": sum(1 for row in matched if row["category"] == "channel"),
        }
        # `truncated` is true when the SCAN hit its ceiling, so `summary` is a floor rather than a
        # total. Reported instead of inferred: a caller cannot tell a complete answer from a capped
        # one by looking at the row count, because the filter makes those two numbers differ.
        return {"ok": True, "truncated": scan_exhausted, "summary": summary, "contracts": rows, "settings": {
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
