"""Removing an agent: cancelling the work it will never do, then tombstoning it.

Two functions, 73 lines, together because the ORDER between them is the invariant. An agent record cannot
simply be deleted — it may own queued or claimed dispatch runs, and a run whose target has gone away is a
run nothing will ever complete. So the non-terminal runs are cancelled first, with their pending controls
failed, and only then is the agent tombstoned and its live state dropped.

TOMBSTONE, NOT DELETE, and this is why the pair exists rather than a single `DELETE FROM agents`: a
removed agent that merely vanished would be re-created by the next heartbeat from a bridge that had not
noticed, and test agents resurrect exactly that way. The tombstone is what makes removal stick.

`_cancel_nonterminal_runs_for_agents` is also called on its own by the superseded-bridge path, so it is not
private to removal — which is a second reason this is a module rather than one function with a helper
buried in it.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — removal and cancellation must
land in ONE transaction owned by the caller, or a crash between them leaves runs cancelled for an agent
that still exists.
"""

from __future__ import annotations

from service.api_core.active_run_discard import _fail_pending_controls_for_run
from service.api_core.agent_sessions import _tombstone_agent
from service.api_core.events import _append_dispatch_event
from service.api_core.serialization import _dedupe_preserve, _json_loads_or
from service.clock import now as _now
from service.reconcilers.status_cache import _live_state_drop


async def _remove_agent_record(
    db,
    agent_id: str,
    *,
    removed_by: str = "",
    reason: str = "",
) -> int:
    cursor = await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    row = await cursor.fetchone()
    runtime_state = _json_loads_or(row["runtime_state"], {}) if row else {}
    bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
    await _cancel_nonterminal_runs_for_agents(
        db,
        [agent_id],
        summary=f'Agent "{agent_id}" was removed before the run could finish.',
        event_type="agent_removed",
    )
    await _tombstone_agent(db, agent_id, removed_by=removed_by, bridge_id=bridge_id, reason=reason)
    await db.execute("DELETE FROM bridge_instances WHERE agent_id = ?", (agent_id,))
    # channel_members has no FK on agent_id, so removing an agent left GHOST memberships
    # (bughunt 2026-07-03): they permanently inflate memberCount AND every later channel
    # send INSERTs an undeliverable inbox row for the deleted agent (unbounded per-post
    # growth). Clean them up here.
    await db.execute("DELETE FROM channel_members WHERE agent_id = ?", (agent_id,))
    cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    # Evict the in-memory derived-status entry too (audit 2026-06-28): SQLite per-agent rows
    # cascade-delete, but _LIVE_STATE_CACHE is a process-global dict and would otherwise keep a
    # stale (never-served) entry forever — small unbounded leak across removed agent ids.
    _live_state_drop(agent_id)
    return cursor.rowcount or 0


async def _cancel_nonterminal_runs_for_agents(
    db,
    agent_ids: list[str],
    *,
    summary: str,
    event_type: str,
) -> int:
    targets = _dedupe_preserve([str(agent_id or "").strip() for agent_id in agent_ids if str(agent_id or "").strip()])
    if not targets:
        return 0

    cancelled = 0
    finished_at = _now()
    chunk_size = 250
    for i in range(0, len(targets), chunk_size):
        chunk = targets[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = await db.execute(
            f"""
            SELECT id
            FROM dispatch_runs
            WHERE target_agent IN ({placeholders})
              AND status IN ('queued', 'claimed', 'running')
            """,
            chunk,
        )
        rows = await cursor.fetchall()
        if not rows:
            continue
        for row in rows:
            await db.execute(
                "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                (summary, finished_at, row["id"]),
            )
            await _append_dispatch_event(db, row["id"], event_type, summary)
            await _fail_pending_controls_for_run(
                db,
                row["id"],
                handled_at=finished_at,
                response_text=summary,
            )
            cancelled += 1
    return cancelled
