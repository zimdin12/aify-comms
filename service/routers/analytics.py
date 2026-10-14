"""The `analytics` route domain: fleet analytics, per-agent analytics, and the pulse.

v0.5.2e. The largest domain moved so far — 687 lines in three handlers — and moved as ONE tag on the
reviewer's ruling, reviewed internally per handler rather than split into three commits.

Read-mostly: these three aggregate over dispatch runs, sessions and messages and return a response.
That is why analytics sits in the small/low-blast-radius group despite its size, and it is why
`get_analytics` (314 lines) is the chosen first target for the METHOD-SPLIT work later. That split is
a SEPARATE tag and needs characterization tests around the endpoint first; the static extraction gate
is necessary and not sufficient for it.

`WORKED_SPAN_CEILING_SECONDS` had no user outside these handlers and moved with them.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include and
FastAPI combines them.

THE SUMMARY'S BEHAVIOUR IS NOT HERE (audit item S2). `GET /analytics` composes
`service/api_core/analytics_summary.py`; the handler validates the range and owns the connection.
"""

from __future__ import annotations

import time

from fastapi import Query, Request

from service.api_core.analytics_summary import fleet_analytics
from service.api_core.routing import domain_router
from service.api_core.tuning import WORKED_SPAN_CEILING_SECONDS
from service.api_core.serialization import _iso_from_ms
from service.api_core.reply_contract import reply_reminder_minutes
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch
from service.db import get_db

router = domain_router()

# THE ANALYTICS DOMAIN IS TWO FILES, COMPOSED HERE rather than in `api_v2.py`. The per-agent handler
# left in v0.5.4; this module keeps the fleet-wide ones and includes it, so `api_v2.py` still sees ONE
# analytics router.
from service.routers.agent_analytics import router as _agent_analytics_router

router.include_router(_agent_analytics_router)


from service.api_core.analytics_series import _build_online_agent_board  # noqa: E402


@router.get("/analytics")
async def get_analytics(request: Request, analytics_range: str = Query("hour", alias="range", pattern="^(hour|day|month|all)$")):
    db = await get_db()
    try:
        return await fleet_analytics(db, analytics_range)
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
        settings = await _load_settings(db)
        now_s = int(time.time())
        win_s = now_s - window_minutes * 60
        win_ms = win_s * 1000
        now_iso = _iso_from_ms(now_s * 1000)
        win_iso = _iso_from_ms(win_ms)

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
            s = _iso_to_epoch(r["started_at"] or r["claimed_at"], default=None)
            f = _iso_to_epoch(r["finished_at"], default=None) if r["finished_at"] else now_s
            # A finish time SQLite reads and Python does not (e.g. 'now') is unknown, not a 500 for the
            # whole board (v0.7.1 review, S11).
            if s is None or f is None:
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

        # Last-worked across ALL runs (not just the window), from RECORDED finishes only. Same
        # COALESCE(started_at, claimed_at) work-start proxy as the working-minutes query —
        # gating on started_at IS NOT NULL hid every production run (started_at unpopulated).
        # An unfinished run used to count as finishing NOW, so any stale claimed or delivered row made
        # an idle agent "worked 0m ago" (2026-10-14 audit, L6). Working now is the derived status
        # (`workingNow` on the board), and an agent with no recorded finish has no last-worked time.
        lw_c = await db.execute(
            "SELECT target_agent, MAX(finished_at) AS lw FROM dispatch_runs "
            "WHERE COALESCE(started_at, claimed_at) IS NOT NULL AND COALESCE(finished_at, '') != '' "
            "GROUP BY target_agent",
        )
        last_worked = {}
        for r in await lw_c.fetchall():
            if r["target_agent"]:
                last_worked[r["target_agent"]] = r["lw"]

        board, online_count, working_now, fleet_working = await _build_online_agent_board(
            db, working_min, last_worked,
            msgs_by_agent, window_minutes,
        )
        utilization = (
            round((fleet_working / (online_count * window_minutes)) * 100, 1)
            if online_count and window_minutes else 0.0
        )

        # Open + overdue reply contracts, fleet-wide, right now. The window is the operator's
        # `reply_reminder_minutes`, the same one the reminder sweep and the Work Loop use.
        owed_c = await db.execute(
            "SELECT requested_at, status FROM dispatch_runs WHERE require_reply=1 "
            "AND status IN ('queued','claimed','running','delivered') AND COALESCE(result_message_id,'')=''"
        )
        owed = await owed_c.fetchall()
        overdue_cut = now_s - reply_reminder_minutes(settings) * 60
        overdue = sum(1 for r in owed if r["status"] != "queued" and (_iso_to_epoch(r["requested_at"]) or now_s) < overdue_cut)

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
            "overdueAfterMinutes": reply_reminder_minutes(settings),
            "agents": board,
        }
    finally:
        await db.close()
