"""The pre-split `resident_lost`, frozen.

Not imported by anything. It is the ONE true original that
`test_resident_lost_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/session_ops.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def resident_lost(agent_id: str, req: AgentResidentLostRequest, request: Request):
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        runtime_state = _json_loads_or(row["runtime_state"], {})
        current_bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
        bridge_id = str(req.bridgeId or "").strip()
        if bridge_id and current_bridge_id and bridge_id != current_bridge_id:
            return {
                "ok": True,
                "ignored": True,
                "reason": "bridge_not_current",
                "agentId": agent_id,
                "currentBridgeId": current_bridge_id,
                "bridgeId": bridge_id,
            }

        if bridge_id:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = CASE WHEN COALESCE(superseded_by, '') = '' THEN 'resident-lost' ELSE superseded_by END,
                    superseded_at = COALESCE(superseded_at, ?)
                WHERE id = ? AND agent_id = ?
                """,
                (now, bridge_id, agent_id),
            )

        settings = await _load_settings(db)
        returned, transition = await _auto_return_resident_to_managed_if_possible(
            db,
            row,
            settings=settings,
            force=True,
            reason="resident_runtime_lost",
        )

        if not transition:
            # A session_mode='managed' agent reaching here is NOT a resident that lost
            # its runtime — it's a MANAGED worker whose backing died (the hermes
            # managed-host reuses this signal via reportGatewayDead when its gateway
            # port goes dead). The server can re-spawn a managed worker on the next
            # message, so it must rest at a COLD-STARTABLE state, not 'stopped'.
            #
            # The old code stopped it (status='stopped', launch_mode='none'), which the
            # send-gate rejects outright ("agent status is stopped") — so a dead-gateway
            # hermes could NEVER wake; every send bounced and the only recovery was a
            # manual hermes-aify restart (operator-reported: whole hermes team stuck
            # 'stopped', 2026-07-06/07). Wake test proved status='stopped' hard-blocks
            # delivery (dispatchRuns:[], reason "agent status is stopped").
            #
            # Fix: for a managed agent, mirror an idle-available managed worker
            # (stored status='active' → _compute_agent_status derives 'available' with
            # no live worker; launch_mode='detached') so the next send cold-starts a
            # fresh session (new gateway). The bound env still gates via the send
            # preflight, so an offline env yields a clean "env unavailable" wait rather
            # than a permanent stop. Resident agents keep the stop fallback (a resident
            # that lost its runtime with no managed backing is correctly stopped).
            agent_is_managed = str(row["session_mode"] or "").strip().lower() == "managed"
            if agent_is_managed:
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'active',
                        status_note = ?,
                        launch_mode = 'detached',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        (
                            "Managed worker backing ended ("
                            + str(req.reason or "runtime/gateway lost").strip()[:200]
                            + "); will cold-start a fresh session on the next message."
                        )[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "managed_worker_lost_available"
            else:
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
                        str(req.reason or "Resident runtime bridge was lost and no managed backing was available.")[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "resident_to_stopped"

        await db.commit()
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        status = await _compute_agent_status(returned, db)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_resident_lost", {"agentId": agent_id, "transition": transition})
        return {
            "ok": True,
            "agentId": agent_id,
            "transition": transition,
            "agent": _agent_record_to_dict(returned, status, 0, dispatch_state),
        }
    finally:
        await db.close()
