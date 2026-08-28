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

from service.api_core.liveness import _agent_wake_mode
from service.api_core.routing import domain_router
from service.api_core.tuning import WORKED_SPAN_CEILING_SECONDS
from service.api_core.serialization import _iso_from_ms
from service.api_core.reply_contract import reply_reminder_minutes
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch
from service.db import get_db
from service.env_status import _ENVIRONMENT_HEARTBEAT_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status

logger = logging.getLogger("aify_comms.routers.analytics")

router = domain_router()

# THE ANALYTICS DOMAIN IS TWO FILES, COMPOSED HERE rather than in `api_v2.py`. The per-agent handler
# left in v0.5.4; this module keeps the fleet-wide ones and includes it, so `api_v2.py` still sees ONE
# analytics router.
from service.routers.agent_analytics import router as _agent_analytics_router

router.include_router(_agent_analytics_router)



# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/api_core/status_refresh.py in v0.5.4, so
# a plain import works.
from service.api_core.status_refresh import _compute_agent_status  # noqa: E402
from service.api_core.managed_env import load_session_environment_by_agent  # noqa: E402
from service.api_core.status_signal_prefetch import PrefetchedStatusSignals  # noqa: E402
from service.status_engine import is_live_agent_status  # noqa: E402
from service.api_core.analytics_series import (
    _agent_leaderboard,
    _build_online_agent_board,
    _append_daily_message_buckets,
    _busiest_channels,
    _dispatch_outcomes_series,
    _failure_reasons,
    _fleet_median_reply_minutes,
    _hourly_message_series,
    _monthly_message_series,
)


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
        # Built ONCE for the whole loop, not once per agent. Every status below resolves the same
        # two questions, and both answers are constant across a single request: the owning
        # environment depends on machine_id alone, and the session environment is one table read for
        # the whole fleet. Measured 2026-08-26 by counting aiosqlite execute() calls through one
        # GET /api/v1/analytics on a COLD live-state cache: 463 round-trips at 24 agents, of which
        # `SELECT * FROM environments WHERE machine_id = ?` and `SELECT environment_id FROM
        # agent_sessions ...` were 48 each and `SELECT * FROM agents WHERE id = ?` 24 -- five per
        # agent, re-reading answers this request already had. `GET /api/v1/agents` was given the same
        # request-scoped dicts in fab4204c and the reconcile sweep a sweep-scoped pair; this is the
        # third caller of the same derivation and the last one still asking per agent.
        #
        # `agent_row=row` is safe HERE specifically: these rows come from the `SELECT * FROM agents`
        # four lines above, which is the same query the refresh would issue for itself.
        environments_by_machine: dict = {}
        session_environment_by_agent = await load_session_environment_by_agent(db)
        # ONE PREFETCH for the whole loop. Three of the four batch parameters were already
        # threaded here; `status_signals` was not, so this endpoint paid `agent_status_state`
        # and `agent_console_signal` per agent -- the same two the pulse board stopped
        # re-reading. Measured: 7.0 round-trips per agent before.
        status_signals = None
        if len(agent_rows) > 1:
            status_signals = await PrefetchedStatusSignals.load(db, [r["id"] for r in agent_rows])
        for row in agent_rows:
            mode = _agent_wake_mode(row)
            if mode != "message-only" and mode != "disabled":
                live_agents += 1
            status = await _compute_agent_status(
                row,
                db,
                environments_by_machine=environments_by_machine,
                session_environment_by_agent=session_environment_by_agent,
                agent_row=row,
                status_signals=status_signals,
            )
            # THROUGH THE DECLARED PARTITION. This read `not offline and not stale`, which
            # counts a STOPPED agent as online -- measured live, /analytics reported 30 while
            # /analytics/pulse reported 27 on a fleet with exactly 3 stopped agents, and
            # `online_agents` is the utilization denominator below. It also excluded `stale`,
            # a status this engine stopped producing, so that half guarded nothing.
            if is_live_agent_status(status):
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
        # The OPERATOR'S window, not a literal: the reminder sweep and the Work Loop filter both
        # read this setting, and a tile labelled "overdue" that uses a different number is a
        # second answer to one question.
        overdue_cut = now_s - reply_reminder_minutes(settings) * 60
        overdue_reply_contracts = sum(
            1 for r in contract_rows
            if (_iso_to_epoch(r["requested_at"]) or now_s) < overdue_cut
        )

        fleet_median_reply = await _fleet_median_reply_minutes(db, run_where, run_params)

        dispatch_outcomes = await _dispatch_outcomes_series(db)

        agent_leaderboard = await _agent_leaderboard(db, run_where, run_params)

        busiest_channels = await _busiest_channels(db, since_s)

        failure_reasons = await _failure_reasons(db, run_where, run_params)

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
            "SELECT requested_at FROM dispatch_runs WHERE require_reply=1 "
            "AND status IN ('queued','claimed','running','delivered') AND COALESCE(result_message_id,'')=''"
        )
        owed = await owed_c.fetchall()
        overdue_cut = now_s - reply_reminder_minutes(settings) * 60
        overdue = sum(1 for r in owed if (_ep(r["requested_at"]) or now_s) < overdue_cut)

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
