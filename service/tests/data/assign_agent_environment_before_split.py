"""The pre-split `assign_agent_environment`, frozen.

Not imported by anything. It is the ONE true original that
`test_assign_agent_environment_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/config.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def assign_agent_environment(agent_id: str, req: AgentEnvironmentAssignRequest, request: Request):
    validate_name(agent_id, "agent ID")
    environment_id = str(req.environmentId or "").strip()
    if not environment_id:
        raise HTTPException(400, "environmentId is required")

    db = await get_db()
    try:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await agent_cursor.fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{environment_id}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment_id}" is {environment.get("status") or "unknown"}, not online')

        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "generic")
        if not _runtime_capability_for_environment(environment, runtime):
            raise HTTPException(400, f'Environment "{environment_id}" does not advertise runtime "{runtime}"')
        workspace, workspace_root = _workspace_for_environment(environment, req.workspace, agent["cwd"] or "")
        settings = await _load_settings(db)
        model = str(req.model if req.model is not None else (agent["model"] or "")).strip()
        if not model:
            if runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        existing_runtime_config = _json_loads_or(agent["runtime_config"], {})
        requested_runtime_config = req.runtimeConfig or {}
        runtime_config = {**existing_runtime_config, **requested_runtime_config}
        if runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        now = _now()
        previous_runtime = _normalize_runtime(agent["runtime"] or runtime)
        latest_session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY
                CASE WHEN COALESCE(NULLIF(session_handle, ''), '') != '' THEN 0 ELSE 1 END,
                last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
        latest_session_handle = str((latest_session["session_handle"] if latest_session else "") or "").strip()
        agent_runtime_state = _json_loads_or(agent["runtime_state"], {})
        state_handle = _runtime_handle_from_state(previous_runtime, agent_runtime_state)
        preserve_handle = ""
        if previous_runtime == runtime:
            preserve_handle = str(agent["session_handle"] or latest_session_handle or state_handle or "").strip()
        preserved_runtime_state = _runtime_state_with_handle(runtime, {}, preserve_handle)

        spec_cursor = await db.execute(
            "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
        spec = await spec_cursor.fetchone()
        if spec:
            spec_id = spec["id"]
            await db.execute(
                """
                UPDATE spawn_specs
                SET environment_id = ?, runtime = ?, workspace = ?, model = ?, metadata = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    json.dumps({**_json_loads_or(spec["metadata"], {}), **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    agent_id,
                ),
            )
        else:
            spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO spawn_specs (
                    id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                    system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                    context_policy, restart_policy, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    model,
                    "",
                    "managed-warm",
                    "",
                    agent["instructions"] or "",
                    "{}",
                    "[]",
                    "{}",
                    "{}",
                    "{}",
                    json.dumps({"createdBy": req.requestedBy or "dashboard", "assignedFromDashboard": True, **({"runtimeConfig": runtime_config} if runtime_config else {})}),
                    now,
                    now,
                ),
            )

        await db.execute(
            """
            UPDATE agent_sessions
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                session_handle = ?,
                spawn_spec_id = COALESCE(NULLIF(spawn_spec_id, ''), ?),
                status = CASE WHEN status IN ('starting','running','recovering','restarting') THEN 'lost' ELSE status END,
                ended_at = CASE WHEN status IN ('starting','running','recovering','restarting') THEN COALESCE(ended_at, ?) ELSE ended_at END,
                last_seen = ?
            WHERE agent_id = ?
            """,
            (environment_id, runtime, workspace, preserve_handle, spec_id, now, now, agent_id),
        )
        session_cursor = await db.execute(
            "SELECT id FROM agent_sessions WHERE agent_id = ? ORDER BY last_seen DESC LIMIT 1",
            (agent_id,),
        )
        existing_session = await session_cursor.fetchone()
        if not existing_session:
            session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode,
                    owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
                    process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    "managed-warm",
                    "managed",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    preserve_handle,
                    "",
                    spec_id,
                    None,
                    json.dumps({"persistent": True, "nativeResume": bool(preserve_handle), "bridgeResume": True, "adopted": True}),
                    "{}",
                    "stopped",
                    now,
                    now,
                    now,
                ),
            )
        await db.execute(
            """
            UPDATE spawn_requests
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                workspace_root = ?,
                updated_at = ?
            WHERE agent_id = ?
              AND status IN ('queued','claimed','starting')
            """,
            (environment_id, runtime, workspace, workspace_root, now, agent_id),
        )
        capabilities = _default_capabilities_for(runtime, "managed", preserve_handle, runtime_config)
        await db.execute(
            """
            UPDATE agents
            SET cwd = ?,
                model = ?,
                runtime = ?,
                machine_id = ?,
                launch_mode = 'none',
                session_mode = 'managed',
                session_handle = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                last_seen = ?
            WHERE id = ?
            """,
            (
                workspace,
                model,
                runtime,
                _normalize_machine_id(environment.get("machineId")),
                preserve_handle,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(preserved_runtime_state),
                now,
                agent_id,
            ),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_environment_assigned", {"agentId": agent_id, "environmentId": environment_id})
        return {
            "ok": True,
            "agentId": agent_id,
            "environmentId": environment_id,
            "runtime": runtime,
            "workspace": workspace,
            "spawnSpecId": spec_id,
        }
    finally:
        await db.close()
