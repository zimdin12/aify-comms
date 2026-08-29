"""Reading dispatch runs: the list, one run in full, and one run's event log.

Extracted from `service/routers/dispatch_messages/dispatch.py` in v0.5.4. The three GET handlers,
with a closure measured before the move: `api_core` and `service` leaves only, nothing local to
`dispatch.py` and nothing from `shared.py`. What is left behind is the WRITE surface — create, claim,
update — which is a different thing to be careful about.

READING A RUN IS WHERE A CALLER FORMS A BELIEF ABOUT IT, so the honesty of these three matters more
than their size. `get_dispatch_run` reports the run's own status AND, separately, whether some other
run is currently blocking the same agent — two different facts that a caller polling for "is it
moving yet" will otherwise conflate into one. The blocking lookup is a live query, not a stored
column, which is why it is asked for here rather than read off the row.

`list_dispatch_runs` filters and bounds. A caller that cannot see its result was capped will treat a
page as the whole set — the same shape as the inbox truncation note, one layer down.

Bodies and route decorators are byte-identical to what stood in `dispatch.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.records import _serialize_dispatch_run_row
from service.api_core.routing import domain_router
from service.db import get_db

router = domain_router()



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
        # ONE MORE ROW THAN ASKED FOR, so the response can say whether this is the whole answer.
        # The dashboard asks for 80 and renders exactly what it gets, and its From / To / runtime
        # dropdowns are BUILT FROM THE ROWS IT RECEIVED -- so an agent whose last run is off the page
        # is not merely absent from the list, it is unselectable, while the empty state invites the
        # operator to "adjust the filters above". Measured on the live database 2026-08-29: a
        # `limit=80` page reached back to 2026-08-26T13:28 and offered ONE distinct sender; a
        # `limit=200` page was also full, so the window is a window at every size the API allows.
        #
        # Same shape as `/sessions` and for the same reason: a bounded page that cannot say it is
        # bounded reads as the whole list. `/contracts` and `/terminals` already report this.
        query += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit + 1)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
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
        # `limit` beside `truncated`: "there are more" without "more than what" leaves a reader
        # unable to judge how much is missing.
        return {"runs": runs, "truncated": truncated, "limit": limit}
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
