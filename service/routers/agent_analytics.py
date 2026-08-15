"""Analytics for ONE agent: its runs, its worked minutes, its recent activity.

Extracted from `service/routers/analytics.py` in v0.5.4. Closure measured before the move: `fastapi`,
`service.db`, and the worked-span ceiling — which went to `api_core/tuning.py` because the fleet
handlers left behind read it too, and a constant shared by two modules that do not import each other
needs a home that neither owns.

PER-AGENT AND FLEET-WIDE ARE DIFFERENT QUESTIONS, which is the split. The fleet handlers aggregate
across everything and answer "how is the team doing"; this one reconstructs a single agent's history
and answers "what has this one been doing". They shared a file because they share a URL prefix.

THE CEILING IS A DATA-QUALITY RULE, NOT A TIMER, and both halves depend on it for the same reason: a
dispatch run that was claimed and then abandoned is force-closed by a 24h reaper, leaving a completed
row whose claimed-to-finished span is ~24h of NON-work. Counting those inflated one agent's "working
total" to 909 hours. Anything above the ceiling is a reaped run and contributes zero.

Body and route decorator are byte-identical to what stood in `analytics.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

from fastapi import Request

from service.api_core.routing import domain_router
from service.api_core.tuning import WORKED_SPAN_CEILING_SECONDS
from service.db import get_db

router = domain_router()



@router.get("/analytics/agent/{agent_id}")
async def get_agent_analytics(agent_id: str, request: Request):
    """Per-agent chat analytics (additive — leaves GET /analytics untouched).

    All counts are over the agent's DIRECT messages (source='direct'). Returns:
      - messageTotal           total direct messages to/from the agent
      - messagesPerHourOfDay   24 buckets keyed on hour-of-day (UTC)
      - byPeer                 direct message counts grouped by the other party
      - workingMinutes         minutes the agent spent as a dispatch target

    PITFALL honored: messages.timestamp is epoch-ms INTEGER but
    dispatch_runs.{started,finished}_at are ISO TEXT — working-minutes uses
    julianday() on the run columns (never epoch arithmetic), NULL-guards both
    run timestamps, and clamps the result >= 0.
    """
    db = await get_db()
    try:
        direct_where = "source = 'direct' AND (from_agent = ? OR to_agent = ?)"

        total_c = await db.execute(
            f"SELECT COUNT(*) FROM messages WHERE {direct_where}",
            (agent_id, agent_id),
        )
        message_total = int((await total_c.fetchone())[0])

        # Hour-of-day histogram (UTC) — 0..23, zero-filled.
        hour_counts = {h: 0 for h in range(24)}
        hod_c = await db.execute(
            f"""
            SELECT CAST(strftime('%H', datetime(timestamp / 1000, 'unixepoch')) AS INTEGER) AS hour,
                   COUNT(*) AS cnt
            FROM messages
            WHERE {direct_where}
            GROUP BY hour
            """,
            (agent_id, agent_id),
        )
        for row in await hod_c.fetchall():
            h = row["hour"]
            if h is not None and 0 <= int(h) <= 23:
                hour_counts[int(h)] = int(row["cnt"] or 0)
        messages_per_hour_of_day = [
            {"hour": h, "count": hour_counts[h]} for h in range(24)
        ]

        # Per-peer counts: the other party on each direct message.
        peer_c = await db.execute(
            f"""
            SELECT CASE WHEN from_agent = ? THEN to_agent ELSE from_agent END AS peer,
                   COUNT(*) AS cnt
            FROM messages
            WHERE {direct_where}
            GROUP BY peer
            ORDER BY cnt DESC, peer ASC
            """,
            (agent_id, agent_id, agent_id),
        )
        by_peer = [
            {"peer": row["peer"], "count": int(row["cnt"] or 0)}
            for row in await peer_c.fetchall()
            if row["peer"] is not None
        ]

        # Working minutes from dispatch_runs where this agent is the target.
        # julianday() gives fractional days; *1440 -> minutes. Guard NULL run
        # timestamps and clamp the sum >= 0. Work-start proxy = COALESCE(started_at,
        # claimed_at): production runs go queued→claimed→completed and almost never
        # populate started_at, so the old `started_at IS NOT NULL` gate made this 0
        # for every agent (operator-reported "work amount is 0 for all agents",
        # 2026-06-19). Same fix as the /analytics/pulse working-minutes query.
        work_c = await db.execute(
            """
            SELECT COALESCE(SUM(MAX(0, (julianday(finished_at) - julianday(COALESCE(started_at, claimed_at))) * 1440)), 0)
            FROM dispatch_runs
            WHERE target_agent = ?
              AND COALESCE(started_at, claimed_at) IS NOT NULL
              AND finished_at IS NOT NULL
              AND (julianday(finished_at) - julianday(COALESCE(started_at, claimed_at))) * 86400 <= ?
            """,
            (agent_id, WORKED_SPAN_CEILING_SECONDS),
        )
        working_minutes_raw = (await work_c.fetchone())[0]
        working_minutes = max(0.0, float(working_minutes_raw or 0))

        # ── 2026-06-12 revamp (operator: "really bad stats, not that useful") ──
        # Operationally meaningful additions; everything above is kept for
        # back-compat. All run-time comparisons use julianday() (ISO TEXT-safe).

        sent_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'direct' AND from_agent = ?",
            (agent_id,),
        )
        messages_sent = int((await sent_c.fetchone())[0])
        messages_received = max(0, message_total - messages_sent)

        # Daily in/out activity, last 14 days, zero-filled (UTC days).
        daily = {}
        daily_c = await db.execute(
            """
            SELECT date(timestamp / 1000, 'unixepoch') AS day,
                   SUM(CASE WHEN from_agent = ? THEN 1 ELSE 0 END) AS sent,
                   SUM(CASE WHEN to_agent = ? THEN 1 ELSE 0 END) AS received
            FROM messages
            WHERE source = 'direct' AND (from_agent = ? OR to_agent = ?)
              AND timestamp / 1000 >= CAST(strftime('%s', 'now', '-14 days') AS INTEGER)
            GROUP BY day
            """,
            (agent_id, agent_id, agent_id, agent_id),
        )
        for row in await daily_c.fetchall():
            if row["day"]:
                daily[str(row["day"])] = {
                    "sent": int(row["sent"] or 0),
                    "received": int(row["received"] or 0),
                }
        day_rows = await (await db.execute(
            "SELECT date('now', '-' || value || ' days') AS day FROM "
            "(SELECT 0 AS value UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 "
            "UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9 "
            "UNION SELECT 10 UNION SELECT 11 UNION SELECT 12 UNION SELECT 13) ORDER BY day"
        )).fetchall()
        daily_activity = [
            {"date": str(r["day"]), **daily.get(str(r["day"]), {"sent": 0, "received": 0})}
            for r in day_rows
        ]

        # Dispatch runs targeting this agent, last 7 days.
        runs_c = await db.execute(
            """
            SELECT status, require_reply, requested_at, started_at, finished_at, subject
            FROM dispatch_runs
            WHERE target_agent = ?
              AND dispatch_mode != 'audit'
              AND julianday(requested_at) > julianday('now') - 7
            """,
            (agent_id,),
        )
        runs = await runs_c.fetchall()
        runs_completed = sum(1 for r in runs if str(r["status"] or "") == "completed")
        runs_failed = sum(1 for r in runs if str(r["status"] or "") in ("failed", "cancelled"))
        runs_open = sum(1 for r in runs if str(r["status"] or "") in ("queued", "claimed", "running", "delivered"))
        last_failed_subject = next(
            (str(r["subject"] or "") for r in sorted(runs, key=lambda r: str(r["requested_at"] or ""), reverse=True)
             if str(r["status"] or "") in ("failed", "cancelled")),
            "",
        )

        def _run_minutes(row, start_col):
            try:
                s = str(row[start_col] or "")
                f = str(row["finished_at"] or "")
                if not s or not f:
                    return None
                from datetime import datetime as _dt
                sv = _dt.fromisoformat(s.replace("Z", "+00:00"))
                fv = _dt.fromisoformat(f.replace("Z", "+00:00"))
                m = (fv - sv).total_seconds() / 60.0
                return m if m >= 0 else None
            except Exception:
                return None

        run_durations = sorted(
            m for m in (_run_minutes(r, "started_at") for r in runs if str(r["status"] or "") == "completed")
            if m is not None
        )
        avg_run_minutes = (sum(run_durations) / len(run_durations)) if run_durations else 0.0
        # Reply latency: request arrival → reply landing, rr=1 completed runs only —
        # "how fast does this agent answer".
        reply_latencies = sorted(
            m for m in (
                _run_minutes(r, "requested_at")
                for r in runs
                if str(r["status"] or "") == "completed" and int(r["require_reply"] or 0) == 1
            )
            if m is not None
        )
        median_reply_minutes = (
            reply_latencies[len(reply_latencies) // 2] if reply_latencies else 0.0
        )

        # Open reply contracts RIGHT NOW (not windowed) — what the agent still owes.
        owed_c = await db.execute(
            """
            SELECT COUNT(*) FROM dispatch_runs
            WHERE target_agent = ? AND require_reply = 1
              AND status IN ('queued', 'claimed', 'running', 'delivered')
              AND COALESCE(result_message_id, '') = ''
            """,
            (agent_id,),
        )
        open_contracts = int((await owed_c.fetchone())[0])

        return {
            "ok": True,
            "agentId": agent_id,
            "messageTotal": message_total,
            "messagesPerHourOfDay": messages_per_hour_of_day,
            "byPeer": by_peer,
            "workingMinutes": working_minutes,
            "messagesSent": messages_sent,
            "messagesReceived": messages_received,
            "dailyActivity": daily_activity,
            "runs7d": {
                "completed": runs_completed,
                "failed": runs_failed,
                "open": runs_open,
                "lastFailedSubject": last_failed_subject,
            },
            "avgRunMinutes7d": round(avg_run_minutes, 1),
            "medianReplyMinutes7d": round(median_reply_minutes, 1),
            "openContracts": open_contracts,
        }
    finally:
        await db.close()
