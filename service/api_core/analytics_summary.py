"""The fleet analytics summary behind `GET /analytics`: message series, window totals, agent and
environment counts, reply contracts, and the operational rollups.

MOVED FROM `service/routers/analytics.py` (audit item S2): the handler held 220 lines of aggregation.
The eight series it shares with the pulse were already in `service/api_core/analytics_series.py`; this
module is the summary that composes them. Every statement runs in the order it ran in the handler, on
the connection the caller passes, and `service/tests/test_heartbeat_and_analytics_sql_freeze.py` pins
that order, recorded before the move. The SQL literals keep their original whitespace, which is why
some sit deeper than the code around them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from service.api_core.analytics_series import (
    _agent_leaderboard,
    _append_daily_message_buckets,
    _busiest_channels,
    _dispatch_outcomes_series,
    _failure_reasons,
    _fleet_median_reply_minutes,
    _hourly_message_series,
    _monthly_message_series,
)
from service.api_core.liveness import _agent_wake_mode
from service.api_core.managed_env import load_session_environment_by_agent
from service.api_core.reply_contract import reply_reminder_minutes
from service.api_core.serialization import _iso_from_ms
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.status_signal_prefetch import PrefetchedStatusSignals
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import _ENVIRONMENT_HEARTBEAT_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status
from service.status_engine import is_live_agent_status
from service.api_core import view_owners

#: The messages a count admits: a direct message to someone, or a channel post.
MESSAGE_WHERE = """
          (
            (source = 'direct' AND to_agent IS NOT NULL)
            OR (source = 'channel' AND to_agent IS NULL)
          )
        """

RANGE_LABELS = {"hour": "last 24 hours", "day": "last 30 days", "month": "last 12 months", "all": "all time"}


@dataclass(frozen=True)
class MessageSeries:
    hourly: list
    daily: list
    monthly: list
    all_time: list


@dataclass(frozen=True)
class AnalyticsWindow:
    """The page's window as SQL: empty clauses for `all`, otherwise one start time for every count."""

    since_s: Optional[int] = None
    run_where: str = ""
    run_params: tuple[Any, ...] = ()
    spawn_where: str = ""
    spawn_params: tuple[Any, ...] = ()


async def fleet_analytics(db, selected_range: str) -> dict:
    settings = await _load_settings(db)
    now_s = int(time.time())
    series = await message_series(db, now_s)
    window = analytics_window(series, selected_range)
    runs_by_status, spawns_by_status = await _window_totals(db, window)
    # THE TILE IS THE SUM OF THE SERIES IT IS SHOWN WITH, read in the same pass, so a message that
    # commits between two reads cannot land in one and not the other (review of 0.9.5, L7).
    shown = {"hour": series.hourly, "day": series.daily, "month": series.monthly}.get(selected_range, series.all_time)
    message_total = sum(int(bucket["count"]) for bucket in shown)
    live_agents, online_agents, working_agents = await _agent_tallies(db)
    online_environments = await _online_environments(db, settings)

    # ── Fleet operational analytics (2026-06-17 round: "real analytics") ──
    # Everything below is additive; all run-time math uses julianday() on the ISO TEXT
    # run columns (never epoch arithmetic on those) and is windowed by `run_where`.
    # Dispatch success rate over the window (completed vs. completed+failed+cancelled).
    runs_completed_n = int(runs_by_status.get("completed", 0))
    runs_failed_n = int(runs_by_status.get("failed", 0)) + int(runs_by_status.get("cancelled", 0))
    runs_finished_n = runs_completed_n + runs_failed_n
    success_rate = round((runs_completed_n / runs_finished_n) * 100, 1) if runs_finished_n else None

    open_reply_contracts, overdue_reply_contracts = await _reply_contracts(db, settings, now_s)

    fleet_median_reply = await _fleet_median_reply_minutes(db, window.run_where, window.run_params)

    dispatch_outcomes = await _dispatch_outcomes_series(db)

    agent_leaderboard = await _agent_leaderboard(db, window.run_where, window.run_params)

    busiest_channels = await _busiest_channels(db, window.since_s)

    failure_reasons = await _failure_reasons(db, window.run_where, window.run_params)

    return {
        "ok": True,
        "messagesPerHour": series.hourly,
        "messagesPerDay": series.daily,
        "messagesPerMonth": series.monthly,
        "messagesPerAllTime": series.all_time,
        "range": selected_range,
        "rangeLabel": RANGE_LABELS[selected_range],
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
        "overdueAfterMinutes": reply_reminder_minutes(settings),
        "fleetMedianReplyMinutes": fleet_median_reply,
        "dispatchOutcomes": dispatch_outcomes,
        "agentLeaderboard": agent_leaderboard,
        "busiestChannels": busiest_channels,
        "failureReasons": failure_reasons,
    }


