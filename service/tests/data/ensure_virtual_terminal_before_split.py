async def ensure_virtual_terminal(agent_id: str, req: VirtualTerminalEnsureRequest, request: Request):
    """Bridge-driven creation of a synthesized terminal_session row.

    Managed pi runs use a persistent `omp --mode rpc` child whose AgentSessionEvent
    stream is synthesized by the bridge into a human-readable terminal_output
    feed. There is no real PTY — the bridge owns the lifecycle. This endpoint is
    idempotent: a second call for the same agent on the same bridge returns the
    existing virtual terminal row. See docs/plans/pi-persistent-rpc.md.
    """
    db = await get_db()
    try:
        agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        bridge_id = str(req.bridgeId or "").strip()
        if not bridge_id:
            raise HTTPException(400, "bridgeId is required")
        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "pi")
        virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME.get(runtime)
        if not virtual_command:
            raise HTTPException(
                409,
                f'Virtual terminal is available for runtimes {sorted(VIRTUAL_RPC_COMMANDS_BY_RUNTIME)} only (got runtime="{runtime}")',
            )

        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE bridge_id = ? ORDER BY last_seen DESC LIMIT 1",
            (bridge_id,),
        )).fetchone()
        if not env_row:
            raise HTTPException(404, f'No environment registered for bridgeId "{bridge_id}"')
        environment_id = env_row["id"]

        session_row = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
              AND environment_id = ?
              AND status IN ('running', 'recovering', 'starting', 'managed-warm')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, environment_id),
        )).fetchone()
        if not session_row:
            raise HTTPException(
                409,
                f'No active agent_session for "{agent_id}" on environment "{environment_id}". '
                f'The bridge should dispatch at least once before requesting a virtual terminal.',
            )
        session_id = session_row["id"]

        # Agent-scoped lookup: one virtual terminal per agent across all of
        # its agent_sessions. If a prior session created the row and is now
        # stale, re-anchor the terminal's session_id (and the new session's
        # terminal_id pointer) to the requesting session so the
        # CASCADE-on-delete FK keeps the row alive once the original
        # session row is eventually cleaned up.
        existing = await (await db.execute(
            """
            SELECT *
            FROM terminal_sessions
            WHERE agent_id = ?
              AND command = ?
              AND status NOT IN ('stopped', 'failed')
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (agent_id, virtual_command),
        )).fetchone()
        if existing:
            existing_session_id = existing["session_id"]
            if existing_session_id != session_id:
                rebind_now = _now()
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET session_id = ?,
                        bridge_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (session_id, bridge_id, rebind_now, existing["id"]),
                )
                # Detach the prior session from the terminal but keep its
                # historical record otherwise intact.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = '',
                        terminal_command = ''
                    WHERE id = ? AND terminal_id = ?
                    """,
                    (existing_session_id, existing["id"]),
                )
                # Point the new active session at the terminal.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = ?,
                        terminal_status = 'running',
                        terminal_command = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (existing["id"], virtual_command, rebind_now, session_id),
                )
                await _append_terminal_event(
                    db,
                    existing["id"],
                    "virtual_pi_rpc_reanchored",
                    json.dumps({
                        "fromSessionId": existing_session_id,
                        "toSessionId": session_id,
                        "bridgeId": bridge_id,
                    }),
                )
                await db.commit()
                existing = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (existing["id"],),
                )).fetchone()
                session_row = await (await db.execute(
                    "SELECT * FROM agent_sessions WHERE id = ?",
                    (session_id,),
                )).fetchone()
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(existing),
                "session": _agent_session_to_dict(session_row),
                "reused": True,
            }

        # Plan 4 (2026-05-25) synth-terminal deprecation: when this runtime
        # routes through a *-aify wrapper PTY, the wrapper IS the terminal —
        # don't create a synth row in parallel. Reuse of a pre-existing synth
        # row (handled above) is still allowed for backwards compatibility
        # and for the hard-failure fallback path that may seed one explicitly.
        settings_for_synth_gate = await _load_settings(db)
        if not _synth_terminal_should_be_created(runtime, settings_for_synth_gate):
            raise HTTPException(
                409,
                f'Synth terminal creation skipped for wrapper-backed runtime "{runtime}" '
                f'(Plan 4 deprecation — the wrapper PTY is the terminal).',
            )

        workspace = str(req.workspace or session_row["workspace"] or "").strip()
        terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        requested_by = str(req.requestedBy or "bridge-rpc").strip() or "bridge-rpc"
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                agent_id,
                environment_id,
                bridge_id,
                runtime,
                workspace,
                virtual_command,
                "",
                "running",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            f"virtual_{runtime}_rpc_attached",
            json.dumps({
                "requestedBy": requested_by,
                "sessionId": session_id,
                "bridgeId": bridge_id,
                "sessionHandle": req.sessionHandle or "",
            }),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_id = ?,
                terminal_status = 'running',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (terminal_id, virtual_command, workspace, now, session_id),
        )
        next_runtime_state = _json_loads_or(agent["runtime_state"], {}) or {}
        next_runtime_state["virtualTerminal"] = True
        next_runtime_state["virtualTerminalId"] = terminal_id
        await db.execute(
            """
            UPDATE agents
            SET runtime_state = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(next_runtime_state), now, agent_id),
        )
        # The agent now has a live worker (virtualTerminalId + terminal_status
        # running). Invalidate the live-status cache so it recomputes to online
        # immediately instead of lying `available` until the 60s sweep.
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "terminal_started",
                {
                    "terminalId": terminal_id,
                    "sessionId": session_id,
                    "agentId": agent_id,
                    "virtual": True,
                },
            )
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
            "reused": False,
        }
    finally:
        await db.close()
