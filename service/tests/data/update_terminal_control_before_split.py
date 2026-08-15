"""The pre-split `update_terminal_control`, frozen.

Not imported by anything. It is the ONE true original that
`test_update_terminal_control_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/terminals.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def update_terminal_control(control_id: str, req: TerminalControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, f'Unsupported terminal control status "{req.status}"')
    db = await get_db()
    try:
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        if not control:
            raise HTTPException(404, f'Terminal control "{control_id}" not found')
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (control["terminal_id"],))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{control["terminal_id"]}" not found')
        now = _now()
        await db.execute(
            """
            UPDATE terminal_controls
            SET status = ?, handled_at = ?, error = ?
            WHERE id = ?
            """,
            (status, now, req.error or "", control_id),
        )
        # Persist the PTY root pid reported by the owning bridge (start-control
        # attach). Stored so Dashboard Stop/Restart can kill-by-pid even if the
        # owning bridge later dies and the PTY is orphaned. Only set on a real
        # positive value — never blank out an existing pid.
        report_pid = str(req.processId or "").strip()
        if report_pid:
            await db.execute(
                "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
                (report_pid, terminal["id"]),
            )
        terminal_status = str(req.terminalStatus or "").strip()
        if status == "failed":
            terminal_status = terminal_status or "failed"
        if control["action"] == "stop" and status == "completed":
            terminal_status = terminal_status or "stopped"
        if terminal_status:
            terminal_status_norm = terminal_status.strip().lower()
            if terminal_status_norm in _TERMINAL_END_STATUSES:
                await _close_active_terminal_runs_for_terminal(
                    db,
                    terminal,
                    terminal_status_norm,
                    now=now,
                    reason=f"Terminal {terminal_status_norm} before an explicit reply was recorded.",
                )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?, updated_at = ?, stopped_at = CASE WHEN ? IN ('stopped','failed') THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                    error = CASE WHEN ? = 'failed' THEN ? ELSE error END
                WHERE id = ?
                """,
                (terminal_status, now, terminal_status, now, status, req.error or "", terminal["id"]),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = CASE WHEN ? IN ('stopped','failed') THEN 'managed' ELSE owner_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (terminal_status, terminal_status, now, terminal["session_id"]),
            )
        if terminal_status in {"stopped", "failed"}:
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal["id"], now=now)
        if terminal_status.strip().lower() in _TERMINAL_END_STATUSES:
            await _invalidate_agent_live_state(db, terminal["agent_id"])
        # A3 real-cols (2026-07-02): a COMPLETED resize control means the bridge actually
        # applied these dims to the PTY — record them as the terminal's authoritative size.
        # GET /terminals prefers this over the infer_source_width heuristic, so the console
        # snapshot renders at the PTY's true width (kills the live-redraw garble caused by
        # inferred≠actual width).
        if (
            status == "completed"
            and str(control["action"] or "").strip().lower() == "resize"
            and int(control["cols"] or 0) > 0
            and int(control["rows"] or 0) > 0
        ):
            await db.execute(
                "UPDATE terminal_sessions SET cols = ?, rows = ? WHERE id = ?",
                (int(control["cols"]), int(control["rows"]), terminal["id"]),
            )
            _resize_live_terminal_screen(terminal["id"], control["cols"], control["rows"])
        if req.output:
            latest_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
            await _append_terminal_output(db, latest_terminal or terminal, req.output, status=terminal_status)
        await _append_terminal_event(
            db,
            terminal["id"],
            f"terminal_control_{status}",
            json.dumps({"controlId": control_id, "action": control["action"], "error": req.error or ""}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        updated_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_updated", {"terminalId": terminal["id"], "controlId": control_id, "status": status})
        return {"ok": True, "control": _terminal_control_to_dict(updated), "terminal": _terminal_session_to_dict(updated_terminal)}
    finally:
        await db.close()
