"""The pre-split `update_agent_session_handle`, frozen.

Not imported by anything. It is the ONE true original that
`test_update_agent_session_handle_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/session_mode.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def update_agent_session_handle(agent_id: str, req: AgentSessionHandleUpdate, request: Request):
    validate_name(agent_id, "agent ID")
    # Drop unexpanded shell placeholders ("$HERMES_SESSION_ID", "${VAR}") so a
    # literal is never stored as the resume handle — see _sanitize_session_handle.
    session_handle = _sanitize_session_handle(req.sessionHandle)
    if len(session_handle) > 512:
        raise HTTPException(400, "sessionHandle must be 512 characters or fewer")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        # ── Sticky session identity + new-id guard (governance, 2026-05-30) ──
        # The bridge heartbeat (session-handle-heartbeat.js, requestedBy=
        # "bridge-heartbeat") continuously reports the runtime's *discovered*
        # session id. We must NOT silently overwrite the persisted handle when
        # that discovered id DRIFTS from what we already pinned — a drift is the
        # observable symptom of a split (agent landed on a fresh id) or a merge
        # (two agents converging on one id). Instead we park the proposed id in
        # `pending_session_id`, flag the agent `session-changed`, and KEEP
        # delivery pointed at the old handle until the operator resolves it.
        #
        # Scope is deliberately narrow so we never break the existing flows:
        #   • First-id auto-accept — no persisted handle yet → accept (current).
        #   • Same id re-reported → no-op (no pending, no churn).
        #   • Clearing (empty handle) → allowed (heal paths clear poisoned ids).
        #   • Deliberate operator re-pin (any other requestedBy, e.g. dashboard
        #     manual set, console attach) → unguarded, as before.
        #   • Re-register (POST /agents) is a separate write site and remains a
        #     full state refresh — it is NOT routed through here.
        requested_by = str(req.requestedBy or "").strip()
        persisted_handle = str(row["session_handle"] or "").strip()

        # ── Cross-agent collision guard (root-cause fix, 2026-05-31) ──
        # A runtime session id must be owned by at most ONE live agent. Never let
        # agent X ADOPT a session id that a DIFFERENT LIVE agent already owns —
        # the resident<->managed invariant. (Incident: graph-tech-lead adopted
        # comms-tech-lead's live resident id 651b895f at 06:07; the kill-prior
        # reaper then turned that collision fatal.) This fires for ANY source
        # (capture, heartbeat, manual set) and covers the first-id case too. Park
        # the colliding id as `pending_session_id` and KEEP this agent's own
        # handle (empty stays empty → the agent launches fresh and captures its
        # OWN id, which won't collide). A stale/dead owner is NOT a collision
        # (the id is free to reassign) — _session_handle_live_owner gates on
        # heartbeat freshness.
        if session_handle and session_handle != persisted_handle:
            _settings_g = await _load_settings(db)
            _owner = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g.get("resident_lease_seconds", 150),
            )
            if _owner:
                _note = (
                    f"session-collision: reported id '{session_handle}' is already owned by live "
                    f"agent '{_owner['agentId']}' ({_owner['sessionMode']}); kept own handle. "
                    "Two live agents must not share one session id."
                )
                await db.execute(
                    "UPDATE agents SET pending_session_id = ?, status_note = ?, last_seen = ? WHERE id = ?",
                    (session_handle, _note, now, agent_id),
                )
                await db.commit()
                updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                settings = await _load_settings(db)
                status = await _compute_agent_status(updated, db)
                dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
                ws = await _get_ws(request)
                if ws:
                    await ws.broadcast("agent_session_changed", {
                        "agentId": agent_id,
                        "sessionHandle": persisted_handle,
                        "pendingSessionId": session_handle,
                        "collisionWith": _owner["agentId"],
                    })
                return {
                    "ok": True,
                    "agentId": agent_id,
                    "state": "session-collision",
                    "collisionWith": _owner["agentId"],
                    # Delivery keeps targeting THIS agent's own handle; the
                    # colliding id is NOT adopted.
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                    "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
                }

        # Auto-confirm (2026-06-04): when ON (default), a SAFE self-change — the
        # cross-agent collision guard above already returned for a live-owned id —
        # is adopted immediately (fall through to the bind path below) instead of
        # parked. This breaks the managed-claude session-changed → stale-console-
        # owner → recycle loop. When OFF, park as `pending_session_id` and wait for
        # a manual Confirm (the original sticky-identity governance behavior).
        _auto_confirm_sid = bool(
            (await _load_settings(db)).get(
                "auto_confirm_session_id", DEFAULT_SETTINGS["auto_confirm_session_id"]
            )
        )
        # FRESH-START GUARD (2026-06-12, the ci-manager lost-context incident): auto-adopt
        # exists for SAFE self-changes (a compaction/resume issues a new id that CARRIES the
        # context). But when the live terminal started FRESH (its command has no --resume —
        # e.g. the wrapper dropped an unresumable handle after days offline), the reported id
        # is an EMPTY session: adopting it overwrites the pinned handle of the real
        # context-bearing session, and every later Restart then "correctly" resumes the empty
        # one. Park such ids for manual Confirm instead, even when auto-confirm is ON.
        _fresh_start_terminal = False
        if (
            _auto_confirm_sid
            and requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
        ):
            try:
                _lt = await (await db.execute(
                    "SELECT command FROM terminal_sessions WHERE agent_id = ? "
                    "AND status IN ('starting','attached','running','active','idle') "
                    "AND id NOT LIKE 'vterm_%' ORDER BY datetime(COALESCE(updated_at, created_at)) DESC LIMIT 1",
                    (agent_id,),
                )).fetchone()
                if _lt is not None:
                    _fresh_start_terminal = "--resume" not in str(_lt["command"] or "")
            except Exception:
                _fresh_start_terminal = False
        if (
            requested_by == "bridge-heartbeat"
            and session_handle
            and persisted_handle
            and session_handle != persisted_handle
            and (not _auto_confirm_sid or _fresh_start_terminal)
        ):
            await db.execute(
                """
                UPDATE agents
                SET pending_session_id = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    (
                        f"session-changed: reported id '{session_handle}' differs from "
                        f"pinned '{persisted_handle}'. Confirm new or keep current."
                    ),
                    now,
                    agent_id,
                ),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
            settings = await _load_settings(db)
            status = await _compute_agent_status(updated, db)
            dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("agent_session_changed", {
                    "agentId": agent_id,
                    "sessionHandle": persisted_handle,
                    "pendingSessionId": session_handle,
                })
            return {
                "ok": True,
                "agentId": agent_id,
                "state": "session-changed",
                # Delivery still targets the OLD (persisted) handle — unchanged.
                "sessionHandle": persisted_handle,
                "pendingSessionId": session_handle,
                "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
            }

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], session_handle)
        capabilities = _default_capabilities_for(runtime, session_mode, session_handle, runtime_config)
        registered_handle = _runtime_state_with_handle(runtime, {}, session_handle)

        # G3 (2026-06-03): advisory (non-blocking) warning when the handle being
        # bound is already owned by a DIFFERENT live agent. The strict cross-agent
        # collision guard above already HARD-BLOCKS the `handle != persisted` live
        # case; this warning covers the remaining binds (e.g. re-pinning the same
        # handle another live agent already shares) so the operator sees that two
        # live agents are pointing at one native session id.
        handle_share_warning = ""
        if session_handle:
            _settings_g3 = await _load_settings(db)
            _owner_g3 = await _session_handle_live_owner(
                db, session_handle, exclude_agent_id=agent_id,
                lease_seconds=_settings_g3.get("resident_lease_seconds", 150),
            )
            if _owner_g3:
                handle_share_warning = (
                    f"session id '{session_handle}' is also owned by live agent "
                    f"'{_owner_g3['agentId']}' ({_owner_g3['sessionMode']}); two live agents "
                    "should not share one native session."
                )
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                session_handle,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"Session handle set by {req.requestedBy or 'operator'}." if session_handle else f"Session handle cleared by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        latest_session = await (await db.execute(
            """
            SELECT id, capabilities, telemetry
            FROM agent_sessions
            WHERE agent_id = ?
              AND runtime = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id, runtime),
        )).fetchone()
        if latest_session:
            session_telemetry = _json_loads_or(latest_session["telemetry"], {})
            if registered_handle:
                session_telemetry["registeredHandle"] = registered_handle
            else:
                session_telemetry.pop("registeredHandle", None)
            session_capabilities = _session_capabilities_replacing_handle(latest_session["capabilities"], session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    capabilities = ?,
                    telemetry = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    session_handle,
                    json.dumps(session_capabilities),
                    json.dumps(session_telemetry),
                    now,
                    latest_session["id"],
                ),
            )
        await db.commit()

        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": session_handle})
        handle_response = {
            "ok": True,
            "agentId": agent_id,
            "sessionHandle": session_handle,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
        if handle_share_warning:
            handle_response["warning"] = handle_share_warning
        return handle_response
    finally:
        await db.close()
