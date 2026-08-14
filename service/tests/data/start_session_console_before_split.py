"""The pre-split `start_session_console`, frozen.

Not imported by anything. It is the ONE true original that
`test_start_session_console_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/sessions.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def start_session_console(session_id: str, req: ConsoleStartRequest, request: Request):
    db = await get_db()
    try:
        session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (session["environment_id"],))).fetchone()
        if not env_row:
            raise HTTPException(409, f'Environment "{session["environment_id"]}" is not available')
        settings = await _load_settings(db)

        # Slice 3: reuse the existing live wrapper PTY for this agent
        # session when one is already attached. Avoids the symptom
        # where each "Start Console" click (or auto-attach via the
        # dashboard) spawns a fresh wrapper PTY even though a previous
        # one is still running — operator-visible "console pops up
        # again". The dispatch path (via _ensure_managed_pty_for_dispatch
        # -> _active_terminal_for_agent) already reuses; this brings the
        # manual-start path to parity.
        existing_terminal_id = str(session["terminal_id"] or "").strip()
        if existing_terminal_id:
            existing_terminal = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (existing_terminal_id,),
            )).fetchone()
            if existing_terminal:
                existing_status = str(existing_terminal["status"] or "").strip().lower()
                if existing_status in {"starting", "attached", "running", "active", "idle", "recovering"}:
                    await _append_terminal_event(
                        db,
                        existing_terminal_id,
                        "console_attach_reused_existing",
                        json.dumps({
                            "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                            "sessionId": session_id,
                            "agentId": session["agent_id"],
                        }),
                    )
                    await db.commit()
                    return {
                        "ok": True,
                        "terminal": _terminal_session_to_dict(existing_terminal),
                        "reused": True,
                    }

        # Agent-scoped virtual terminal reattach (Phase 2 follow-up).
        # The virtual terminal_session created by /agents/{id}/virtual-terminal/ensure
        # is canonical per-agent: ONE row per agent regardless of how many
        # agent_sessions exist over the agent's lifetime. The bridge creates
        # it tied to whichever agent_session was active at first dispatch,
        # but a later dashboard Console click on a DIFFERENT agent_session
        # for the same agent must attach to that same virtual terminal —
        # otherwise the dashboard would spawn a fresh pi-aify PTY console
        # and the operator sees a different terminal than the one actually
        # driving their dispatches. Skip the PTY env-supports check too:
        # virtual terminals don't need node-pty.
        agent_row_for_virtual = await (await db.execute(
            "SELECT id, runtime, runtime_state FROM agents WHERE id = ?",
            (session["agent_id"],),
        )).fetchone()
        if agent_row_for_virtual:
            agent_runtime_state = _json_loads_or(agent_row_for_virtual["runtime_state"], {}) or {}
            virtual_terminal_id = str(agent_runtime_state.get("virtualTerminalId") or "").strip()
            if virtual_terminal_id:
                virtual_terminal = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (virtual_terminal_id,),
                )).fetchone()
                if virtual_terminal:
                    virtual_status = str(virtual_terminal["status"] or "").strip().lower()
                    virtual_command = str(virtual_terminal["command"] or "")
                    if (
                        virtual_command in VIRTUAL_RPC_COMMAND_SET
                        and virtual_status in {"starting", "running", "recovering", "active", "idle"}
                    ):
                        attach_now = _now()
                        # Point the requesting session at the canonical
                        # virtual terminal so the dashboard's session view
                        # follows it.
                        await db.execute(
                            """
                            UPDATE agent_sessions
                            SET terminal_id = ?,
                                terminal_status = ?,
                                terminal_command = ?,
                                last_seen = ?
                            WHERE id = ?
                            """,
                            (virtual_terminal_id, virtual_status, virtual_command, attach_now, session_id),
                        )
                        await _append_terminal_event(
                            db,
                            virtual_terminal_id,
                            "virtual_pi_rpc_console_attached",
                            json.dumps({
                                "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                                "sessionId": session_id,
                                "agentId": session["agent_id"],
                            }),
                        )
                        await db.commit()
                        updated_session_for_virtual = await (await db.execute(
                            "SELECT * FROM agent_sessions WHERE id = ?",
                            (session_id,),
                        )).fetchone()
                        ws_for_virtual = await _get_ws(request)
                        if ws_for_virtual:
                            await ws_for_virtual.broadcast(
                                "terminal_started",
                                {
                                    "terminalId": virtual_terminal_id,
                                    "sessionId": session_id,
                                    "agentId": session["agent_id"],
                                    "virtual": True,
                                    "reused": True,
                                },
                            )
                        return {
                            "ok": True,
                            "terminal": _terminal_session_to_dict(virtual_terminal),
                            "session": _agent_session_to_dict(updated_session_for_virtual),
                            "reused": True,
                            "virtual": True,
                        }

        runtime = _normalize_runtime(session["runtime"] or "")
        if runtime == "pi":
            environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
            if not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
                raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')
            workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
            terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            now = _now()
            bridge_id = str(environment.get("bridgeId") or "").strip()
            virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME["pi"]
            requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
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
                    session["agent_id"],
                    session["environment_id"],
                    bridge_id,
                    session["runtime"],
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
                "virtual_pi_rpc_console_started",
                json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace}),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = 'running',
                    terminal_command = ?,
                    terminal_workspace = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (bridge_id, terminal_id, virtual_command, workspace, now, session_id),
            )
            next_runtime_state = _json_loads_or((agent_row_for_virtual["runtime_state"] if agent_row_for_virtual else "") or "{}", {}) or {}
            next_runtime_state["virtualTerminal"] = True
            next_runtime_state["virtualTerminalId"] = terminal_id
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_runtime_state), now, session["agent_id"]),
            )
            # The agent now has a live worker (virtualTerminalId + terminal_status
            # running). Invalidate the live-status cache so it recomputes to online
            # immediately instead of lying `available` until the 60s sweep.
            await _invalidate_agent_live_state(db, session["agent_id"])
            await db.commit()
            terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
            ws_for_virtual = await _get_ws(request)
            if ws_for_virtual:
                await ws_for_virtual.broadcast(
                    "terminal_started",
                    {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"], "virtual": True},
                )
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(terminal),
                "session": _agent_session_to_dict(updated_session),
                "reused": False,
                "virtual": True,
            }

        environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
        if not _environment_supports_terminal(environment, session["runtime"]):
            env_id = environment.get("id")
            if not bool(environment.get("terminal")) or not bool(environment.get("pty")):
                # Whole-environment PTY capability is off — not a per-runtime
                # issue. The bridge on that host reports no terminal/pty
                # (usually node-pty is not installed/built there).
                detail = (
                    f'Environment "{env_id}" has no PTY/terminal capability — its bridge reports '
                    f'terminal={bool(environment.get("terminal"))}, pty={bool(environment.get("pty"))}. '
                    f'This blocks the Console for ALL runtimes there (not just "{session["runtime"]}"). '
                    f'Fix: install/build node-pty for the aify-comms bridge on that host '
                    f'(reinstall via install.sh and restart the bridge), then retry. '
                    f'Use an environment that advertises terminal support in the meantime.'
                )
            else:
                advertised = ", ".join(
                    str(r) for r in (environment.get("terminalRuntimes") or [])
                ) or "none"
                detail = (
                    f'Environment "{env_id}" supports the Console but not for runtime '
                    f'"{session["runtime"]}". It advertises terminal runtimes: {advertised}. '
                    f'Spawn/select a supported runtime, or update that bridge.'
                )
            raise HTTPException(409, detail)
        if runtime == "pi" and not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
            raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')

        workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
        terminal_id = f"term_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        now = _now()
        command = str(req.command or "").strip() or _default_console_command(session, workspace, interactive=True)
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        bridge_id = str(environment.get("bridgeId") or "").strip()
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
                session["agent_id"],
                session["environment_id"],
                bridge_id,
                session["runtime"],
                workspace,
                command,
                "",
                "starting",
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
            "console_start_requested",
            json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace, "command": command}),
        )
        await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=session["environment_id"],
            bridge_id=bridge_id,
            action="start",
            requested_by=requested_by,
            body=command,
        )

        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'console',
                owner_bridge_id = ?,
                terminal_id = ?,
                terminal_status = 'starting',
                terminal_command = ?,
                terminal_workspace = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (bridge_id, terminal_id, command, workspace, now, session_id),
        )
        await db.commit()
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_started", {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"]})
        return {
            "ok": True,
            "terminal": _terminal_session_to_dict(terminal),
            "session": _agent_session_to_dict(updated_session),
        }
    finally:
        await db.close()
