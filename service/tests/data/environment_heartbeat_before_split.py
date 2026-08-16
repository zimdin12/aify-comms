"""The pre-split `environment_heartbeat`, frozen.

Not imported by anything. It is the ONE true original that
`test_environment_heartbeat_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/environments.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.

EDITED ONCE SINCE CAPTURE, and the edit is the whole reason this note exists. The round trip proves
the split was a pure block-lift OF THE CODE AS IT STANDS, so a later change to a line the split did
not move must be applied here IDENTICALLY or the proof forbids ever editing the function again. The
one change: `if requested_status not in {"online", "degraded", "offline"}:` became
`... not in ENVIRONMENT_REGISTRABLE_STATUSES:` when the environment status vocabulary got an owner in
`service/env_status.py`. Same statement, same position, a named set in place of the literal. Anything
larger than that belongs in a reviewed re-capture, not in a fixture nudge to go green.
"""


async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):
    env_id = str(req.id or "").strip()
    if not env_id:
        raise HTTPException(400, "Environment id is required")

    now = _now()
    cwd_roots = _normalize_roots(req.cwdRoots or [])
    runtimes = req.runtimes or []
    metadata = req.metadata or {}
    if req.terminal is not None:
        metadata["terminal"] = bool(req.terminal)
    if req.pty is not None:
        metadata["pty"] = bool(req.pty)
    if req.terminalRuntimes is not None:
        metadata["terminalRuntimes"] = [
            _normalize_runtime(str(runtime or ""))
            for runtime in req.terminalRuntimes
            if str(runtime or "").strip()
        ]
    requested_status = str(req.status or "online").strip().lower()
    if requested_status not in ENVIRONMENT_REGISTRABLE_STATUSES:
        requested_status = "online"
    db = await get_db()
    try:
        existing_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        existing = await existing_cursor.fetchone()
        # Forget-tombstone guard (2026-06-03): a row in `forgotten` status is the
        # environment-level equivalent of an agent tombstone. A passive heartbeat
        # from a still-running aify-comms bridge that predates the forget MUST NOT
        # resurrect it (the old bug: the blind UPDATE below flipped status back to
        # 'online' seconds after the operator forgot the env). Only a genuine fresh
        # (re)launch — a bridge whose bridgeStartedAt is newer than forgottenAt —
        # is allowed to clear the tombstone and re-register. Mirrors how agent
        # registration honors agent_tombstones unless explicitly restored.
        if existing and str(existing["status"] or "").strip().lower() == "forgotten":
            forgotten_meta = _json_loads_or(existing["metadata"], {})
            forgotten_at = _timestamp_sort_key(forgotten_meta.get("forgottenAt"))
            incoming_started = _bridge_started_at(metadata)
            relaunched = bool(incoming_started) and (not forgotten_at or incoming_started > forgotten_at)
            if not relaunched:
                # Lingering/passive heartbeat — keep the env forgotten, do not touch
                # last_seen or status. Return the tombstoned record as-is.
                return {"ok": True, "environment": _environment_record_to_dict(existing), "forgotten": True}
        registered_at = existing["registered_at"] if existing else now
        existing_metadata = _json_loads_or(existing["metadata"], {}) if existing else {}
        manual_roots = bool(existing_metadata.get("manualRoots"))
        effective_roots = _json_loads_or(existing["cwd_roots"], []) if existing and manual_roots else cwd_roots
        next_metadata = {**metadata, "advertisedCwdRoots": cwd_roots}
        if manual_roots:
            next_metadata.update({
                "manualRoots": True,
                "manualRootsUpdatedAt": existing_metadata.get("manualRootsUpdatedAt", ""),
                "manualRootsUpdatedBy": existing_metadata.get("manualRootsUpdatedBy", ""),
            })
        superseded_bridge_id = ""
        if existing and str(existing["bridge_id"] or "").strip() and str(req.bridgeId or "").strip():
            existing_bridge_id = str(existing["bridge_id"] or "").strip()
            incoming_bridge_id = str(req.bridgeId or "").strip()
            if existing_bridge_id != incoming_bridge_id:
                existing_metadata = _json_loads_or(existing["metadata"], {})
                existing_started = _bridge_started_at(existing_metadata)
                incoming_started = _bridge_started_at(metadata)
                if existing_started and (not incoming_started or incoming_started < existing_started):
                    return {"ok": True, "environment": _environment_record_to_dict(existing)}
                if incoming_started and (not existing_started or incoming_started > existing_started):
                    superseded_bridge_id = existing_bridge_id
        if (
            existing
            and requested_status != "online"
            and str(existing["bridge_id"] or "").strip()
            and str(req.bridgeId or "").strip()
            and str(existing["bridge_id"] or "").strip() != str(req.bridgeId or "").strip()
        ):
            return {"ok": True, "environment": _environment_record_to_dict(existing)}
        if existing:
            await db.execute(
                """
                UPDATE environments
                SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?,
                    bridge_version = ?, cwd_roots = ?, runtimes = ?, status = ?,
                    metadata = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    now,
                    env_id,
                ),
            )
        else:
            await db.execute(
                """
                INSERT INTO environments (
                    id, label, machine_id, os, kind, bridge_id, bridge_version,
                    cwd_roots, runtimes, status, metadata, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    env_id,
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    registered_at,
                    now,
                ),
            )
        if superseded_bridge_id:
            # Bound accumulation: drain superseded-bridge stops for this env that have
            # been pending well past the point a live superseded bridge would have
            # claimed them (it polls every ~3s). Anything still pending after the TTL
            # targets a bridge that never came back; left unbounded these accumulate
            # one-per-restart (99 observed for a single env, 2026-07-03). The claim-side
            # guard already prevents any of them from stopping a live bridge; this just
            # keeps the table from growing without limit.
            drain_cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=SUPERSEDE_STOP_STALE_SECONDS)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            await db.execute(
                """
                UPDATE environment_controls
                SET status = 'failed',
                    handled_at = ?,
                    error = 'stale superseded-bridge stop drained (target bridge never claimed)'
                WHERE environment_id = ?
                  AND action = 'stop'
                  AND status = 'pending'
                  AND requested_by = 'server:superseded-bridge'
                  AND requested_at < ?
                """,
                (now, env_id, drain_cutoff),
            )
            pending_cursor = await db.execute(
                """
                SELECT id
                FROM environment_controls
                WHERE environment_id = ?
                  AND bridge_id = ?
                  AND action = 'stop'
                  AND status IN ('pending', 'claimed')
                LIMIT 1
                """,
                (env_id, superseded_bridge_id),
            )
            pending = await pending_cursor.fetchone()
            if not pending:
                await db.execute(
                    """
                    INSERT INTO environment_controls (
                        id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"envctl-{uuid.uuid4().hex}",
                        env_id,
                        superseded_bridge_id,
                        req.machineId or "",
                        "stop",
                        "pending",
                        "server:superseded-bridge",
                        now,
                    ),
                )
        # Env recovery / status transition: when the env flips between online and
        # offline/degraded, bound agents' derived status (offline ↔ available/online)
        # changes too. Invalidate their live-status cache so the transition shows
        # immediately rather than after the ~90s env window / 60s sweep.
        prior_status = str((existing["status"] if existing else "") or "").strip().lower()
        # Env-down is DERIVED from staleness and never writes environments.status, so on
        # recovery prior_status == requested_status == 'online' and the stored-column
        # check below is skipped — leaving bound agents cached 'offline' up to the ~180s
        # horizon (bughunt 2026-07-03). Also invalidate when the env was EFFECTIVELY
        # offline (stale last_seen) before this heartbeat, regardless of the stored column.
        _env_offline_seconds = max(30, int((await _load_settings(db)).get("environment_offline_seconds", 90) or 90))
        prior_effective = _environment_effective_status(existing, offline_seconds=_env_offline_seconds) if existing else "offline"
        if existing and (prior_status != requested_status or prior_effective == "offline"):
            bound_rows = await (await db.execute(
                "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
                (env_id,),
            )).fetchall()
            for bound in bound_rows:
                bound_agent = str(bound["agent_id"] or "").strip()
                if bound_agent:
                    await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        row_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        row = await row_cursor.fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_heartbeat", {"environmentId": env_id, "bridgeId": req.bridgeId or ""})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()
