async def stop_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (terminal["environment_id"],))).fetchone()
        settings = await _load_settings(db)
        env_status = _environment_effective_status(
            env_row,
            offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
        ) if env_row else "offline"
        current_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
        terminal_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_status = str(terminal["status"] or "").strip().lower()
        bridge_can_claim = bool(
            terminal_bridge_id
            and current_bridge_id
            and terminal_bridge_id == current_bridge_id
            and env_status in {"online", "degraded"}
        )
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_stop_requested",
            json.dumps({"requestedBy": requested_by, "body": req.body or "", "controlId": control_id}),
        )
        if terminal_status in {"stopped", "failed"} or not bridge_can_claim:
            reason = "Terminal bridge is no longer current; stop reconciled in control plane."
            await db.execute(
                """
                UPDATE terminal_controls
                SET status = 'completed',
                    claimed_at = COALESCE(claimed_at, ?),
                    handled_at = ?
                WHERE id = ?
                """,
                (now, now, control_id),
            )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = 'stopped',
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?),
                    error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
                WHERE id = ?
                """,
                (now, now, reason if terminal_status not in {"stopped", "failed"} else "", terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    terminal_status = 'stopped',
                    last_seen = ?
                WHERE id = ?
                """,
                (now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            # _clear_console_terminal_binding only invalidates when the agent's
            # consoleTerminal pointer matches (no-ops for virtual/RPC terminals,
            # whose pointer is virtualTerminalId). Invalidate explicitly here —
            # mirroring the sibling bridge-reported completion path — so the
            # reconciled stop drops the agent out of `online`/`working`
            # immediately rather than lying until the 60s sweep.
            await _invalidate_agent_live_state(db, terminal["agent_id"])
            await _append_terminal_event(
                db,
                terminal_id,
                "console_stop_reconciled",
                json.dumps({
                    "requestedBy": requested_by,
                    "reason": reason,
                    "terminalBridge": terminal_bridge_id,
                    "environmentBridge": current_bridge_id,
                    "environmentStatus": env_status,
                }),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
            return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopping', updated_at = ?
            WHERE id = ?
            """,
            (now, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_status = 'stopping',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
    finally:
        await db.close()
