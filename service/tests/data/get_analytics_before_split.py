"""FIXTURE: `get_analytics` exactly as it was BEFORE the v0.5.3 method split.

Not executable app code and never imported by the service — it is the comparison subject for
`test_analytics_split_is_inert.py`, which inlines the extracted helper back and requires the
result to reproduce this. Do not "fix" or reformat it: any edit here silently changes what the
proof is proving.
"""

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

        hourly = []
        hour_start = (now_s // 3600) * 3600
        for i in range(23, -1, -1):
            start_s = hour_start - i * 3600
            hourly.append({
                "label": time.strftime("%H:00", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 3600) * 1000),
            })

        daily = []
        today_struct = time.localtime(now_s)
        today_start_s = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d", today_struct), "%Y-%m-%d")))
        for i in range(29, -1, -1):
            start_s = today_start_s - i * 86400
            daily.append({
                "label": time.strftime("%m-%d", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 86400) * 1000),
            })

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
