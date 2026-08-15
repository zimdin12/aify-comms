"""The pre-split `_close_idle_virtual_rpc_workers`, frozen.

Not imported by anything. It is the ONE true original that
`test_close_idle_virtual_rpc_workers_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/reconcilers/terminals.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def _close_idle_virtual_rpc_workers(db, *, idle_close_enabled: bool, idle_close_minutes: int, limit: int = 200) -> list[dict[str, str]]:
    """Auto-close managed worker terminals idle longer than configured."""
    # SEAM NORMALIZATION, v0.5 slice 5 (declared). Two keys, supplied by the caller from its pass
    # settings as required scalars — same keys, same defaults, same use. Narrow scalars rather than
    # the whole dict, which is the shape the reviewer preferred in slice 1a.
    minutes = int(idle_close_minutes or 0)
    if minutes <= 0 or not bool(idle_close_enabled):
        return []
    cursor = await db.execute(
        f"""
        SELECT
          t.id,
          t.agent_id,
          t.command,
          t.environment_id,
          t.bridge_id,
          s.id AS agent_session_id
        FROM terminal_sessions t
        LEFT JOIN agent_sessions s ON s.id = t.session_id
        LEFT JOIN agents a ON a.id = t.agent_id
        WHERE t.status IN ('starting', 'attached', 'running', 'recovering', 'active', 'idle')
          AND (
            t.command IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
            OR t.command LIKE '%-aify%'
            OR t.command LIKE 'opencode%'
          )
          AND (
            COALESCE(a.session_mode, '') = 'managed'
            OR COALESCE(s.owner_mode, '') = 'managed'
            OR COALESCE(s.mode, '') LIKE 'managed%'
          )
          AND datetime(t.updated_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_runs r
            WHERE r.target_agent = t.agent_id
              AND (
                r.status IN ('queued', 'claimed', 'running')
                OR (r.status = 'delivered' AND COALESCE(r.require_reply, 0) = 1)
              )
          )
        ORDER BY t.updated_at ASC
        LIMIT ?
        """,
        (*VIRTUAL_RPC_COMMAND_SET, f"-{minutes} minutes", limit),
    )
    rows = await cursor.fetchall()
    now = _now()
    closed: list[dict[str, str]] = []
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        command = str(row["command"] or "").strip()
        if not terminal_id:
            continue
        is_virtual_rpc = command in VIRTUAL_RPC_COMMAND_SET
        has_bridge_owner = bool(str(row["environment_id"] or "").strip() and str(row["bridge_id"] or "").strip())
        next_status = "stopped" if is_virtual_rpc or not has_bridge_owner else "stopping"
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = ?,
                stopped_at = CASE WHEN ? = 'stopped' THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (
                next_status,
                next_status,
                now,
                now,
                f"Auto-closed: idle longer than worker_idle_close_minutes={minutes}.",
                terminal_id,
            ),
        )
        if not is_virtual_rpc and has_bridge_owner:
            await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=str(row["environment_id"] or "").strip(),
                bridge_id=str(row["bridge_id"] or "").strip(),
                action="stop",
                requested_by="auto-close-idle-worker",
            )
        await _append_terminal_event(
            db,
            terminal_id,
            "managed_worker_auto_closed_idle",
            json.dumps({"agentId": owner_agent, "idleMinutes": minutes, "status": next_status}),
        )
        session_id = str(row["agent_session_id"] or "").strip()
        if session_id:
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (next_status, now, session_id),
            )
        if owner_agent:
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                changed = False
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    changed = True
                if str(rs.get("terminalId") or "").strip() == terminal_id:
                    rs.pop("terminalId", None)
                    changed = True
                if changed:
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)
        closed.append({"terminalId": terminal_id, "agentId": owner_agent})
    return closed
