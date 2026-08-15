"""The eight series and rollups the Analytics page is built from.

Moved out of `service/routers/analytics.py` in v0.5.4, byte-identical. A router should hold routes,
and these are its entire non-route population — 203 lines of aggregation with no route decorator
between them.

They are a natural leaf: none of them calls another, none touches the router, and the only thing they
need beyond stdlib is `_iso_from_ms`. That is also why they were worth moving as ONE block rather
than one at a time — they are contiguous in the original and separated only by blank lines, so the
move is a single span with nothing interleaved to preserve.
"""
from __future__ import annotations

import time
from typing import Any

from service.api_core.serialization import _iso_from_ms
from service.api_core.status_refresh import _compute_agent_status

async def _fleet_median_reply_minutes(db, run_where, run_params):
    """Median reply latency in minutes across completed required-reply runs, or None.

    None is not zero, and the characterization net pins that: no completed replies means "nothing
    measured", which the dashboard renders differently from a real zero.
    """
    # Fleet median reply latency (completed required-reply runs in window), minutes.
    _extra = "status = 'completed' AND require_reply = 1 AND requested_at IS NOT NULL AND finished_at IS NOT NULL"
    reply_where = (f"{run_where} AND {_extra}") if run_where else f"WHERE {_extra}"
    reply_c = await db.execute(
        f"SELECT (julianday(finished_at) - julianday(requested_at)) * 1440 AS mins FROM dispatch_runs {reply_where}",
        run_params,
    )
    reply_mins = sorted(float(r["mins"]) for r in await reply_c.fetchall() if r["mins"] is not None and float(r["mins"]) >= 0)
    fleet_median_reply = round(reply_mins[len(reply_mins) // 2], 1) if reply_mins else None
    return fleet_median_reply


async def _dispatch_outcomes_series(db):
    """Completed-vs-failed counts for the last 14 days: ALWAYS 14 entries, zero-filled.

    Dense, not sparse — the second query generates the day spine so a quiet day is a zero row
    rather than a gap. Assuming otherwise is how a chart loses its x-axis.
    """
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
    return dispatch_outcomes


async def _agent_leaderboard(db, run_where, run_params):
    """Top dispatch targets in the window, with a per-agent success rate.

    The loop `continue`s past rows with no agent. Safe to move because the loop moves with it — but
    the gate refused this block until it learned to tell a loop-bound `continue` from one whose
    loop stays behind (hole 12).
    """
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
    return agent_leaderboard


async def _busiest_channels(db, since_s):
    """Channel-message volume in the window, top 8.
    """
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
    return busiest_channels


async def _failure_reasons(db, run_where, run_params):
    """Failed and cancelled runs grouped by truncated error text, top 8.
    """
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
    return failure_reasons


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


async def _build_online_agent_board(
    db, working_min, last_worked,
    msgs_by_agent, window_minutes,
):
        """The online-agent board and the three fleet counters computed alongside it.

        Extracted from `get_analytics_pulse` (`service/routers/analytics.py`) in v0.5.4. A router
        should hold routes; this is aggregation, and it was the last large piece of that handler not
        already living here.

        IT RETURNS FOUR THINGS BECAUSE THEY ARE ONE PASS. The board rows, how many agents are online,
        how many are working, and the fleet's total working minutes all fall out of a single loop over
        agents. Computing them separately would derive each agent's status three times, and
        `_compute_agent_status` is the expensive call here.

        TWO INCIDENTS ARE PINNED IN THIS BODY, both about a number that lied. Working minutes are
        capped at the window's wall clock, because overlapping orphaned `delivered` contracts each
        accrued the full window and summed to 240 minutes of work in one hour. And the per-row
        workingNow label reads derive() like the tile count does, after three rows claimed to be
        working under a tile that said two.
        """
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
        return board, online_count, working_now, fleet_working
