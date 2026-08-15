"""Renaming an agent means rewriting every reference to the old id. This is that rewrite.

Extracted from `rename_agent` in `service/routers/agents/identity.py` in v0.5.4;
`test_rename_agent_split_is_inert.py` inlines it back and AST-compares against the pre-split fixture.
The body is at its original 8-space column so the SQL literals inside are preserved byte-for-byte.

IT RUNS INSIDE THE CALLER'S TRANSACTION AND OWNS NONE OF IT. `BEGIN IMMEDIATE`, the commit and the
rollback all stay in the route. That is deliberate: this is fourteen statements that must all land or
none of them, and a helper that could commit half of them would be a way to leave an agent with its
messages under the new id and its sessions under the old.

WHAT IS AND IS NOT REPOINTED is not obvious from reading it, and is asserted by
`test_agent_rename_covers_every_agent_reference.py`, which derives the full list from the schema
rather than from this file. Three groups exist: columns repointed here; columns that need no
repointing because their table cascades off `agents` when the old row is deleted below; and columns
deliberately left, of which the tombstone's own `agent_id` is the clearest — it must keep naming the
OLD id, since recording that the old id is retired is the entire point of writing it.
"""
from __future__ import annotations


async def _rewrite_agent_references_for_rename(db, agent_id, new_agent_id, now, req) -> None:
        """Copy the row under the new id, repoint every reference, retire the old id.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
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
