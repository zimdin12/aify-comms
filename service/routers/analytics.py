"""The `analytics` route domain: fleet analytics, per-agent analytics, and the pulse.

v0.5.2e. The largest domain moved so far — 687 lines in three handlers — and moved as ONE tag on the
reviewer's ruling, reviewed internally per handler rather than split into three commits.

Read-mostly: these three aggregate over dispatch runs, sessions and messages and return a response.
That is why analytics sits in the small/low-blast-radius group despite its size, and it is why
`get_analytics` (314 lines) is the chosen first target for the METHOD-SPLIT work later. That split is
a SEPARATE tag and needs characterization tests around the endpoint first; the static extraction gate
is necessary and not sufficient for it.

BORROWED, measured: `_agent_wake_mode` and `_compute_agent_status` both have users that have not
moved, so they are reached through function-scope imports rather than copied. `WORKED_SPAN_CEILING_
SECONDS` had no user outside these handlers and moved with them.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include and
FastAPI combines them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import Query, Request

from service.api_core.routing import domain_router
from service.api_core.serialization import _iso_from_ms
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch
from service.db import get_db
from service.env_status import _ENVIRONMENT_HEARTBEAT_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status

logger = logging.getLogger("aify_comms.routers.analytics")

router = domain_router()


def _agent_wake_mode(*a, **k):
    """BORROWED: still used by handlers and serializers that have not moved."""
    from service.control_plane import _agent_wake_mode as _impl

    return _impl(*a, **k)


async def _compute_agent_status(*a, **k):
    """BORROWED: still used by handlers that have not moved."""
    from service.control_plane import _compute_agent_status as _impl

    return await _impl(*a, **k)


# Analytics data-quality ceiling (2026-06-19). NOT a status timer — used only by the
# work-minutes analytics. Dispatch runs go queued→claimed→completed, and a run that is
# claimed but then abandoned/stuck is force-closed by a 24h reaper, leaving a completed row
# whose claimed→finished span is ~24h of NON-work. Counting COALESCE(started_at, claimed_at)→
# finished for those (a regression in 93f44df) inflated "working total" to absurd values
# (sc-architect showed 909h). A real worked span — even a long autonomous run — never
# approaches this; anything above it is a reaped/stuck run and contributes 0 worked minutes.
WORKED_SPAN_CEILING_SECONDS = 4 * 3600


async def _append_daily_message_buckets(daily, today_start_s, count_messages_between):
    """Append the 30 daily buckets, ending with the local day `today_start_s` begins.

    A VOID extraction that appends into a list it is handed, rather than building and
    returning one. That is not a style choice: `daily = []` sits ABOVE the two lines that
    compute `today_struct`/`today_start_s`, and `today_struct` is read further down by the
    monthly series — so a value-returning extraction would either have to reorder the
    caller (the round trip would stop closing) or hand back two values (a shape the gate
    deliberately does not model). Appending is the version that is provable as-is.
    """
    for i in range(29, -1, -1):
        start_s = today_start_s - i * 86400
        daily.append({
            "label": time.strftime("%m-%d", time.localtime(start_s)),
            "start": _iso_from_ms(start_s * 1000),
            "count": await count_messages_between(start_s * 1000, (start_s + 86400) * 1000),
        })


async def _monthly_message_series(today_struct, count_messages_between):
    """The 12 monthly buckets, ending with the month `today_struct` falls in.

    Contiguous and single-live-out, so unlike the daily buckets this one returns its
    series instead of appending into one. `today_struct` is passed because the caller
    computed it for the daily series and still owns it.
    """
    monthly = []
    year = today_struct.tm_year
    month = today_struct.tm_mon
    for i in range(11, -1, -1):
        m = month - i
        y = year
        while m <= 0:
            m += 12
            y -= 1
        next_m = m + 1
        next_y = y
        if next_m > 12:
            next_m = 1
            next_y += 1
        start_s = int(time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)))
        end_s = int(time.mktime((next_y, next_m, 1, 0, 0, 0, 0, 0, -1)))
        monthly.append({
            "label": f"{y}-{m:02d}",
            "start": _iso_from_ms(start_s * 1000),
            "count": await count_messages_between(start_s * 1000, end_s * 1000),
        })
    return monthly


async def _hourly_message_series(now_s, count_messages_between):
    """The 24 hourly message buckets, ending with the hour `now_s` falls in.

    The first method split in this series, extracted from `get_analytics`. Both `now_s` and
    the counting closure are PASSED rather than reached for: the closure is a local of the
    handler, so a helper that captured it would capture the wrong frame — and the
    extract-method gate refuses exactly that shape rather than trusting it.
    """
    hourly = []
    hour_start = (now_s // 3600) * 3600
    for i in range(23, -1, -1):
        start_s = hour_start - i * 3600
        hourly.append({
            "label": time.strftime("%H:00", time.localtime(start_s)),
            "start": _iso_from_ms(start_s * 1000),
            "count": await count_messages_between(start_s * 1000, (start_s + 3600) * 1000),
        })
    return hourly


@router.get("/analytics")
async def get_analytics(request: Request, analytics_range: str = Query("hour", alias="range", pattern="^(hour|day|month|all)$")):
    selected_range = analytics_range
    db = await get_db()
    try:
        settings = await _load_settings(db)
        now_s = int(time.time())
        message_where = """
          (
            (source = 'direct' AND to_agent IS NOT NULL)
            OR (source = 'channel' AND to_agent IS NULL)
          )
        """

        async def count_messages_between(start_ms: int, end_ms: int) -> int:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM messages WHERE {message_where} AND timestamp >= ? AND timestamp < ?",
                (start_ms, end_ms),
            )
            return int((await cursor.fetchone())[0])

        hourly = await _hourly_message_series(now_s, count_messages_between)

        daily = []
        today_struct = time.localtime(now_s)
        today_start_s = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d", today_struct), "%Y-%m-%d")))
        await _append_daily_message_buckets(daily, today_start_s, count_messages_between)

        monthly = await _monthly_message_series(today_struct, count_messages_between)

        all_time_c = await db.execute(
            f"""
            SELECT strftime('%Y-%m', datetime(timestamp / 1000, 'unixepoch')) AS bucket,
                   MIN(timestamp) AS start_ms,
                   COUNT(*) AS cnt
            FROM messages
            WHERE {message_where}
            GROUP BY bucket
            ORDER BY bucket ASC
            """
        )
        all_time = [
            {"label": row["bucket"] or "unknown", "start": _iso_from_ms(int(row["start_ms"] or 0)), "count": int(row["cnt"] or 0)}
            for row in await all_time_c.fetchall()
        ]

        since_s_by_range = {
            "hour": now_s - 24 * 3600,
            "day": now_s - 30 * 86400,
            "month": now_s - 366 * 86400,
        }
        since_s = since_s_by_range.get(selected_range)
        run_where = ""
        run_params: tuple[Any, ...] = ()
        spawn_where = ""
        spawn_params: tuple[Any, ...] = ()
        message_count_where = message_where
        message_count_params: tuple[Any, ...] = ()
        if since_s is not None:
            since_iso = _iso_from_ms(since_s * 1000)
            since_ms = since_s * 1000
            run_where = "WHERE COALESCE(finished_at, requested_at) >= ?"
            run_params = (since_iso,)
            spawn_where = "WHERE updated_at >= ?"
            spawn_params = (since_iso,)
            message_count_where = f"{message_where} AND timestamp >= ?"
            message_count_params = (since_ms,)

        status_c = await db.execute(
            f"SELECT status, COUNT(*) as cnt FROM dispatch_runs {run_where} GROUP BY status",
            run_params,
        )
        runs_by_status = {row["status"]: row["cnt"] for row in await status_c.fetchall()}
        message_total_c = await db.execute(
            f"SELECT COUNT(*) FROM messages WHERE {message_count_where}",
            message_count_params,
        )
        message_total = int((await message_total_c.fetchone())[0])
        spawn_status_c = await db.execute(
            f"SELECT status, COUNT(*) as cnt FROM spawn_requests {spawn_where} GROUP BY status",
            spawn_params,
        )
        spawns_by_status = {row["status"]: row["cnt"] for row in await spawn_status_c.fetchall()}

        agents_c = await db.execute("SELECT * FROM agents")
        agent_rows = await agents_c.fetchall()
        live_agents = 0
        online_agents = 0
        working_agents = 0
        for row in agent_rows:
            mode = _agent_wake_mode(row)
            if mode != "message-only" and mode != "disabled":
                live_agents += 1
            status = await _compute_agent_status(row, db)
            if not status.startswith("offline") and not status.startswith("stale"):
                online_agents += 1
            if status.startswith("working"):
                working_agents += 1

        # N9 (bug-hunt 2026-07-31): this counted STORED status and the card it feeds claims
        # "bridges reachable right now". Nothing ages an environment — every `UPDATE environments`
        # writer is a registration, an explicit disable, or a `last_seen` bump — so a bridge that
        # died uncleanly kept `status='online'` forever and was counted as reachable indefinitely.
        # That is the same false green `aify-doctor`'s env-bridge check exists to prevent (756f3a5),
        # surviving in the surface the operator actually watches. Derive it like every other reader:
        # `_environment_effective_status` IS the liveness truth, and the three sibling cards in this
        # grid were already derived — this was the only raw one.
        env_offline_seconds = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
        env_rows_c = await db.execute("SELECT status, last_seen FROM environments")
        online_environments = sum(
            1
            for env_row in await env_rows_c.fetchall()
            if _environment_effective_status(env_row, offline_seconds=env_offline_seconds)
            in _ENVIRONMENT_HEARTBEAT_STATUSES
        )

        # ── Fleet operational analytics (2026-06-17 round: "real analytics") ──
        # Everything below is additive; all run-time math uses julianday() on the ISO TEXT
        # run columns (never epoch arithmetic on those) and is windowed by `run_where`.
        from datetime import datetime as _dt

        def _iso_to_epoch(value: Any) -> float | None:
            try:
                return _dt.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
            except Exception:
                return None

        # Dispatch success rate over the window (completed vs. completed+failed+cancelled).
        runs_completed_n = int(runs_by_status.get("completed", 0))
        runs_failed_n = int(runs_by_status.get("failed", 0)) + int(runs_by_status.get("cancelled", 0))
        runs_finished_n = runs_completed_n + runs_failed_n
        success_rate = round((runs_completed_n / runs_finished_n) * 100, 1) if runs_finished_n else None

        # Open + overdue reply contracts fleet-wide (NOT windowed — what's outstanding right now).
        # Overdue = an unanswered required reply whose run was requested more than 30 min ago.
        contracts_c = await db.execute(
            """
            SELECT requested_at FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('queued', 'claimed', 'running', 'delivered')
              AND COALESCE(result_message_id, '') = ''
            """
        )
        contract_rows = await contracts_c.fetchall()
        open_reply_contracts = len(contract_rows)
        overdue_cut = now_s - 30 * 60
        overdue_reply_contracts = sum(
            1 for r in contract_rows
            if (_iso_to_epoch(r["requested_at"]) or now_s) < overdue_cut
        )

        # Fleet median reply latency (completed required-reply runs in window), minutes.
        _extra = "status = 'completed' AND require_reply = 1 AND requested_at IS NOT NULL AND finished_at IS NOT NULL"
        reply_where = (f"{run_where} AND {_extra}") if run_where else f"WHERE {_extra}"
        reply_c = await db.execute(
            f"SELECT (julianday(finished_at) - julianday(requested_at)) * 1440 AS mins FROM dispatch_runs {reply_where}",
            run_params,
        )
        reply_mins = sorted(float(r["mins"]) for r in await reply_c.fetchall() if r["mins"] is not None and float(r["mins"]) >= 0)
        fleet_median_reply = round(reply_mins[len(reply_mins) // 2], 1) if reply_mins else None

        # Dispatch outcomes over time — last 14 days, completed vs. failed (stacked), zero-filled.
        outcomes = {}
        out_c = await db.execute(
            """
            SELECT date(COALESCE(finished_at, requested_at)) AS day,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed
            FROM dispatch_runs
            WHERE julianday(COALESCE(finished_at, requested_at)) > julianday('now') - 14
            GROUP BY day
            """
        )
        for row in await out_c.fetchall():
            if row["day"]:
                outcomes[str(row["day"])] = {"completed": int(row["completed"] or 0), "failed": int(row["failed"] or 0)}
        out_day_rows = await (await db.execute(
            "SELECT date('now', '-' || value || ' days') AS day FROM "
            "(SELECT 0 AS value UNION SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 "
            "UNION SELECT 5 UNION SELECT 6 UNION SELECT 7 UNION SELECT 8 UNION SELECT 9 "
            "UNION SELECT 10 UNION SELECT 11 UNION SELECT 12 UNION SELECT 13) ORDER BY day"
        )).fetchall()
        dispatch_outcomes = [
            {"date": str(r["day"]), **outcomes.get(str(r["day"]), {"completed": 0, "failed": 0})}
            for r in out_day_rows
        ]

        # Agent leaderboard — top dispatch targets in the window, with per-agent success rate.
        leader_c = await db.execute(
            f"""
            SELECT target_agent AS agent,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status IN ('failed', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                   COUNT(*) AS total
            FROM dispatch_runs
            {run_where}
            GROUP BY target_agent
            ORDER BY completed DESC, total DESC
            LIMIT 10
            """,
            run_params,
        )
        agent_leaderboard = []
        for row in await leader_c.fetchall():
            if not row["agent"]:
                continue
            comp = int(row["completed"] or 0)
            fail = int(row["failed"] or 0)
            fin = comp + fail
            agent_leaderboard.append({
                "agent": row["agent"],
                "completed": comp,
                "failed": fail,
                "total": int(row["total"] or 0),
                "successRate": round((comp / fin) * 100, 1) if fin else None,
            })

        # Busiest channels — channel-message volume in the window.
        chan_where = "source = 'channel' AND channel IS NOT NULL AND TRIM(channel) != ''"
        chan_params: tuple[Any, ...] = ()
        if since_s is not None:
            chan_where += " AND timestamp >= ?"
            chan_params = (since_s * 1000,)
        chan_c = await db.execute(
            f"SELECT channel, COUNT(*) AS cnt FROM messages WHERE {chan_where} GROUP BY channel ORDER BY cnt DESC LIMIT 8",
            chan_params,
        )
        busiest_channels = [
            {"channel": row["channel"], "count": int(row["cnt"] or 0)}
            for row in await chan_c.fetchall()
        ]

        # Failure reasons — failed/cancelled runs grouped by (truncated) error text, in window.
        fail_extra = "status IN ('failed', 'cancelled')"
        fail_where = (f"{run_where} AND {fail_extra}") if run_where else f"WHERE {fail_extra}"
        fail_c = await db.execute(
            f"""
            SELECT COALESCE(NULLIF(TRIM(error_text), ''), '(no error text recorded)') AS reason, COUNT(*) AS cnt
            FROM dispatch_runs
            {fail_where}
            GROUP BY reason
            ORDER BY cnt DESC
            LIMIT 8
            """,
            run_params,
        )
        failure_reasons = [
            {"reason": str(row["reason"] or "")[:120], "count": int(row["cnt"] or 0)}
            for row in await fail_c.fetchall()
        ]

        return {
            "ok": True,
            "messagesPerHour": hourly,
            "messagesPerDay": daily,
            "messagesPerMonth": monthly,
            "messagesPerAllTime": all_time,
            "range": selected_range,
            "rangeLabel": {"hour": "last 24 hours", "day": "last 30 days", "month": "last 12 months", "all": "all time"}[selected_range],
            "messageTotal": message_total,
            "runsByStatus": runs_by_status,
            "runTotal": sum(runs_by_status.values()),
            "spawnRequestsByStatus": spawns_by_status,
            "spawnRequestTotal": sum(spawns_by_status.values()),
            "liveAgents": live_agents,
            "onlineAgents": online_agents,
            "workingAgents": working_agents,
            "onlineEnvironments": online_environments,
            "successRate": success_rate,
            "runsCompleted": runs_completed_n,
            "runsFailed": runs_failed_n,
            "openReplyContracts": open_reply_contracts,
            "overdueReplyContracts": overdue_reply_contracts,
            "fleetMedianReplyMinutes": fleet_median_reply,
            "dispatchOutcomes": dispatch_outcomes,
            "agentLeaderboard": agent_leaderboard,
            "busiestChannels": busiest_channels,
            "failureReasons": failure_reasons,
        }
    finally:
        await db.close()


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


@router.get("/analytics/pulse")
async def get_analytics_pulse(request: Request, window_minutes: int = Query(60, ge=5, le=1440)):
    """Fleet 'pulse' for the Chat landing dashboard — a glanceable, window-scoped
    (5min .. 24h) view of comms performance + a board of online agents.

    Returns message rate, fleet working-utilization (working agent-minutes over
    available online agent-minutes in the window), open/overdue reply contracts,
    and per online agent: status, last-worked, currently-working, and in-window
    message + working-minute activity. All run-time math uses julianday() on the
    ISO TEXT run columns (never epoch arithmetic), NULL-guarded and clamped >= 0.
    """
    db = await get_db()
    try:
        from datetime import datetime as _dt
        settings = await _load_settings(db)
        now_s = int(time.time())
        win_s = now_s - window_minutes * 60
        win_ms = win_s * 1000
        now_iso = _iso_from_ms(now_s * 1000)
        win_iso = _iso_from_ms(win_ms)

        def _ep(value):
            try:
                return _dt.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
            except Exception:
                return None

        # Messages in window (direct + channel, same filter as GET /analytics).
        msg_where = "((source='direct' AND to_agent IS NOT NULL) OR (source='channel' AND to_agent IS NULL))"
        mc = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {msg_where} AND timestamp >= ?", (win_ms,))
        msg_count = int((await mc.fetchone())[0])
        per_hour = round(msg_count / (window_minutes / 60.0), 1) if window_minutes else 0.0

        # Per-agent direct-message counts in window (credit both parties).
        mpa = await db.execute(
            "SELECT agent, COUNT(*) c FROM ("
            " SELECT from_agent AS agent FROM messages WHERE source='direct' AND timestamp >= ?"
            " UNION ALL"
            " SELECT to_agent AS agent FROM messages WHERE source='direct' AND to_agent IS NOT NULL AND timestamp >= ?"
            ") GROUP BY agent",
            (win_ms, win_ms),
        )
        msgs_by_agent = {r["agent"]: int(r["c"] or 0) for r in await mpa.fetchall() if r["agent"]}

        # Working minutes per agent = clamped overlap of each run with the window.
        # Work-start proxy = COALESCE(started_at, claimed_at): production dispatch runs go
        # queued→claimed→completed and almost never populate started_at, so gating on
        # started_at IS NOT NULL left working_min empty for every agent → Utilization stuck
        # at 0 forever (operator-reported 2026-06-19). claimed_at is the moment the worker
        # took the run, which is the correct work-start signal. Same COALESCE convention as
        # the Runs queries (lines ~2167/2190).
        runs_c = await db.execute(
            "SELECT target_agent, started_at, claimed_at, finished_at FROM dispatch_runs "
            "WHERE COALESCE(started_at, claimed_at) IS NOT NULL "
            "AND julianday(COALESCE(finished_at, ?)) >= julianday(?)",
            (now_iso, win_iso),
        )
        working_min = {}
        for r in await runs_c.fetchall():
            a = r["target_agent"]
            if not a:
                continue
            s = _ep(r["started_at"] or r["claimed_at"])
            f = _ep(r["finished_at"]) if r["finished_at"] else now_s
            if s is None:
                continue
            if f <= s:
                continue  # negative/zero span (clock skew or late-backfilled claimed_at) → no work; parity with the per-agent MAX(0,...)
            # Skip reaped/stuck runs: their claimed→finished span is non-work and would
            # otherwise add the whole window as "working". Applies to OPEN runs too
            # (2026-07-02 screenshot incident): a `delivered` run whose worker died is a
            # legitimately-open reply contract (reminders recover it), but its ever-growing
            # span is not work. The ceiling must never be SMALLER than the window, or a
            # genuine long run (a 5h task viewed in the 12h/24h pulse) would be dropped as
            # if it were a reaped orphan (review 2026-07-03). overlap already clamps the
            # contribution to the in-window slice; this only discards spans that exceed both.
            if (f - s) > max(WORKED_SPAN_CEILING_SECONDS, window_minutes * 60):
                continue
            overlap = min(f, now_s) - max(s, win_s)
            if overlap > 0:
                working_min[a] = working_min.get(a, 0.0) + overlap / 60.0

        # Last-worked + currently-working across ALL runs (not just the window). Same
        # COALESCE(started_at, claimed_at) work-start proxy as the working-minutes query —
        # gating on started_at IS NOT NULL hid every production run (started_at unpopulated).
        lw_c = await db.execute(
            "SELECT target_agent, MAX(COALESCE(finished_at, ?)) AS lw "
            "FROM dispatch_runs WHERE COALESCE(started_at, claimed_at) IS NOT NULL GROUP BY target_agent",
            (now_iso,),
        )
        last_worked = {}
        for r in await lw_c.fetchall():
            if r["target_agent"]:
                last_worked[r["target_agent"]] = r["lw"]

        # Online-agent board (exclude offline/stopped).
        agents_c = await db.execute("SELECT * FROM agents")
        board = []
        online_count = 0
        working_now = 0
        fleet_working = 0.0
        for row in await agents_c.fetchall():
            if row["id"] == "dashboard":
                continue
            status = await _compute_agent_status(row, db)
            if status.startswith("offline") or status.startswith("stopped"):
                continue
            online_count += 1
            aid = row["id"]
            # Cap at wall-clock: OVERLAPPING runs (e.g. several orphaned `delivered`
            # contracts from a dead worker epoch) each accrue the full window and
            # summed to absurd "240m work in 1h" (2026-07-02 screenshot incident).
            # An agent cannot work more than the window's wall-clock.
            wm = round(min(working_min.get(aid, 0.0), float(window_minutes)), 1)
            fleet_working += wm
            if status.startswith("working"):
                working_now += 1
            board.append({
                "id": aid,
                "role": row["role"],
                "runtime": row["runtime"],
                "mode": row["session_mode"],
                "status": status,
                "lastWorkedAt": last_worked.get(aid),
                # SINGLE SOURCE OF TRUTH (2026-07-02): the per-row label previously used
                # open-runs (`active_now`) while the tile count + status dot use derive()
                # — orphaned `delivered` contracts made three rows say "working now" under
                # a "2 Working now" tile with online dots. derive() is the sole authority.
                "workingNow": status.startswith("working"),
                "messagesInWindow": msgs_by_agent.get(aid, 0),
                "workingMinutesInWindow": wm,
            })
        # Working agents first, then most-active, then alphabetical.
        board.sort(key=lambda a: (0 if a["workingNow"] else 1, -a["messagesInWindow"], a["id"]))
        utilization = (
            round((fleet_working / (online_count * window_minutes)) * 100, 1)
            if online_count and window_minutes else 0.0
        )

        # Open + overdue (>30min) reply contracts, fleet-wide, right now.
        owed_c = await db.execute(
            "SELECT requested_at FROM dispatch_runs WHERE require_reply=1 "
            "AND status IN ('queued','claimed','running','delivered') AND COALESCE(result_message_id,'')=''"
        )
        owed = await owed_c.fetchall()
        overdue = sum(1 for r in owed if (_ep(r["requested_at"]) or now_s) < now_s - 30 * 60)

        return {
            "ok": True,
            "windowMinutes": window_minutes,
            "messages": {"count": msg_count, "perHour": per_hour},
            "onlineAgents": online_count,
            "workingNow": working_now,
            "fleetWorkingMinutes": round(fleet_working, 1),
            "fleetUtilizationPct": utilization,
            "openReplyContracts": len(owed),
            "overdueReplyContracts": overdue,
            "agents": board,
        }
    finally:
        await db.close()
