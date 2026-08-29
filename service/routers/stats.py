"""The aggregate stats route.

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

from fastapi import Request

from service.api_core.routing import domain_router
from service.api_core.serialization import _iso_from_ms
from service.db import get_db
from service.models import validate_model_shape

logger = logging.getLogger("aify_comms.routers.stats")

router = domain_router()


@router.get("/stats")
async def get_stats(request: Request):
    db = await get_db()
    try:
        # Read-path-write fix (2026-06-29): _repair_unusable_active_runs scanned the runs table on
        # every poll of this stats endpoint. It already runs in the 60s reconcile loop AND on every
        # GET /agents poll (the constantly-polled live roster), so the copy here was redundant
        # write-txn contention — removed so /stats is a pure read.
        agents_c = await db.execute("SELECT COUNT(*) FROM agents")
        agents = (await agents_c.fetchone())[0]

        environments_c = await db.execute("SELECT COUNT(*) FROM environments WHERE status != 'forgotten'")
        environments = (await environments_c.fetchone())[0]

        spawn_c = await db.execute("SELECT status, COUNT(*) as cnt FROM spawn_requests GROUP BY status")
        spawn_by_status = {row["status"]: row["cnt"] for row in await spawn_c.fetchall()}

        sessions_c = await db.execute("SELECT COUNT(*) FROM agent_sessions WHERE status IN ('starting','running')")
        active_sessions = (await sessions_c.fetchone())[0]

        total_c = await db.execute("SELECT COUNT(*) FROM messages WHERE source = 'direct'")
        total = (await total_c.fetchone())[0]

        # THREE COUNTS, ONE PASS. These were three queries differing only in which rows they kept:
        # unread-to-a-registered-agent split by source, and unread-to-an-unregistered one. Each
        # re-walked `messages` and re-probed `read_receipts` for the same population.
        #
        # MEASURED on the operator's database, 2026-08-29, with 34,107 messages and 31,913 receipts:
        # the three drove 67,856 read_receipts probes per request (33,440 direct + 488 channel +
        # 33,928 addressed) where one drives 33,928 -- exactly half, because the two source-split
        # queries sum to the third's population. `/stats` is on the dashboard's poll cycle and logged
        # 2,262 SLOW-REQ warnings in the 8.5 hours to 2026-08-29 07:56.
        #
        # The plans differ in the honest direction: the two source-split queries used
        # `idx_messages_source` and the orphan one already SCANNED, so the combined query keeps that
        # one scan and drops the other two passes rather than turning an index seek into a scan.
        # Verified against live data before the change: both forms return (128, 0, 1891).
        unread_c = await db.execute(
            """
            SELECT
              SUM(CASE WHEN a.id IS NOT NULL AND m.source = 'direct'  THEN 1 ELSE 0 END),
              SUM(CASE WHEN a.id IS NOT NULL AND m.source = 'channel' THEN 1 ELSE 0 END),
              SUM(CASE WHEN a.id IS NULL THEN 1 ELSE 0 END)
            FROM messages m
            LEFT JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND r.message_id IS NULL
            """
        )
        # SUM over no rows is NULL, not 0. An empty database would otherwise put `None` into three
        # dashboard counters, which renders as "null" rather than "0" -- the COUNT(*) these replace
        # could never do that.
        unread_row = await unread_c.fetchone()
        unread = int(unread_row[0] or 0)
        channel_unread = int(unread_row[1] or 0)
        orphan_unread = int(unread_row[2] or 0)

        # Today
        today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) * 1000)
        today_c = await db.execute("SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today_start,))
        today = (await today_c.fetchone())[0]
        since_24h_ms = int((time.time() - 24 * 60 * 60) * 1000)
        since_24h_iso = _iso_from_ms(since_24h_ms)
        direct_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'direct' AND timestamp >= ?",
            (since_24h_ms,),
        )
        direct_24h = (await direct_24h_c.fetchone())[0]
        channel_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'channel' AND to_agent IS NULL AND timestamp >= ?",
            (since_24h_ms,),
        )
        channel_24h = (await channel_24h_c.fetchone())[0]
        active_pairs_c = await db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT
                    CASE WHEN from_agent < to_agent THEN from_agent ELSE to_agent END AS a,
                    CASE WHEN from_agent < to_agent THEN to_agent ELSE from_agent END AS b
                FROM messages
                WHERE source = 'direct'
                  AND to_agent IS NOT NULL
                  AND timestamp >= ?
                GROUP BY a, b
            )
            """,
            (since_24h_ms,),
        )
        active_pairs_24h = (await active_pairs_c.fetchone())[0]
        run_failures_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status IN ('failed','cancelled') AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        run_failures_24h = (await run_failures_24h_c.fetchone())[0]
        failed_spawns_24h_c = await db.execute(
            "SELECT COUNT(*) FROM spawn_requests WHERE status = 'failed' AND updated_at >= ?",
            (since_24h_iso,),
        )
        failed_spawns_24h = (await failed_spawns_24h_c.fetchone())[0]
        completed_runs_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status = 'completed' AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        completed_runs_24h = (await completed_runs_24h_c.fetchone())[0]

        # By type
        type_c = await db.execute("SELECT type, COUNT(*) as cnt FROM messages WHERE source = 'direct' GROUP BY type")
        by_type = {row["type"]: row["cnt"] for row in await type_c.fetchall()}

        # By agent
        agent_c = await db.execute("SELECT to_agent, COUNT(*) as cnt FROM messages WHERE to_agent IS NOT NULL GROUP BY to_agent")
        by_agent = {row["to_agent"]: row["cnt"] for row in await agent_c.fetchall()}

        # Shared
        shared_c = await db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(size),0) as total_size FROM shared_artifacts")
        shared_row = await shared_c.fetchone()

        dispatch_c = await db.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM dispatch_runs
            GROUP BY status
            """
        )
        dispatch_by_status = {row["status"]: row["cnt"] for row in await dispatch_c.fetchall()}
        # NOT PENDING. Every row this counts is FINISHED -- completed, failed or cancelled -- and
        # never got a reply. It is a historical total that only grows, and it is not the number of
        # replies currently owed: measured 2026-08-28, this said 118 while the count of OPEN
        # reply-owing runs was ZERO.
        #
        # Nothing reads it today (`dispatch_reply_pending` is in the /stats dead-field ledger), so
        # no operator has been shown 118 outstanding obligations. The name is left alone because
        # renaming an emitted field is a response-shape change; this comment is here so whoever
        # wires it to a tile labels it what it is. `GET /contracts?state=missing_reply` is the
        # closed-without-a-reply list, and `/analytics` carries the genuinely-owed count.
        reply_pending_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
              AND NOT (
                  runtime = 'claude-code'
                  AND status = 'completed'
                  AND COALESCE(summary, '') LIKE 'Delivered to Claude resident session%'
              )
            """
        )
        reply_pending = (await reply_pending_c.fetchone())[0]

        return {
            "agents": agents,
            "environments": environments,
            "spawn_requests_total": sum(spawn_by_status.values()),
            "spawn_requests_by_status": spawn_by_status,
            "active_sessions": active_sessions,
            "total_messages": total,
            "unread_messages": unread,
            "channel_unread_messages": channel_unread,
            "orphan_unread_messages": orphan_unread,
            "messages_today": today,
            "direct_messages_24h": direct_24h,
            "channel_posts_24h": channel_24h,
            "active_dm_pairs_24h": active_pairs_24h,
            "run_failures_24h": run_failures_24h,
            "failed_spawns_24h": failed_spawns_24h,
            "completed_runs_24h": completed_runs_24h,
            "messages_by_type": by_type,
            "messages_by_agent": by_agent,
            "shared_files": shared_row["cnt"],
            "shared_size_bytes": shared_row["total_size"],
            "shared_size_mb": round(shared_row["total_size"] / 1048576, 2),
            "dispatch_runs_total": sum(dispatch_by_status.values()),
            "dispatch_runs_by_status": dispatch_by_status,
            "dispatch_reply_pending": reply_pending,
        }
    finally:
        await db.close()
