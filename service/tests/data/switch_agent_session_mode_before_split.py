"""The pre-split `switch_agent_session_mode`, frozen.

Not imported by anything. It is the ONE true original that
`test_switch_agent_session_mode_split_is_inert.py` inlines every extraction back against — see the
analytics precedent for why one fixture beats a chain of per-slice copies.

Captured from `git show HEAD:service/routers/agents/session_mode.py` at the commit before the first
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def switch_agent_session_mode(agent_id: str, req: AgentSessionModeSwitchRequest, request: Request):
    """Plan 6 C1 (2026-05-26): operator-driven resident/managed mode flip.

    Today the wrapper auto-detects via `[ -t 0 ]`; this endpoint lets the
    operator override the agent's `session_mode` regardless of how the
    wrapper was launched. Edge cases the server protects against (unless
    `force=true` is passed):

    - Active dispatch run in flight -> 409 (switching mid-turn would
      stall the run; wait for it to finish).

    (The former hermes-without-gatewayUrl 409 guard was removed: under the
    api_server model resident hermes resumes its pinned session via
    `--resume` and never needs a gateway URL — it was a tui_gateway-era
    requirement.)

    Audit log: a `dispatch_events` row of type
    `mode_switch_<old>_to_<new>` is appended with body
    `agentId=<id> by=<requestedBy>`, providing traceability without a
    new table.

    State-transition side effects (C2):
    - resident -> managed: best-effort eager-spawn of a wrapper PTY so
      the next dispatch lands in a ready Console (mirrors the spawn
      path used by `_ensure_managed_pty_for_dispatch` during /dispatch).
    - managed -> resident: best-effort release of any active managed
      PTY by flipping its status to 'stopping' so the bridge reconciles
      the close cleanly. Operator must launch a resident `*-aify`
      session themselves for the agent to come back online.

    Side-effect failures do not roll back the mode change itself —
    operators can always re-attach manually. The `sideEffects` field in
    the response surfaces what happened (or what failed).
    """
    validate_name(agent_id, "agent ID")
    new_mode = _normalize_session_mode(req.mode)
    requested_raw = str(req.mode or "").strip().lower()
    if requested_raw not in _SESSION_MODES:
        raise HTTPException(400, "mode must be 'resident' or 'managed'")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        current_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime = _normalize_runtime(row["runtime"] or "generic")
        current_runtime_state = _json_loads_or(row["runtime_state"], {})
        resident_candidate = current_runtime_state.get("manualResidentCandidate")
        if not isinstance(resident_candidate, dict):
            resident_candidate = {}
        row_runtime_config = _json_loads_or(row["runtime_config"], {})
        candidate_runtime_config = resident_candidate.get("runtimeConfig") if isinstance(resident_candidate.get("runtimeConfig"), dict) else {}
        switch_runtime_config = (
            {**row_runtime_config, **candidate_runtime_config}
            if new_mode == "resident" and candidate_runtime_config
            else row_runtime_config
        )
        switch_session_handle = str(
            (resident_candidate.get("sessionHandle") if new_mode == "resident" else "")
            or row["session_handle"]
            or ""
        ).strip()
        # Adopt the resident candidate's runtime when switching to resident. A
        # resident wrapper of a different runtime (e.g. a hermes hermes-aify
        # session registering against an agent last seen as managed pi) records
        # itself as a manualResidentCandidate with runtime="hermes". Without
        # this, the switch promoted the candidate's bridge/handle/config but
        # kept the stale runtime, producing an inconsistent pi-resident agent
        # pointing at a hermes bridge — the switch appeared to do nothing.
        effective_runtime = runtime
        if new_mode == "resident":
            candidate_runtime = str(resident_candidate.get("runtime") or "").strip()
            if candidate_runtime:
                effective_runtime = _normalize_runtime(candidate_runtime)

        if current_mode == new_mode:
            return {
                "ok": True,
                "agentId": agent_id,
                "mode": new_mode,
                "previousMode": current_mode,
                "changed": False,
            }

        # G2 (2026-06-03): block managed->resident for runtimes that don't
        # support a resident bridge (pi, opencode). Without this guard the flip
        # produces a `presence-only` agent whose every dispatch is rejected as
        # undeliverable — a silent footgun. Source resident support from the
        # runtime adapter (claude/codex/hermes = True; pi/opencode = False).
        # `force=true` may override (operator-initiated metadata-only flip), but
        # we still attach a clear warning to the response so the limbo is visible.
        forced_resident_warning = ""
        switch_warnings = []
        if new_mode == "resident":
            try:
                from service.runtimes import adapter_for
                _resident_supported = bool(adapter_for(effective_runtime).supports_resident)
            except Exception:
                _resident_supported = False
            if not _resident_supported:
                if not req.force:
                    raise HTTPException(
                        409,
                        (
                            f"resident mode is not supported for {effective_runtime}; "
                            "it is managed-only. Keep this agent managed, or pass "
                            "force=true to change metadata only (it will be undeliverable)."
                        ),
                    )
                forced_resident_warning = (
                    f"resident mode is not supported for {effective_runtime} (managed-only); "
                    "forced switch leaves this agent presence-only and every dispatch will be "
                    "rejected until it is switched back to managed."
                )

        if not req.force:
            blocking = await _get_blocking_active_run(db, agent_id)
            if blocking:
                raise HTTPException(
                    409,
                    f"Agent has an active dispatch run (runId={blocking.get('runId')}); wait for it to finish or pass force=true",
                )
            # api_server model: resident hermes resumes its pinned session via --resume; no gatewayUrl needed (was a tui_gateway-era guard)
            if new_mode == "managed":
                managed_session = await (await db.execute(
                    """
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                      AND status NOT IN ('failed','lost','stopped','ended','completed','cancelled')
                    ORDER BY last_seen DESC
                    LIMIT 1
                    """,
                    (agent_id, runtime),
                )).fetchone()
                if not managed_session:
                    # RELAXED (2026-06-11): this used to 409, but since lazy auto-start a
                    # managed agent with no live backing is simply `available` — it cold-starts
                    # on the next send and resolves its environment at claim time. Blocking the
                    # flip stranded resident agents on offline machines (operator-reported: an
                    # old resident session on another PC could not be switched). Allow the
                    # switch and surface a warning instead.
                    switch_warnings.append(
                        "No live managed backing yet — the agent reads `available` and a managed "
                        "worker will cold-start on the next send once its environment is online."
                    )

        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        runtime_config = switch_runtime_config
        runtime_state = dict(current_runtime_state)
        runtime_state.pop("pendingResidentTakeover", None)
        if new_mode == "resident":
            if resident_candidate.get("bridgeId"):
                runtime_state["bridgeInstanceId"] = str(resident_candidate.get("bridgeId") or "")
            if resident_candidate:
                runtime_state["manualResidentCandidate"] = resident_candidate
        else:
            runtime_state.pop("manualResidentCandidate", None)
        runtime_state["ownership"] = {
            "mode": new_mode,
            "previousMode": current_mode,
            "reason": "manual_session_mode_switch",
            "requestedBy": requested_by,
            "at": now,
        }
        if new_mode == "managed" and not str(runtime_state.get("environmentId") or "").strip():
            # ENV-BINDING INFERENCE (2026-06-12, operator-reported): flipping resident→managed
            # left runtime_state without an environmentId — the agent then rendered in the
            # Sessions page's "unassigned" group and looked unreachable until the operator
            # hand-edited the identity or a spawn re-bound it. The right binding is almost
            # always derivable: the latest session row's environment, else the (online-first,
            # newest) environment registered for the agent's own machine.
            inferred_env = ""
            try:
                _ls = await (await db.execute(
                    "SELECT environment_id FROM agent_sessions WHERE agent_id = ? "
                    "AND COALESCE(environment_id, '') != '' "
                    "ORDER BY datetime(COALESCE(last_seen, created_at)) DESC LIMIT 1",
                    (agent_id,),
                )).fetchone()
                inferred_env = str((_ls["environment_id"] if _ls else "") or "").strip()
                if not inferred_env:
                    _machine = _normalize_machine_id(row["machine_id"] or "")
                    if _machine:
                        _er = await (await db.execute(
                            "SELECT id FROM environments WHERE machine_id = ? "
                            "AND status NOT IN ('forgotten', 'disabled') "
                            "ORDER BY CASE WHEN status = 'online' THEN 0 ELSE 1 END, "
                            "datetime(COALESCE(last_seen, '')) DESC LIMIT 1",
                            (_machine,),
                        )).fetchone()
                        inferred_env = str((_er["id"] if _er else "") or "").strip()
            except Exception:
                inferred_env = ""
            if inferred_env:
                runtime_state["environmentId"] = inferred_env
            else:
                switch_warnings.append(
                    "No environment binding could be inferred for this machine — the agent "
                    "will appear under 'unassigned' on the Sessions page until an environment "
                    "bridge for its machine comes online."
                )
        next_launch_mode = "managed" if new_mode == "managed" else "detached"
        capabilities = _default_capabilities_for(
            effective_runtime,
            new_mode,
            switch_session_handle,
            runtime_config,
        )
        next_machine_id = _normalize_machine_id(
            resident_candidate.get("machineId") or row["machine_id"] or ""
            if new_mode == "resident"
            else row["machine_id"] or ""
        )
        next_cwd = (
            str(resident_candidate.get("cwd") or row["cwd"] or "")
            if new_mode == "resident"
            else str(row["cwd"] or "")
        )
        await db.execute(
            """
            UPDATE agents
            SET session_mode = ?,
                runtime = ?,
                launch_mode = ?,
                session_handle = ?,
                machine_id = ?,
                cwd = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                driver_state = ?,
                status = CASE WHEN status = 'stopped' THEN 'idle' ELSE status END,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                new_mode,
                effective_runtime,
                next_launch_mode,
                switch_session_handle,
                next_machine_id,
                next_cwd,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(runtime_state),
                # Switching TO resident while adopting a LIVE resident bridge keeps that
                # session as the active driver. The previous unconditional 'idle' clobbered
                # the 'driving' the just-registered resident session had set, so its OWN
                # channel sidecar was told to RELEASE on its next claim/heartbeat and
                # resident delivery silently died — sends said "sent", runs queued forever
                # (sc-manager, 2026-06-12: launch terminal first, click switch second).
                ("driving" if (new_mode == "resident" and str(resident_candidate.get("bridgeId") or "").strip()) else "idle"),
                f"Manually switched from {current_mode} to {new_mode} by {requested_by}"
                + (f" (runtime {runtime}->{effective_runtime})" if effective_runtime != runtime else "")
                + ".",
                now,
                agent_id,
            ),
        )
        # C1 audit log — `dispatch_events.run_id` is a NOT NULL FK to
        # `dispatch_runs(id)`, so we can't attach an agent-level event with
        # an empty run_id. Workaround: insert a synthetic anchor row into
        # `dispatch_runs` with status='completed' (so it never enters the
        # claim/queue paths) and a recognizable subject. Then attach the
        # mode_switch event to it. Operators see the audit row in the same
        # per-agent dispatch history view; no new table needed.
        event_type = f"mode_switch_{current_mode}_to_{new_mode}"
        audit_run_id = f"mode_switch_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, from_agent, target_agent, dispatch_mode, execution_mode,
                runtime, message_type, subject, body, status, summary, requested_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                audit_run_id,
                requested_by,
                agent_id,
                "audit",
                "audit",
                effective_runtime,
                "audit",
                "session-mode-switch",
                f"agentId={agent_id} {current_mode}->{new_mode} by={requested_by}",
                "completed",
                event_type,
                now,
                now,
            ),
        )
        await _append_dispatch_event(
            db,
            audit_run_id,
            event_type,
            f"agentId={agent_id} by={requested_by}",
        )

        # C2 state-transition side effects. Wrapped in try/except so a side
        # effect failure (e.g., environment offline, no live agent_sessions
        # row) does NOT roll back the mode change — operators can still
        # re-spawn/attach manually. Failures surface in the response's
        # `sideEffects.error` field.
        settings = await _load_settings(db)
        side_effects: dict[str, Any] = {}
        try:
            if new_mode == "managed":
                # FIX SET B1 (2026-06-03): wrapper-backed managed runtimes
                # (codex/hermes) must NOT eager-start via
                # _ensure_managed_pty_for_dispatch — that re-attaches a PTY to the
                # leftover RESIDENT agent_sessions row (a resident `*-aify --resume`,
                # NOT a managed-warm worker), so no `managed-wrapper-child` bridge
                # registers and the next 'channel' run is rejected
                # `managed_wrapper_child_required` → queued forever (the lc-coder
                # resident→managed strand). Instead: RETIRE the leftover non-terminal
                # resident agent_sessions row(s) and cold-start a managed-warm
                # spawn_request so a bridge spawns a real managed worker whose
                # in-session MCP registers the wrapper-child claimer.
                if _managed_via_wrapper_for_runtime(settings, runtime):
                    await db.execute(
                        """
                        UPDATE agent_sessions
                        SET status = 'retired', last_seen = ?
                        WHERE agent_id = ?
                          AND COALESCE(status, '') NOT IN ('retired', 'stopped', 'terminated', 'failed')
                        """,
                        (now, agent_id),
                    )
                    _switch_coldstart_warnings: list[str] = []
                    coldstarted = await _coldstart_spawn_request_for_dispatch(
                        db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by,
                        warnings=_switch_coldstart_warnings,
                    )
                    if _switch_coldstart_warnings:
                        side_effects["handleCollisionWarnings"] = _switch_coldstart_warnings
                    if coldstarted:
                        side_effects["managedSpawnRequested"] = True
                    else:
                        side_effects["error"] = _coldstart_refusal_message(
                            _switch_coldstart_warnings, runtime)
                else:
                    terminal = await _ensure_managed_pty_for_dispatch(
                        db, agent_id, runtime=runtime, settings=settings, requested_by=requested_by
                    )
                    if terminal is not None:
                        # `_ensure_managed_pty_for_dispatch` returns either a sqlite
                        # Row (existing active terminal) or a dict (newly spawned).
                        try:
                            side_effects["managedTerminalId"] = terminal["id"] if "id" in terminal.keys() else terminal.get("id")
                        except Exception:
                            side_effects["managedTerminalId"] = None
                    else:
                        side_effects["error"] = "No managed session/backing was available for eager PTY start."
            else:
                # managed -> resident: best-effort stop of any active managed PTY.
                active = await _active_terminal_for_agent(db, agent_id, settings=settings)
                if active is not None:
                    terminal_id = active["terminal_id"] if "terminal_id" in active.keys() else None
                    session_id = active["session_id"] if "session_id" in active.keys() else ""
                    if terminal_id:
                        await db.execute(
                            "UPDATE terminal_sessions SET status = 'stopping', updated_at = ? WHERE id = ?",
                            (now, terminal_id),
                        )
                        if session_id:
                            await db.execute(
                                "UPDATE agent_sessions SET terminal_status = 'stopping', last_seen = ? WHERE id = ?",
                                (now, session_id),
                            )
                        side_effects["stoppedTerminalId"] = terminal_id
        except Exception as exc:  # pragma: no cover — surface, do not abort
            logger.warning("session-mode side-effect failed for %s: %s", agent_id, exc)
            side_effects["error"] = str(exc)

        await db.commit()
        # Takeover/resume command for the operator. On a managed -> resident
        # switch this is the command the operator runs to drive the SAME session
        # interactively; mirrored in the dashboard. Best-effort (empty if the
        # adapter has none).
        resume_command = _resume_command_for(effective_runtime, switch_session_handle, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_session_mode_updated",
                {"agentId": agent_id, "mode": new_mode, "previousMode": current_mode},
            )
        updated_agent = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        updated_status = await _compute_agent_status(updated_agent, db)
        updated_dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        response_payload = {
            "ok": True,
            "agentId": agent_id,
            "mode": new_mode,
            "previousMode": current_mode,
            "changed": True,
            "resumeCommand": resume_command,
            "sideEffects": side_effects,
            "agent": _agent_record_to_dict(updated_agent, updated_status, 0, updated_dispatch_state),
        }
        if forced_resident_warning:
            switch_warnings.insert(0, forced_resident_warning)
        if switch_warnings:
            response_payload["warning"] = " ".join(switch_warnings)
        return response_payload
    finally:
        await db.close()
