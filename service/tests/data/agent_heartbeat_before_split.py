"""The pre-split `agent_heartbeat`, frozen.

Not imported by anything. It is the ONE true original that
`test_agent_heartbeat_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/liveness.py` at the commit before the first
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def agent_heartbeat(agent_id: str, request: Request):
    """Lightweight heartbeat — bridge poll loop calls this to signal liveness."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    terminal_id = str(body.get("terminalId", "") or "").strip()
    bridge_kind = str(body.get("bridgeKind", "") or "").strip().lower()
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        # Mode FSM release signal (Task 4.1, 2026-05-30). Symmetric with the
        # claim path: a DISPLACED managed sidecar (bridgeKind="channel-sidecar")
        # pulsing turn_busy via heartbeat is told to RELEASE once the agent has
        # been switched to resident, so it stops driving even between claims.
        # driver_state guard (2026-05-31, sc-manager): see the claim-path comment.
        # A live resident driver (driver_state='driving') keeps its own delivery
        # sidecar; only a displaced managed driver (not 'driving') is released.
        if bridge_kind == "channel-sidecar":
            mode_row = await (await db.execute(
                "SELECT session_mode, driver_state FROM agents WHERE id = ?",
                (agent_id,),
            )).fetchone()
            if (
                mode_row
                and _normalize_session_mode(mode_row["session_mode"] or "resident") != "managed"
                and str((mode_row["driver_state"] if "driver_state" in mode_row.keys() else "") or "").strip().lower() != "driving"
            ):
                # Live resident bridge ⇒ this is the resident's OWN delivery sidecar,
                # not a displaced managed driver — adopt driving instead of releasing
                # (see _adopt_live_resident_driver).
                if await _adopt_live_resident_driver(db, agent_id):
                    await db.commit()
                else:
                    return {"ok": True, "release": True}
        if bridge_id:
            bridge_row = await (await db.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (bridge_id, agent_id),
            )).fetchone()
            if bridge_row and str(bridge_row["superseded_by"] or "").strip():
                return {
                    "ok": False,
                    "ignored": True,
                    "reason": "bridge_superseded",
                    "supersededBy": str(bridge_row["superseded_by"] or "").strip(),
                }
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
            (now, agent_id),
        )
        if bridge_id:
            if terminal_id:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, terminal_id = ? WHERE id = ? AND agent_id = ?",
                    (now, terminal_id, bridge_id, agent_id),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (now, bridge_id, agent_id),
                )
        # Unconditional liveness beat (Workstream A, 2026-06-01). A long-lived
        # bridge posts {bridgeId, bridgeKind, liveness:true} on a fixed interval
        # regardless of turn activity, so last_seen is a true "alive now" signal.
        # Unlike the plain UPDATE above (which no-ops when the bridge has no row
        # yet — e.g. an idle channel-sidecar that never claimed), this UPSERTS the
        # row, refreshing its current agent identity as well as last_seen +
        # bridge_kind. It never clears superseded_by and never touches turn
        # state. (A superseded existing row is already short-circuited by the
        # guard above.)
        if body.get("liveness") and bridge_id:
            arow = await (await db.execute(
                "SELECT machine_id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,),
            )).fetchone()
            arow_machine = (arow["machine_id"] if arow else "") or ""
            arow_runtime = (arow["runtime"] if arow else "") or "generic"
            if bridge_kind == "channel-sidecar":
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=bridge_id,
                    agent_id=agent_id,
                    machine_id=arow_machine,
                    runtime=arow_runtime,
                    session_mode=(arow["session_mode"] if arow else "") or "managed",
                    now=now,
                )
            else:
                # FIX SET B3 (2026-06-03): the 30s liveness beat from the host-side
                # bridge (server.js) posts bridgeKind="resident", but the SAME agent
                # may have a wrapper-child / channel-sidecar bridge row that registered
                # the authoritative managed kind. A plain COALESCE(NULLIF(?,''),...)
                # let that generic "resident" beat DEMOTE a 'managed-wrapper-child'
                # (or 'channel-sidecar') back to 'resident' — after which
                # _has_live_managed_wrapper_child / _has_live_channel_sidecar stop
                # matching and the managed agent loses its claimer (the lc-coder /
                # codex-managed strand). Guard: an incoming '' or 'resident' can NEVER
                # overwrite an existing 'managed-wrapper-child' or 'channel-sidecar';
                # any other incoming kind still COALESCE-wins as before.
                updated = await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, "
                    "bridge_kind = CASE "
                    "WHEN COALESCE(bridge_kind, '') IN ('managed-wrapper-child', 'channel-sidecar') "
                    "AND COALESCE(?, '') IN ('', 'resident') THEN bridge_kind "
                    "ELSE COALESCE(NULLIF(?, ''), bridge_kind) END "
                    "WHERE id = ? AND agent_id = ?",
                    (now, bridge_kind, bridge_kind, bridge_id, agent_id),
                )
                if not getattr(updated, "rowcount", 0):
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO bridge_instances (
                            id, agent_id, machine_id, runtime, session_mode,
                            session_handle, terminal_id, bridge_kind,
                            registered_at, last_seen, superseded_by, superseded_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (bridge_id, agent_id,
                         _normalize_machine_id(arow_machine),
                         arow_runtime,
                         "managed", "", "", bridge_kind or "resident",
                         now, now, "", None),
                    )
                    await db.execute(
                        "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                        (now, bridge_id, agent_id),
                    )
        # Liveness recovery (audit 2026-06-28): a plain liveness beat (no turnBusy) doesn't flip
        # turn state, but it DOES prove the bridge is alive again. If the agent was cached
        # `offline`, drop that entry so the next read recomputes to available/online instead of
        # serving offline for the full ~180s horizon (the documented "recovery on any real event
        # is immediate" contract was violated — invalidation only ran on the turnBusy path).
        # Surgical: only the offline-cached case, so normal online agents keep their warm cache.
        if body.get("liveness"):
            _cached_live = _live_state_get(agent_id)
            if _cached_live and _cached_live.get("status") == "offline":
                await _invalidate_agent_live_state(db, agent_id)

        # Authoritative turn-busy signal (contract with the bridge). Missing
        # "turnBusy" → liveness only (old-bridge safe). turnBusy=true: latest
        # bridge wins. turnBusy=false: only the owning bridge+run may clear,
        # so a stale false from a superseded bridge/run cannot wipe a newer
        # active turn.
        turn_flip = False  # WS-1: did this heartbeat actually change turn_busy (working⇄ready)?
        if "turnBusy" in body:
            turn_busy = bool(body.get("turnBusy"))
            turn_run_id = str(body.get("turnRunId", "") or "").strip()
            turn_runtime = str(body.get("turnRuntime", "") or "").strip()
            _prev_row = await (await db.execute(
                "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,))).fetchone()
            _prev_busy = bool(_prev_row and _prev_row["turn_busy"])
            if turn_busy:
                await db.execute(
                    """
                    INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                    VALUES (?, 1, ?, ?, ?, ?)
                    ON CONFLICT(agent_id) DO UPDATE SET
                        turn_busy = 1,
                        turn_run_id = excluded.turn_run_id,
                        turn_bridge_id = excluded.turn_bridge_id,
                        turn_runtime = excluded.turn_runtime,
                        turn_updated_at = excluded.turn_updated_at
                    """,
                    (agent_id, turn_run_id, bridge_id, turn_runtime, now),
                )
                turn_flip = not _prev_busy  # to-working transition
                # status v2 (Fix A, 2026-06-05): the /heartbeat turnBusy field is the
                # DOMINANT turn signal for MANAGED runtimes (hermes/codex/pi/opencode)
                # and claude channel-woken turns — the dispatch lifecycle pulses it,
                # but it only ever wrote agent_turn_state (OLD engine) and never fed
                # agent_status_state, so the `new` engine showed online/idle mid-turn.
                # Feed turn_start here too. Flag-agnostic at the write layer (only the
                # `new` read path consumes agent_status_state, so it is a no-op for
                # `old`); idempotent with any resident turn-start hook (turn_start just
                # sets in_turn=1). Mirrors the /turn-start endpoint's same pattern.
                await _apply_status_event(db, agent_id, {"kind": "turn_start", "runId": turn_run_id})
            else:
                cur = await (await db.execute(
                    "SELECT turn_bridge_id, turn_run_id FROM agent_turn_state WHERE agent_id = ?",
                    (agent_id,),
                )).fetchone()
                if cur:
                    stored_bridge = str(cur["turn_bridge_id"] or "").strip()
                    stored_run = str(cur["turn_run_id"] or "").strip()
                    if stored_bridge == bridge_id and (not stored_run or stored_run == turn_run_id):
                        await db.execute(
                            "UPDATE agent_turn_state SET turn_busy = 0, turn_updated_at = ? WHERE agent_id = ?",
                            (now, agent_id),
                        )
                        # status v2 (Fix A): clear in_turn ONLY inside the SAME
                        # ownership guard that gates the turn_busy=0 write, so a
                        # stale/superseded bridge or a non-owning run can never wipe
                        # a live turn's in_turn. Mirrors exactly the guard the
                        # turn_busy=0 write uses — never clears where the old code
                        # would not clear turn_busy.
                        await _apply_status_event(db, agent_id, {"kind": "turn_end", "runId": ""})
                        turn_flip = _prev_busy  # to-ready transition (only when we actually cleared)
            # A turn_busy flip changes derived status (working ⇄ idle). Invalidate
            # the live-state cache so the next read recomputes immediately, instead
            # of lagging up to the 60s reconcile sweep. Symmetric with the dedicated
            # /turn-start and /turn-end endpoints, which already invalidate.
            await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # WS-1 (2026-06-17): the /heartbeat turnBusy field is the DOMINANT turn signal for
        # managed runtimes, but it only invalidated the cache — the dashboard still waited its
        # ~60s poll to see the flip. Push it immediately, but ONLY on an actual working⇄ready
        # flip (not every 3s liveness/refresh beat), flag-gated to keep `old` unchanged.
        if turn_flip:
            settings = await _load_settings(db)
            await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True}
    finally:
        await db.close()
