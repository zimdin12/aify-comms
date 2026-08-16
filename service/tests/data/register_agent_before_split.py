"""`register_agent` exactly as it was before any extract-method split — the proof's reference.

Committed as a FIXTURE rather than recovered from git on demand, for the reason
test_analytics_split_is_inert.py records: a proof that needs `.git` to run cannot run from a clean
clone, and the v0.5 route gates already shipped broken that way once.

CAPTURED WITH AN EXPLICIT utf-8 DECODE. The first version of this fixture was generated with
`subprocess.run(..., text=True)`, which decodes using the WINDOWS LOCALE encoding — every em dash and
arrow in 684 lines of comments came through mangled, and the round trip failed on a block nobody had
touched. The gate caught it. If this file is ever regenerated, decode the bytes explicitly.

NOT AN IMPORTABLE MODULE. This is a function lifted out of its module, so it reads names that were in
scope THERE and are not here. `scripts/undefined_name_sweep.py` skips `service/tests/data/` for exactly
that reason. Nothing should import this file; the test reads it as text.

EDITED THREE TIMES SINCE CAPTURE, and the rule is the same one `environment_heartbeat_before_split.py`
records. The round trip proves the split was a pure block-lift OF THE CODE AS IT STANDS, so a later
change to a line the split did not move must be applied here IDENTICALLY, or the proof forbids ever
editing the function again. The one change: `incoming_started = _timestamp_sort_key(...)` became
`_parsed_timestamp(...)` when the tombstone gates stopped treating an unparseable, caller-supplied
`bridgeStartedAt` as newer than every real timestamp. The second: three `req.launchMode` reads became
`_normalize_launch_mode(req.launchMode)` when the stop marker stopped being case-sensitive. The third:
`req.status or "idle"` gained the same case fold, for the same reason one field over. Same statements,
same positions, normalising builders. Anything larger belongs in a reviewed re-capture,
not a fixture nudge to go green.
"""