async def message_series(db, now_s: int) -> MessageSeries:
    """The hourly, daily, monthly and all-time message charts, in that order."""

    async def count_messages_between(start_ms: int, end_ms: int) -> int:
        cursor = await db.execute(
            f"SELECT COUNT(*) FROM messages WHERE {MESSAGE_WHERE} AND timestamp >= ? AND timestamp < ?",
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
            WHERE {MESSAGE_WHERE}
            GROUP BY bucket
            ORDER BY bucket ASC
            """
    )
    all_time = [
        {"label": row["bucket"] or "unknown", "start": _iso_from_ms(int(row["start_ms"] or 0)), "count": int(row["cnt"] or 0)}
        for row in await all_time_c.fetchall()
    ]
    return MessageSeries(hourly=hourly, daily=daily, monthly=monthly, all_time=all_time)


def analytics_window(series: MessageSeries, selected_range: str) -> AnalyticsWindow:
    # ONE WINDOW FOR THE PAGE, THE CHART'S OWN. The counts were a rolling now-minus-24h (30 d, 366 d)
    # while the chart's buckets are hour-, day- and month-aligned, so the message tile read 1629 above a
    # chart that summed to 1617 (2026-10-14 audit, L7). The window starts where the first bucket does.
    since_s_by_range = {
        key: int(_iso_to_epoch(points[0]["start"]))
        for key, points in (("hour", series.hourly), ("day", series.daily), ("month", series.monthly)) if points
    }
    since_s = since_s_by_range.get(selected_range)
    if since_s is None:
        return AnalyticsWindow()
    since_iso = _iso_from_ms(since_s * 1000)
    return AnalyticsWindow(
        since_s=since_s,
        run_where="WHERE COALESCE(finished_at, requested_at) >= ?",
        run_params=(since_iso,),
        spawn_where="WHERE updated_at >= ?",
        spawn_params=(since_iso,),
    )


async def _window_totals(db, window: AnalyticsWindow) -> tuple[dict, dict]:
    status_c = await db.execute(
        f"SELECT status, COUNT(*) as cnt FROM dispatch_runs {window.run_where} GROUP BY status",
        window.run_params,
    )
    runs_by_status = {row["status"]: row["cnt"] for row in await status_c.fetchall()}
    spawn_status_c = await db.execute(
        f"SELECT status, COUNT(*) as cnt FROM spawn_requests {window.spawn_where} GROUP BY status",
        window.spawn_params,
    )
    spawns_by_status = {row["status"]: row["cnt"] for row in await spawn_status_c.fetchall()}
    return runs_by_status, spawns_by_status


async def _agent_tallies(db) -> tuple[int, int, int]:
    """Live (wakeable), online and working agents, each status derived the way every reader derives it."""
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
        live = is_live_agent_status(status)
        view_owners.observe_live_count(row, live, 'summary')  # the status shadow's (S2c slice 5); never raises
        if live:
            online_agents += 1
        if status.startswith("working"):
            working_agents += 1
    return live_agents, online_agents, working_agents


async def _online_environments(db, settings: dict) -> int:
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
    return sum(
        1
        for env_row in await env_rows_c.fetchall()
        if _environment_effective_status(env_row, offline_seconds=env_offline_seconds)
        in _ENVIRONMENT_HEARTBEAT_STATUSES
    )


async def _reply_contracts(db, settings: dict, now_s: int) -> tuple[int, int]:
    # Open + overdue reply contracts fleet-wide (NOT windowed — what's outstanding right now).
    # Overdue = an unanswered required reply its target received, requested longer ago than the reminder window
    # (a queued run was never delivered; `_contract_state` says the same).
    contracts_c = await db.execute(
            """
            SELECT requested_at, status FROM dispatch_runs
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
        if r["status"] != "queued" and (_iso_to_epoch(r["requested_at"]) or now_s) < overdue_cut
    )
    return open_reply_contracts, overdue_reply_contracts
