"""The pre-split `rename_agent`, frozen.

Not imported by anything. It is the ONE true original that
`test_rename_agent_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/agents/identity.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def rename_agent(agent_id: str, req: AgentRenameRequest, request: Request):
    validate_name(agent_id, "agent ID")
    new_agent_id = str(req.newAgentId or "").strip()
    validate_name(new_agent_id, "new agent ID")
    if new_agent_id == agent_id:
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": False}

    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        existing = await (await db.execute("SELECT id FROM agents WHERE id = ?", (new_agent_id,))).fetchone()
        if existing:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" already exists')
        tombstone = await _agent_tombstone(db, new_agent_id)
        if tombstone:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" was intentionally removed before; clear that ID before reusing it')

        now = _now()
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note,
                runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                capabilities, runtime_config, runtime_state, registered_at, last_seen
            )
            SELECT ?, role, CASE WHEN name = id THEN ? ELSE name END, cwd, model, description,
                   instructions, status, status_note, runtime, machine_id, launch_mode,
                   session_mode, session_handle, managed_by, capabilities, runtime_config,
                   runtime_state, registered_at, ?
            FROM agents
            WHERE id = ?
            """,
            (new_agent_id, new_agent_id, now, agent_id),
        )
        for table, column in (
            ("agent_sessions", "agent_id"),
            ("spawn_specs", "agent_id"),
            ("spawn_requests", "agent_id"),
            ("bridge_instances", "agent_id"),
            ("read_receipts", "agent_id"),
            ("channel_members", "agent_id"),
            ("terminal_sessions", "agent_id"),
        ):
            await db.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET to_agent = ? WHERE to_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE shared_artifacts SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET target_agent = ? WHERE target_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_controls SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE channels SET created_by = ? WHERE created_by = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE agents SET managed_by = ? WHERE managed_by = ?", (new_agent_id, agent_id))
        await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.execute(
            """
            INSERT OR REPLACE INTO agent_tombstones (agent_id, removed_at, removed_by, bridge_id, reason)
            VALUES (?,?,?,?,?)
            """,
            (agent_id, now, req.requestedBy or "dashboard", "", f"renamed_to:{new_agent_id}"),
        )
        await db.commit()
        # Rename is DB-only: a still-running session is bootstrapped under the OLD id (now
        # tombstoned), so it is orphaned — its heartbeats bounce and it does NOT keep the new id
        # live. Surface that + the recovery in the response so the caller/dashboard doesn't have to
        # rediscover it by hand (2026-07-07: a rename silently orphaned the live session and notified
        # nobody). We report facts + a plain note; the dashboard can format the exact relaunch command.
        session_mode = str(agent["session_mode"] or "resident").strip().lower()
        runtime = str(agent["runtime"] or "").strip()
        # "Live" needs a FRESHNESS predicate, not merely a row that was never superseded.
        #
        # This asked `bridge_instances` for any row with an empty `superseded_by`, which is not the
        # same question. Those rows accumulate BY DESIGN (KNOWN_ISSUES.md, 2026-08-07 retraction) and
        # a bridge that died without a clean supersede keeps `superseded_by = ''` until the sweep's
        # `_reap_stale_orphan_bridges` gets to it. So a rename minutes after a crashed wrapper told
        # the operator "A live session is still running as '<old>' and is now orphaned — relaunch it",
        # sending them to recover a session that had already been dead for hours.
        #
        # `_agent_liveness` is the repo's single liveness predicate and already applies the exact
        # leases the status engine uses, so the note now agrees with the dot the operator is looking
        # at. Advisory text only — nothing here changes state either way — but a wrong instruction is
        # the same class of defect as a wrong status, and this file has spent a week on that class.
        liveness = await _agent_liveness(db, new_agent_id)
        had_live_bridge = bool(
            liveness.get("worker_live")
            or liveness.get("sidecar_live")
            or liveness.get("resident_bridge_fresh")
        )
        note = (
            f"History + session handle preserved under '{new_agent_id}'; old id '{agent_id}' is "
            f"tombstoned (sends to it are now rejected). "
            + (
                (
                    f"A live {session_mode} session is still running as '{agent_id}' and is now orphaned — "
                    f"re-register/relaunch it as '{new_agent_id}' "
                    + ("(dashboard Restart, or delete-session then send to cold-start a fresh worker) "
                       if session_mode == "managed"
                       else f"(relaunch the wrapper with the new id, e.g. --aify-agent {new_agent_id}) ")
                    + "so the live identity matches. "
                )
                if had_live_bridge else ""
            )
            + "Notify teammates to address the new id."
        )
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_renamed", {"oldAgentId": agent_id, "newAgentId": new_agent_id})
        return {
            "ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": True,
            "hadLiveBridge": had_live_bridge, "sessionMode": session_mode, "runtime": runtime,
            "note": note,
        }
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    finally:
        await db.close()
