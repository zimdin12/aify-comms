"""The pre-split `control_session`, frozen.

Not imported by anything. It is the ONE true original that
`test_control_session_split_is_inert.py` inlines every extraction back against — see the analytics
precedent for why one fixture beats a chain of per-slice copies: verifying extraction N against "the
state just before extraction N" needs a second copy of the function per split, each rotting
independently while staying green.

Captured from `git show HEAD:service/routers/sessions.py` at the commit before the first extraction.
"""


async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    # Lifecycle cleanup (2026-06-03): `recover` + `resume` were byte-identical
    # aliases of `restart` with NO dashboard caller — dropped. (Resident
    # wake-resume lives on POST /agents/{id}/control, a different endpoint.)
    if action not in {"stop", "restart", "recreate", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=req.from_agent or "dashboard",
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        spawn_request_row = None
        spawn_spec_row = None
        cancelled_spawns = 0
        coldstart_warnings: list[str] = []
        if action in {"restart", "recreate"}:
            pending_cursor = await db.execute(
                """
                SELECT *
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
            pending_spawn = await pending_cursor.fetchone()
            if pending_spawn:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" already has pending spawn request "{pending_spawn["id"]}" ({pending_spawn["status"]}).',
                )

        if action in {"restart", "recreate"}:
            spec_id = str(session["spawn_spec_id"] or "").strip()
            if not spec_id:
                # FIX 5 (2026-06-03): a resident-origin session has a NULL spawn_spec,
                # yet the SEND path already auto-starts it via the cold-start helper.
                # Mirror that here instead of hard-erroring: cold-start a managed worker
                # (creates a queued spawn_request a bridge can claim), then continue to
                # the status-update tail with that queued/claimed spawn_request row. Only
                # raise when nothing can host it (no cold-start AND no claimable request).
                settings = await _load_settings(db)
                coldstarted = await _coldstart_spawn_request_for_dispatch(
                    db,
                    agent_id,
                    runtime=str(session["runtime"] or ""),
                    settings=settings,
                    requested_by=req.from_agent or "dashboard",
                    warnings=coldstart_warnings,
                )
                if not coldstarted and not await _has_claimable_spawn_request(db, agent_id):
                    raise HTTPException(
                        409,
                        (
                            f'Session "{session_id}" has no stored spawn spec and no online '
                            f'environment can host managed {session["runtime"] or "runtime"}.'
                        ),
                    )
                spawn_request_row = await (await db.execute(
                    """
                    SELECT *
                    FROM spawn_requests
                    WHERE agent_id = ?
                      AND status IN ('queued', 'claimed')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (agent_id,),
                )).fetchone()
                # Fall through to the shared status-update tail below.
                spawn_spec_row = None
            else:
                spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))
                spawn_spec_row = await spec_cursor.fetchone()
                if not spawn_spec_row:
                    raise HTTPException(409, f'Session "{session_id}" references missing spawn spec "{spec_id}"')
                env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (spawn_spec_row["environment_id"],))
                env_row = await env_cursor.fetchone()
                if not env_row:
                    raise HTTPException(409, f'Environment "{spawn_spec_row["environment_id"]}" is not available')

                agent_cursor = await db.execute("SELECT role, name FROM agents WHERE id = ?", (agent_id,))
                agent_row = await agent_cursor.fetchone()
                environment = _environment_record_to_dict(env_row)
                if str(environment.get("status") or "").lower() != "online":
                    raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}; assign a live environment before {action}.')
                workspace = _normalize_workspace_for_environment(environment, spawn_spec_row["workspace"] or session["workspace"] or "")
                workspace_root = _workspace_root_for(environment, workspace)
                request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                resume_policy = "fresh_context" if action == "recreate" else "native_first"
                request_session_handle = "" if action == "recreate" else (session["session_handle"] or "")
                await db.execute(
                    """
                    INSERT INTO spawn_requests (
                        id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                        workspace, workspace_root, initial_message, priority, subject, mode,
                        resume_policy, status, session_handle, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request_id,
                        spec_id,
                        req.from_agent or "dashboard",
                        spawn_spec_row["environment_id"],
                        agent_id,
                        (agent_row["role"] if agent_row else "") or "coder",
                        (agent_row["name"] if agent_row else "") or agent_id,
                        spawn_spec_row["runtime"],
                        workspace,
                        workspace_root,
                        req.body or "",
                        req.priority or "normal",
                        req.subject or f"{action.title()} {agent_id}",
                        spawn_spec_row["mode"] or session["mode"] or "managed-warm",
                        resume_policy,
                        "queued",
                        request_session_handle,
                        now,
                        now,
                    ),
                )
                spawn_request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
                if action == "recreate":
                    await db.execute(
                        """
                        UPDATE agents
                        SET session_handle = '',
                            runtime_state = '{}',
                            last_seen = ?
                        WHERE id = ?
                        """,
                        (now, agent_id),
                    )

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recreate": "ended",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering','ended') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        if action in {"stop", "cli_takeover"}:
            pending_spawn_cursor = await db.execute(
                """
                SELECT id
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                """,
                (agent_id,),
            )
            for pending_spawn in await pending_spawn_cursor.fetchall():
                await db.execute(
                    """
                    UPDATE spawn_requests
                    SET status = 'cancelled',
                        error = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('queued', 'claimed', 'starting')
                    """,
                    (
                        f'Session "{session_id}" was {"paused for CLI takeover" if action == "cli_takeover" else "stopped from the dashboard"} before spawn completed.',
                        now,
                        now,
                        pending_spawn["id"],
                    ),
                )
                cancelled_spawns += 1
            if action == "cli_takeover":
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        "Paused for direct CLI takeover. Close the CLI session and use Sessions -> Restart to return control to the dashboard.",
                        now,
                        agent_id,
                    ),
                )
            else:
                agent_current = await (await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
                if agent_current and _normalize_session_mode(agent_current["session_mode"] or "resident") == "resident":
                    await db.execute(
                        """
                        UPDATE agents
                        SET status = 'stopped',
                            status_note = ?,
                            launch_mode = 'none',
                            last_seen = ?
                        WHERE id = ?
                        """,
                        ("Resident session stop requested from dashboard; live bridge should terminate the CLI host.", now, agent_id),
                    )
                else:
                    await db.execute(
                        "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END, last_seen = ? WHERE id = ?",
                        (now, agent_id),
                    )
        else:
            await db.execute(
                "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'idle' END, last_seen = ? WHERE id = ?",
                (now, agent_id),
            )

        # Halt the running backing (2026-06-07): Stop/Restart/Reset/CLI-takeover must PROMPTLY
        # kill the live managed PTY, not just flip DB status. Previously only the agent-control
        # stop enqueued a terminal stop, so the UI's session-control Stop left the worker running
        # as a headless orphan until a reaper / the next Restart's reap-prior. Enqueue a terminal
        # 'stop' for the session's live terminal(s). For restart/recreate the new spawn_request
        # was already queued above (and an env-offline target 409'd before reaching here), so we
        # never kill the old backing without a replacement queued. Resume is unaffected — it
        # carries via the durable session_handle, not the live PTY.
        live_terminals = await (await db.execute(
            "SELECT id, environment_id, bridge_id, status FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )).fetchall()
        for term_row in live_terminals:
            if str(term_row["status"] or "").strip().lower() in _TERMINAL_ACTIVE_STATUSES:
                await _append_terminal_control(
                    db,
                    terminal_id=term_row["id"],
                    environment_id=term_row["environment_id"] or "",
                    bridge_id=term_row["bridge_id"] or "",
                    action="stop",
                    requested_by=req.from_agent or "dashboard",
                    body=f"Session {action} from dashboard.",
                )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
            if spawn_request_row:
                await ws.broadcast(
                    "spawn_request_created",
                    {"spawnRequestId": spawn_request_row["id"], "environmentId": spawn_request_row["environment_id"]},
                )
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
            "warnings": coldstart_warnings,
            "spawnRequest": _spawn_request_to_dict(spawn_request_row, _spawn_spec_to_dict(spawn_spec_row) if spawn_spec_row else None) if spawn_request_row else None,
        }
    finally:
        await db.close()