async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        normalized_runtime = _normalize_runtime(req.runtime or "generic")
        normalized_session_mode = _normalize_session_mode(req.sessionMode or "resident")
        resolved_cwd = req.cwd or ""
        runtime_config = req.runtimeConfig or {}
        _validate_registration_cwd(
            agent_id=req.agentId,
            runtime=normalized_runtime,
            session_mode=normalized_session_mode,
            machine_id=req.machineId or "",
            cwd=resolved_cwd,
            runtime_config=runtime_config,
        )
        now = _now()
        tombstone = await _agent_tombstone(db, req.agentId)
        if tombstone and not req.restoreDeleted:
            if req.autoRegister:
                raise HTTPException(
                    410,
                    (
                        f"Agent '{req.agentId}' was intentionally removed at "
                        f"{tombstone['removed_at']}; auto re-registration is blocked."
                    ),
                )
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed. "
                    "Pass restoreDeleted=true to register this ID again."
                ),
            )
        if tombstone and req.restoreDeleted:
            # Tombstone-resurrection guard (2026-06-03). The bridge sets
            # restoreDeleted=true UNCONDITIONALLY on every auto/comms_register, so
            # a still-running bridge that predates the deletion would otherwise
            # clear the tombstone and resurrect a deliberately-removed agent
            # (it reappears in /api/v1/agents and the dashboard DM rail). Mirror
            # the environment forget-tombstone freshness check: only a GENUINE
            # fresh relaunch — a bridge whose bridgeStartedAt is NEWER than the
            # tombstone's removed_at — may restore. A passive auto re-register
            # from a bridge that launched BEFORE the deletion (or with no/older
            # bridgeStartedAt) keeps the agent deleted (410, tombstone untouched).
            #
            # An explicit, operator-initiated restore (restoreDeleted=true with
            # autoRegister=false — not a passive bridge beat) is preserved: a
            # deliberate operator bring-back still clears the tombstone.
            removed_at = _timestamp_sort_key(tombstone["removed_at"] if "removed_at" in tombstone.keys() else "")
            incoming_started = _parsed_timestamp(req.bridgeStartedAt)
            relaunched = bool(incoming_started) and (not removed_at or incoming_started > removed_at)
            if req.autoRegister and not relaunched:
                raise HTTPException(
                    410,
                    (
                        f"Agent '{req.agentId}' was intentionally removed at "
                        f"{tombstone['removed_at']}; a lingering bridge cannot "
                        "resurrect it. Relaunch the agent to restore."
                    ),
                )
            # FIX 4 (2026-06-03): COLLATE NOCASE so the explicit-restore clear path
            # matches the same row the case-insensitive lookup above found.
            await db.execute(
                "DELETE FROM agent_tombstones WHERE agent_id = ? COLLATE NOCASE",
                (req.agentId,),
            )
        existing = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        row = await existing.fetchone()
        bridge_id = (req.bridgeId or "").strip()
        terminal_id = str(req.terminalId or "").strip()
        # Mutual-exclusion collision guard (Task 4.1, 2026-05-30). One-driver
        # invariant: at most one driver per session at a time. If a process tries
        # to attach in a DIFFERENT session_mode than the one currently DRIVING
        # the session, reject with an actionable error so the operator switches
        # mode in the dashboard first (which releases the prior driver) rather
        # than silently colliding N wrappers / overwriting an active session.
        #
        # Scope: the guard fires ONLY on a cross-mode attach to a session that
        # is actively `driving`. Two cases are deliberately NOT hard-rejected
        # here because each is handled gracefully elsewhere, preserving the
        # invariant without an error:
        #   - SAME-mode re-attach/supersession by the same logical agent (a
        #     managed restart, or a second resident window) -> existing
        #     machine_id bridge supersession.
        #   - a RESIDENT registration against a DRIVING MANAGED agent -> the
        #     established `manualResidentCandidate` flow below parks the resident
        #     and returns `ownershipTransition=manual_switch_required` (it never
        #     lets the resident drive; the operator switches in the dashboard).
        # That leaves the genuinely-unhandled collision — a MANAGED registration
        # against a DRIVING RESIDENT session (which would otherwise silently
        # overwrite the live resident driver) — which is hard-rejected here.
        if row and not bool(req.restoreDeleted):
            existing_mode = _normalize_session_mode(row["session_mode"] or "resident")
            driver_state = str((row["driver_state"] if "driver_state" in row.keys() else "") or "idle").strip().lower()
            graceful_resident_candidate = (
                normalized_session_mode == "resident" and existing_mode == "managed"
            )
            if (
                driver_state == "driving"
                and existing_mode != normalized_session_mode
                and not graceful_resident_candidate
            ):
                resume_command = _resume_command_for(
                    row["runtime"] or normalized_runtime,
                    row["session_handle"] or "",
                    req.agentId,
                )
                detail = (
                    f"agent '{req.agentId}' is currently {existing_mode} — "
                    f"switch it to {normalized_session_mode} in the dashboard first, then run: "
                    f"{resume_command}"
                    if resume_command
                    else (
                        f"agent '{req.agentId}' is currently {existing_mode} — "
                        f"switch it to {normalized_session_mode} in the dashboard first."
                    )
                )
                raise HTTPException(409, detail)
        # Same-mode race guard (Phase 4, 2026-05-31). A fresh resident bridge of
        # the SAME mode, owned by a DIFFERENT bridge_id, is already driving this
        # identity — a second live wrapper would race it. Hard-reject (operator-
        # chosen) unless force=true: the operator deliberately takes over after
        # restarting the prior wrapper (wrappers surface this via the
        # AIFY_FORCE_REGISTER escape hatch). Stale prior bridges fall through and
        # are superseded normally (self-heal). Same-process periodic re-register
        # keeps its bridge_id and is excluded by `id != ?` in the helper.
        # NB: do NOT gate this on restoreDeleted — the bridge's auto-register
        # sends restoreDeleted=true unconditionally, so gating here would make
        # the guard dead in production. Restoring a tombstone is orthogonal: a
        # tombstoned agent has no live bridge to conflict with, so the freshness
        # check below simply finds nothing and the register proceeds.
        if row and bridge_id and not bool(getattr(req, "force", False)):
            settings_for_guard = await _load_settings(db)
            conflict = await _fresh_same_mode_bridge_conflict(
                db,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                new_bridge_id=bridge_id,
                session_mode=normalized_session_mode,
                lease_seconds=settings_for_guard.get("resident_lease_seconds", 150),
            )
            # SAME-SESSION RELAUNCH TAKEOVER (2026-06-13, the sc-manager stale+deaf
            # incident): a quick close-and-relaunch of a resident wrapper ALWAYS hit this
            # guard — kill-prior killed the old session seconds before the new bridge
            # booted, but the dead bridge's heartbeat lease (150s) made it look like a
            # "LIVE owner", the auto-register was 409'd (never retried), and the session
            # ran for hours with no binding file: sidecar mute (no inbound delivery, no
            # sidecar liveness) + runtime_state pinned to the dead bridge → `stale`.
            # When the incoming registration RESUMES the very session handle the
            # conflicting bridge holds, it is a relaunch of that same native session —
            # one session can only have one living process — so take over: supersede the
            # old bridge and proceed. A conflict with a DIFFERENT (or unknown) session
            # stays hard-409 (the real Phase-4 duplicate-identity protection).
            incoming_handle = str(req.sessionHandle or "").strip()
            conflict_handle = str(
                (conflict["session_handle"] if conflict and "session_handle" in conflict.keys() else "") or ""
            ).strip()
            if conflict and incoming_handle and incoming_handle == conflict_handle:
                # IN-FLIGHT PROTECTION (the Phase-4 operator-chosen invariant stays): a
                # prior bridge actively driving a claimed/running run is genuinely-live
                # evidence — never silently supersede it; the hard 409 below stands and
                # the bridge-side retry waits it out. Only an IDLE same-session owner
                # (the killed-prior relaunch case) is taken over.
                in_flight = await (await db.execute(
                    """
                    SELECT COUNT(*) FROM dispatch_runs
                    WHERE target_agent = ? AND status IN ('claimed', 'running')
                    """,
                    (req.agentId,),
                )).fetchone()
                if not int(in_flight[0] or 0):
                    await db.execute(
                        "UPDATE bridge_instances SET superseded_by = ?, superseded_at = ? WHERE id = ?",
                        (bridge_id, _now(), conflict["id"]),
                    )
                    logger.info(
                        "same-session relaunch takeover: agent=%s handle=%s superseded=%s by=%s",
                        req.agentId, incoming_handle, conflict["id"], bridge_id,
                    )
                    conflict = None
            if conflict:
                seen_s = _iso_to_epoch((conflict["last_seen"] or ""))
                ago = int(max(0, time.time() - seen_s)) if seen_s else 0
                resume_command = _resume_command_for(
                    row["runtime"] or normalized_runtime,
                    row["session_handle"] or "",
                    req.agentId,
                )
                detail = (
                    f"agent '{req.agentId}' already has a LIVE {normalized_session_mode} "
                    f"bridge (seen {ago}s ago). Stop that instance first, or pass force=true "
                    f"(AIFY_FORCE_REGISTER=1) to take over."
                )
                if resume_command:
                    detail += f" To resume after taking over: {resume_command}"
                raise HTTPException(409, detail)
        managed_wrapper_child = bool(req.managedWrapperChild) or (
            normalized_session_mode == "managed"
            and bool(terminal_id)
            and normalized_runtime in _CHANNEL_CLAIM_RUNTIMES
        )
        if managed_wrapper_child and row:
            runtime_config = _merge_runtime_policy_for_wrapper_reregister(
                _json_loads_or(row["runtime_config"], {}),
                runtime_config,
            )
        model_value = req.model or ""
        if managed_wrapper_child and not model_value and row and "model" in row.keys():
            model_value = row["model"] or ""
        # Re-register is a full state refresh: sessionHandle and runtime_state come
        # from the new request only. Preserving them across re-register let stale
        # Codex thread IDs survive a fresh codex-aify start, which then made
        # thread/resume fail with AbsolutePathBuf or "no rollout found".
        # Reject unexpanded shell placeholders (e.g. "$HERMES_SESSION_ID") so a
        # literal never gets stored as the resume handle — see
        # _sanitize_session_handle.
        session_handle = _sanitize_session_handle(req.sessionHandle or "")
        existing_state = json.dumps(_runtime_state_with_handle(normalized_runtime, {}, session_handle))
        # Description is team-facing metadata that survives re-register when the
        # caller does not pass a new value. Passing "" explicitly clears it.
        if req.description is None:
            description_value = (row["description"] if row and "description" in row.keys() else "") or ""
        else:
            description_value = req.description
        capabilities = req.capabilities
        if capabilities is None:
            capabilities = _default_capabilities_for(normalized_runtime, normalized_session_mode, session_handle, runtime_config)
        console_terminal = None
        if terminal_id and normalized_session_mode == "resident":
            console_terminal = await (
                await db.execute(
                    """
                    SELECT *
                    FROM terminal_sessions
                    WHERE id = ?
                      AND agent_id = ?
                      AND status IN ('starting','attached','running','active','idle')
                    """,
                    (terminal_id, req.agentId),
                )
            ).fetchone()
        if console_terminal:
            existing_mode = _normalize_session_mode((row["session_mode"] if row else "") or "managed")
            existing_state = _json_loads_or((row["runtime_state"] if row else "") or "{}", {})
            existing_capabilities = (row["capabilities"] if row and "capabilities" in row.keys() else "") or json.dumps(capabilities or [])
            existing_runtime_config = (row["runtime_config"] if row and "runtime_config" in row.keys() else "") or json.dumps(runtime_config)
            next_state = _runtime_state_with_handle(normalized_runtime, existing_state, session_handle)
            next_state["consoleTerminal"] = {
                "terminalId": terminal_id,
                "bridgeId": bridge_id,
                "sessionHandle": session_handle,
                "at": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET role = ?,
                    name = ?,
                    cwd = ?,
                    runtime = ?,
                    machine_id = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    capabilities = ?,
                    runtime_config = ?,
                    runtime_state = ?,
                    status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    req.role,
                    req.name or req.agentId,
                    resolved_cwd,
                    normalized_runtime,
                    req.machineId or "",
                    session_handle,
                    session_handle,
                    existing_capabilities,
                    existing_runtime_config,
                    json.dumps(next_state),
                    "Dashboard Console PTY attached.",
                    now,
                    req.agentId,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'console',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    -- A live console PTY attaching IS the authoritative "backing (re)started"
                    -- event: promote a dead-state denorm back to running, else the session row
                    -- stays 'stopped' from the PREVIOUS backing's death and the Console label
                    -- reads "Console stopped" for a live attached terminal forever (cms-manager,
                    -- 2026-06-10 — the display deriver deliberately never promotes, so the bind
                    -- moment must). Operator disable is enforced on agents.status, not here.
                    status = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                  THEN 'running' ELSE status END,
                    ended_at = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                    THEN NULL ELSE ended_at END,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    console_terminal["bridge_id"] or "",
                    terminal_id,
                    console_terminal["status"] or "attached",
                    session_handle,
                    session_handle,
                    now,
                    console_terminal["session_id"],
                ),
            )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="managed",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": existing_mode,
                    "ownershipTransition": "console_terminal_attached",
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": str(req.status or "idle").strip().lower(),
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": existing_mode,
                "ownershipTransition": "console_terminal_attached",
            }
        fresh_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
        if bridge_id:
            fresh_state["bridgeInstanceId"] = bridge_id
        if normalized_session_mode == "resident":
            fresh_state["ownership"] = {
                "mode": "resident",
                "previousMode": _normalize_session_mode(row["session_mode"] or "managed") if row else "",
                "reason": "registered_cli",
                "at": now,
            }
        elif normalized_session_mode == "managed" and _normalize_launch_mode(req.launchMode) == "managed":
            fresh_state["ownership"] = {
                "mode": "managed",
                "previousMode": _normalize_session_mode(row["session_mode"] or "resident") if row else "",
                "reason": "registered_managed",
                "at": now,
            }
        # Plan 2 (2026-05-25) pi flip mechanics: pi-runtime no longer
        # supports a true resident session, but operators may still try
        # to register one (e.g. via legacy wrapper). Mark it pending-flip
        # so _drain_and_flip_pi_resident_agents (Task 17) can migrate it
        # to managed once any active runs drain. Once flipped, the agent
        # row's session_mode becomes "managed" and capabilities are
        # recomputed from PiAdapter (supports_resident=False).
        if normalized_runtime == "pi" and normalized_session_mode == "resident":
            fresh_state["pi_resident_pending_flip"] = True
        existing_state = json.dumps(fresh_state)
        if row and normalized_session_mode == "resident" and _normalize_session_mode(row["session_mode"] or "resident") == "managed":
            active_run = await _get_blocking_active_run(db, req.agentId)
            existing_state_dict = _json_loads_or(row["runtime_state"], {})
            existing_state_dict.pop("pendingResidentTakeover", None)
            existing_state_dict["manualResidentCandidate"] = {
                "bridgeId": bridge_id,
                "machineId": req.machineId or "",
                "runtime": normalized_runtime,
                "sessionHandle": session_handle,
                "runtimeConfig": runtime_config,
                "capabilities": capabilities or [],
                "cwd": resolved_cwd,
                "launchMode": _normalize_launch_mode(req.launchMode),
                "registeredAt": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET runtime_state = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_state_dict),
                    (
                        f"Resident CLI registered, but agent remains managed. Use Switch to resident when ready."
                        + (f" Active run {active_run.get('runId') or ''} is still running." if active_run else "")
                    ),
                    now,
                    req.agentId,
                ),
            )
            if session_handle:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET session_handle = ?,
                        telemetry = CASE
                            WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                            ELSE telemetry
                        END,
                        last_seen = ?
                    WHERE id = (
                        SELECT id
                        FROM agent_sessions
                        WHERE agent_id = ?
                          AND runtime = ?
                          AND status = 'cli-takeover'
                        ORDER BY last_seen DESC
                        LIMIT 1
                    )
                    """,
                    (
                        session_handle,
                        json.dumps({"registeredHandle": _runtime_state_with_handle(normalized_runtime, {}, session_handle)}),
                        now,
                        req.agentId,
                        normalized_runtime,
                    ),
                )
            if bridge_id:
                await _record_bridge_registration(
                    db,
                    bridge_id=bridge_id,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=normalized_runtime,
                    session_mode="resident",
                    session_handle=session_handle,
                    terminal_id=terminal_id,
                    now=now,
                )
            await _invalidate_agent_live_state(db, req.agentId)
            await db.commit()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_registered", {
                    "agentId": req.agentId,
                    "role": req.role,
                    "runtime": normalized_runtime,
                    "machineId": req.machineId or "",
                    "sessionMode": "managed",
                    "residentBridgeId": bridge_id,
                })
            return {
                "ok": True,
                "agentId": req.agentId,
                "role": req.role,
                "status": row["status"] or "active",
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "bridgeId": bridge_id,
                "sessionMode": "managed",
                "ownershipTransition": "manual_switch_required",
                # Task 4.1: the takeover command the operator runs after flipping
                # the agent to resident in the dashboard (one-driver invariant).
                "resumeCommand": _resume_command_for(normalized_runtime, session_handle, req.agentId),
                "blockedByRun": active_run,
            }
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, driver_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                name = excluded.name,
                cwd = excluded.cwd,
                model = excluded.model,
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                status_note = excluded.status_note,
                runtime = excluded.runtime,
                machine_id = excluded.machine_id,
                launch_mode = excluded.launch_mode,
                session_mode = excluded.session_mode,
                session_handle = excluded.session_handle,
                managed_by = excluded.managed_by,
                capabilities = excluded.capabilities,
                runtime_config = excluded.runtime_config,
                runtime_state = excluded.runtime_state,
                driver_state = excluded.driver_state,
                last_seen = excluded.last_seen
            """,
            (
                req.agentId, req.role, req.name or req.agentId, resolved_cwd, model_value,
                # Folded for the same reason as `launch_mode` two lines down: `agents.status`
                # is compared against lowercase literals by readers that do not all fold.
                description_value, req.instructions or "", str(req.status or "idle").strip().lower(),
                (row["status_note"] if row and "status_note" in row.keys() else "") or "",
                normalized_runtime,
                req.machineId or "", _normalize_launch_mode(req.launchMode),
                normalized_session_mode, session_handle, req.managedBy or "",
                json.dumps(capabilities or []), json.dumps(runtime_config),
                existing_state,
                # One-driver FSM: an attaching process carrying a bridge_id is a
                # live driver for this session -> mark driving. A metadata-only
                # (re)register without a bridge keeps the prior driver_state.
                ("driving" if bridge_id else (str((row["driver_state"] if row and "driver_state" in row.keys() else "") or "idle"))),
                row["registered_at"] if row and row["registered_at"] else now, now
            )
        )
        if session_handle:
            app_server_url = ""
            if isinstance(runtime_config, dict):
                app_server_url = str(runtime_config.get("appServerUrl") or "").strip()
            session_runtime_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    app_server_url = CASE WHEN ? != '' THEN ? ELSE app_server_url END,
                    last_seen = ?,
                    capabilities = CASE
                        WHEN COALESCE(NULLIF(capabilities, ''), '{}') = '{}' THEN ?
                        ELSE capabilities
                    END,
                    telemetry = CASE
                        WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                        ELSE telemetry
                    END
                WHERE id = (
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                )
                """,
                (
                    session_handle,
                    app_server_url,
                    app_server_url,
                    now,
                    json.dumps({"persistent": True, "nativeResume": True, "bridgeResume": True, "cliAttach": True}),
                    json.dumps({"registeredHandle": session_runtime_state}),
                    req.agentId,
                    normalized_runtime,
                ),
            )
        if bridge_id:
            await _record_bridge_registration(
                db,
                bridge_id=bridge_id,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                runtime=normalized_runtime,
                session_mode=normalized_session_mode,
                session_handle=session_handle,
                terminal_id=terminal_id,
                managed_wrapper_child=managed_wrapper_child,
                now=now,
            )
        await _invalidate_agent_live_state(db, req.agentId)
        # Universal rule: when a *-aify wrapper registers an agent as
        # resident, the operator's real terminal owns it. ANY managed
        # wrapper PTY that exists for this agent must be torn down at
        # that moment — no time-based detection, just the resident-
        # register event itself triggers it. Mark active terminal_sessions
        # as stopped with a clear reason; clear the agent_session
        # terminal_id binding so the dashboard stops displaying a ghost
        # console; send a 'stop' terminal_control to the owning bridge
        # so the underlying PTY process is killed if still alive.
        if normalized_session_mode == "resident":
            stale_terminals = await (
                await db.execute(
                    """
                    SELECT id, environment_id, bridge_id
                    FROM terminal_sessions
                    WHERE agent_id = ?
                      AND status IN ('starting','attached','running','active','idle','recovering')
                      AND (? = '' OR id != ?)
                    """,
                    (req.agentId, terminal_id, terminal_id),
                )
            ).fetchall()
            for term in stale_terminals:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        error = COALESCE(NULLIF(error, ''), 'superseded_by_resident_takeover')
                    WHERE id = ?
                    """,
                    (now, now, term["id"]),
                )
                await _append_terminal_event(
                    db,
                    term["id"],
                    "superseded_by_resident_takeover",
                    json.dumps({
                        "agentId": req.agentId,
                        "residentBridge": bridge_id,
                        "newSessionMode": "resident",
                    }),
                )
                # Best-effort kill: enqueue 'stop' so the owning bridge
                # tears down the wrapper subprocess if still alive. If
                # the bridge is dead, the row is already marked stopped
                # so it doesn't matter that the control is never claimed.
                await _append_terminal_control(
                    db,
                    terminal_id=term["id"],
                    environment_id=term["environment_id"] or "",
                    bridge_id=term["bridge_id"] or "",
                    action="stop",
                    requested_by="resident-takeover",
                    body="",
                )
            if stale_terminals:
                # Clear agent_sessions.terminal_id binding for sessions
                # that pointed at any of the just-stopped terminals so
                # the dashboard stops rendering a ghost Console.
                stopped_ids = [t["id"] for t in stale_terminals]
                placeholders = ",".join(["?"] * len(stopped_ids))
                await db.execute(
                    f"""
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = ''
                    WHERE agent_id = ?
                      AND terminal_id IN ({placeholders})
                    """,
                    (req.agentId, *stopped_ids),
                )
            await _upsert_resident_agent_session(
                db,
                agent_id=req.agentId,
                runtime=normalized_runtime,
                workspace=resolved_cwd,
                machine_id=req.machineId or "",
                session_handle=session_handle,
                runtime_config=runtime_config,
                bridge_id=bridge_id,
                capabilities=capabilities or [],
                now=now,
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_registered", {
                "agentId": req.agentId,
                "role": req.role,
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "sessionMode": normalized_session_mode,
            })
        return {
            "ok": True,
            "agentId": req.agentId,
            "role": req.role,
            "status": str(req.status or "idle").strip().lower(),
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "bridgeId": bridge_id,
            "sessionMode": normalized_session_mode,
        }
    finally:
        await db.close()
