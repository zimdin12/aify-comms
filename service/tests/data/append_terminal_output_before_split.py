"""The pre-split `append_terminal_output`, frozen.

Not imported by anything. It is the ONE true original that
`test_append_terminal_output_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/terminals.py` at the commit before the first extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def append_terminal_output(terminal_id: str, req: TerminalOutputRequest, request: Request):
    db = await get_db()
    try:
        # Deliberately omit the (up to 64KB) `output` blob: this is the
        # high-frequency ingest path and never needs the existing buffer. The
        # queue flush re-reads only what it concatenates.
        terminal = await (await db.execute(
            """
            SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                   workspace, command, output_seq, status, requested_by,
                   created_at, updated_at, stopped_at, error
            FROM terminal_sessions WHERE id = ?
            """,
            (terminal_id,),
        )).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        # Bridge-ownership check: for REAL PTY terminals (a node-pty process
        # spawned by one bridge), a mismatched bridge_id MUST 409 — only the
        # owning bridge can write to its PTY. But synthesized virtual rpc
        # terminals (pi/hermes/codex/opencode) are just frame buffers with no
        # underlying owned process; sequential bridges that take over an
        # agent (e.g., aify-comms restarted between dispatches) need to
        # write to the SAME terminal_session row so the operator's Console
        # view stays continuous. Operator-reported 2026-05-22:
        # graph-tester-pi's synth terminal stopped updating at the
        # timestamp of the bridge that originally created it — every later
        # dispatch was rejected with 409.
        new_bridge_id = str(req.bridgeId or "").strip()
        existing_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_command = str(terminal["command"] or "")
        is_virtual_rpc = terminal_command in VIRTUAL_RPC_COMMAND_SET
        if new_bridge_id and existing_bridge_id and new_bridge_id != existing_bridge_id:
            if is_virtual_rpc:
                # Transfer ownership of the synth terminal to the new bridge.
                # Audit so operators see the takeover in the event log.
                #
                # Revive if previously stopped — the bridge-supersession
                # cleanup (`_stop_virtual_terminals_for_superseded_bridges`)
                # can race against an in-flight dispatch on the new bridge:
                # supersession stops the row, then the new bridge's
                # /output POST arrives. Operator-reported 2026-05-22:
                # codex synth terminal showed "started then stopped" yet
                # the agent still replied — frames were accumulating
                # in terminal_events while the row was stale-stopped,
                # leaving the dashboard rendering "terminal is not
                # running" despite a healthy stream of frames. The
                # arriving POST is hard proof the new bridge is
                # actively writing, so undo the stale stop.
                current_status = str(terminal["status"] or "").strip().lower()
                if current_status == "stopped":
                    await db.execute(
                        """
                        UPDATE terminal_sessions
                        SET bridge_id = ?, status = 'running', stopped_at = NULL, error = ''
                        WHERE id = ?
                        """,
                        (new_bridge_id, terminal_id),
                    )
                else:
                    await db.execute(
                        "UPDATE terminal_sessions SET bridge_id = ? WHERE id = ?",
                        (new_bridge_id, terminal_id),
                    )
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "virtual_rpc_bridge_takeover",
                    json.dumps({
                        "from": existing_bridge_id,
                        "to": new_bridge_id,
                        "revived": current_status == "stopped",
                    }),
                )
                # Commit immediately — the endpoint's only other commit
                # is inside the _TERMINAL_END_STATUSES branch, which
                # doesn't fire for normal "running" output POSTs. Without
                # this, the bridge_id transfer + revive would silently
                # be lost on the next connection (failing the takeover
                # contract for any subsequent reader).
                await db.commit()
            else:
                raise HTTPException(409, "Terminal is owned by a different bridge")
        status = str(req.status or "").strip()
        next_seq = await TERMINAL_OUTPUT_WRITES.enqueue(
            terminal_id,
            req.output or "",
            status=status,
            base_seq=int(terminal["output_seq"] or 0),
            autoschedule=not bool(getattr(request.app.state, "testing", False)),
        )
        if status in _TERMINAL_END_STATUSES:
            now = _now()
            summary = f"Terminal {status} before an explicit reply was recorded."
            await _close_active_terminal_runs_for_terminal(db, terminal, status, now=now, reason=summary)
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?,
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?)
                WHERE id = ?
                """,
                (status, now, now, terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = 'managed',
                    last_seen = ?
                WHERE id = ?
                """,
                (status, now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            await db.commit()
        # Do NOT broadcast per-POST here: concurrent POSTs reorder vs seq and
        # the dashboard's seq-dedupe then drops frames (scrambled console).
        # Hand the ws manager to the write queue, which emits one ordered,
        # coalesced, post-commit broadcast per flush instead.
        ws = await _get_ws(request)
        if ws is not None:
            TERMINAL_OUTPUT_WRITES.ws_manager = ws
        # Ingest ack only — the response intentionally carries no output buffer
        # (clients read full output via GET /terminals/{id}). The sole caller
        # is the bridge, which uses outputSeq/status and ignores the rest.
        terminal_payload = _terminal_session_to_dict(terminal)
        terminal_payload["outputSeq"] = next_seq
        if status:
            terminal_payload["status"] = status
        return {"ok": True, "terminal": terminal_payload}
    finally:
        await db.close()
