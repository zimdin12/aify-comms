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
