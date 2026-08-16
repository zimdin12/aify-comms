"""The pre-split `control_agent`, frozen.

Not imported by anything. It is the ONE true original that
`test_control_agent_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/session_ops.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def control_agent(agent_id: str, req: AgentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"interrupt", "stop", "resume", "start"}:
        raise HTTPException(400, f'Unsupported agent control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        now = _now()

        # START (2026-07-14). A managed agent with NO session row could not be started from the
        # dashboard at all: the Console tab returns early on "no session" (above the start
        # buttons, which all need a session id), so the only way to bring one up was to send it a
        # message and hope. Operator: "why can't I start hermes models?" — the cold-start itself
        # was never broken; there was simply no button.
        #
        # This is the SAME mechanism the send path uses (_coldstart_spawn_request_for_dispatch):
        # create a spawn request, a bridge claims it, registers a session and brings the worker
        # up — resuming the agent's saved session handle when it has one, which for the hermes
        # coders means their existing conversation (lc-coder alone is 12,780 messages).
        if action == "start":
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" is resident — its terminal is the CLI you launched, '
                    "not a dashboard-owned worker. Switch it to managed to start one from here.",
                )
            # ALLOWLIST, never a blocklist (fixed 2026-07-26). This gate used to be
            # `status NOT IN ('stopped','failed','ended','cancelled')`, which silently treats
            # every status NOT on that list as LIVE. `lost` is not on it — so an agent whose
            # worker was lost months ago read as "already running" forever: Start returned
            # alreadyRunning, no spawn request was ever created, the agent stayed `available`,
            # and clicking again just repeated the toast. Live-reproduced on the whole ef- team
            # (ef-manager / ef-coder-lead / ef-tech-lead / ef-tester — four sessions stuck
            # `lost` with ended_at 2026-04-30), which were permanently unstartable from the
            # dashboard. Note the asymmetry that made it invisible: derive() correctly reported
            # `available` off real liveness, so status and this gate disagreed.
            #
            # Use the canonical live sets instead, so a new session status can never silently
            # mean "live" here again. The union of both is deliberate: LIVE_SESSION_STATUSES is
            # the session-row set the reconcilers use, _borrowed_live_session_statuses() the narrower
            # status-engine set that also covers restarting/cli-takeover. A row must ALSO not be
            # marked ended — a live status with ended_at set is a stale row the reconcilers heal,
            # and trusting it would re-create exactly this permanent block.
            _start_live_statuses = sorted(
                {s.lower() for s in LIVE_SESSION_STATUSES}
                | {s.lower() for s in _borrowed_live_session_statuses()}
            )
            _live_ph = ",".join("?" for _ in _start_live_statuses)
            live = await (await db.execute(
                f"""
                SELECT id FROM agent_sessions
                WHERE agent_id = ?
                  AND LOWER(COALESCE(status,'')) IN ({_live_ph})
                  AND COALESCE(ended_at,'') = ''
                LIMIT 1
                """,
                (agent_id, *_start_live_statuses),
            )).fetchone()
            if live:
                # Already running — starting again would spawn a duplicate worker.
                return {"ok": True, "agentId": agent_id, "action": "start", "alreadyRunning": True}
            settings = await _load_settings(db)
            start_runtime = _normalize_runtime(agent["runtime"] or "")
            # N8 applied to the DASHBOARD START BUTTON. `_coldstart_spawn_request_for_dispatch`
            # refuses for FIVE distinct causes and records which one in `warnings`; this call site
            # passed no list, so the reason was discarded and every cause rendered the same
            # sentence — "no environment bridge is available to run it. Start one on its host with
            # `aify-comms`." That sentence NAMES a cause. Measured, three of the five causes reach
            # this branch, and for two of them the claim is false: a non-cold-startable runtime and
            # a corrupt environment row both reported a missing bridge. (The resident refusal is
            # guarded EARLIER with its own accurate message, and an in-flight spawn returns 200
            # below, so neither was ever part of this defect.)
            #
            # The advice made it worse than a vague message would have been: a bare `aify-comms` on
            # a host that already runs one SUPERSEDES the live bridge and reaps its managed workers
            # (2026-08-11, nine agents). So a wrong diagnosis here steers the operator into an
            # outage. Read the recorded reason instead of asserting one.
            coldstart_warnings: list[str] = []
            started = await _coldstart_spawn_request_for_dispatch(
                db,
                agent_id,
                runtime=start_runtime,
                settings=settings,
                requested_by=req.from_agent or "dashboard",
                warnings=coldstart_warnings,
            )
            await db.commit()
            if not started:
                # _coldstart returns False for an already-pending/booting spawn too (idempotent
                # success, not a failure). Clicking Start twice during a slow boot — before the
                # session row exists — must not surface a false "no environment bridge" error.
                if await _has_pending_or_booting_spawn_request(db, agent_id):
                    return {"ok": True, "agentId": agent_id, "action": "start", "spawnPending": True}
                raise HTTPException(
                    409,
                    _coldstart_refusal_message(coldstart_warnings, start_runtime),
                )
            await _invalidate_agent_live_state(db, agent_id)
            return {"ok": True, "agentId": agent_id, "action": "start", "spawnRequested": True}
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if action in {"interrupt", "stop"}:
            if active_run:
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=req.from_agent or "dashboard",
                    action="interrupt",
                    body=req.body or f"Agent {action} requested from dashboard.",
                )
            elif action == "interrupt":
                raise HTTPException(409, f'Agent "{agent_id}" has no active run to interrupt')

        cancelled_queued = 0
        if action == "stop":
            queued_cursor = await db.execute(
                "SELECT id FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                (agent_id,),
            )
            queued_rows = await queued_cursor.fetchall()
            for row in queued_rows:
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                    (f'Agent "{agent_id}" was stopped from the dashboard before the run could start.', now, row["id"]),
                )
                await _append_dispatch_event(db, row["id"], "agent_stopped", "Agent stopped from dashboard")
                cancelled_queued += 1
            stop_note = "Stopped from dashboard. Resume to allow wake/dispatch again."
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                stop_note = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ?
                WHERE id = ?
                """,
                (stop_note, now, agent_id),
            )
            # Kill the managed console/TUI too — aify-comms is the lifecycle driver
            # for managed sessions, so Stop must tear down the running terminal
            # instead of leaving an abandoned TUI (operator-reported 2026-05-31).
            # Resident windows are the operator's OWN process; the bridge teardown
            # handles those (see stop_note), so this is managed-only.
            if _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
                await _request_stop_agent_terminals(
                    db, agent_id, requested_by=req.from_agent or "dashboard", now=now,
                )
        elif action == "resume":
            await db.execute(
                """
                UPDATE agents
                SET status = 'idle', status_note = '', launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_control_requested",
                {"agentId": agent_id, "action": action, "controlId": control_id, "cancelledQueued": cancelled_queued},
            )
        await _broadcast_agent_status(ws, db, agent_id)
        return {
            "ok": True,
            "agentId": agent_id,
            "action": action,
            "controlId": control_id,
            "cancelledQueued": cancelled_queued,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()
