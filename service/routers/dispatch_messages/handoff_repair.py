"""The handoff repair sweep: one endpoint that exists because delivery can be lost silently.

Extracted from `service/routers/dispatch_messages/dispatch.py` in v0.5.4. Closure measured before
the move — `api_core`, `reconcilers` and `service` leaves only, nothing local to `dispatch.py`.

THIS IS MAINTENANCE, NOT DISPATCH, and that is the whole reason it gets its own file. Everything else
in the package answers a request about a run; this walks runs that already went wrong and repairs
them — mirroring a handoff message that never arrived, closing runs the reconciler can prove were
delivered, and reporting an async manager result the dashboard never received. It is the endpoint an
operator reaches for after something has already failed, and it reads as an ordinary dispatch route
only because of where it happened to live.

Byte-identical body and route decorator.
"""

from __future__ import annotations

from fastapi import Query, Request

from service.api_core.dashboard_run_report import _maybe_report_async_manager_result_to_dashboard
from service.api_core.dispatch_state import _is_delivery_only_claude_run
from service.api_core.dispatch_sweeps import _mirror_missing_dispatch_handoff
from service.api_core.routing import domain_router
from service.api_core.ws import _get_ws
from service.db import get_db
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs

router = domain_router()



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
