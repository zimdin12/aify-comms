"""Dispatch state as the dashboard asks for it: one agent, or a map of many. Leaf module.

Layer-0 slice of the v0.5.4 decomposition. Two reads that answer "what is this agent's dispatch
doing" for the status surfaces, sharing one row-to-label projection.

Separate from `api_core/dispatch_text.py` on purpose: that module is pure formatting and takes no
`db`. These two query and then format, so folding them in would give a pure module a database.
`_format_dispatch_state` is imported from there rather than duplicated.

DB ACCESS: `db` passed in, reads only, no connection opened and no transaction taken.
"""

from __future__ import annotations

from typing import Any

from service.api_core.dispatch_text import _format_dispatch_state


async def _get_dispatch_state_for_agent(db, agent_id: str) -> dict[str, Any]:
    active_cursor = await db.execute(
        """
        SELECT id, from_agent, subject, status, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at
             , claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent = ? AND status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT 1
        """,
        (agent_id,)
    )
    active_row = await active_cursor.fetchone()
    queued_cursor = await db.execute(
        "SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
        (agent_id,)
    )
    queued_count = (await queued_cursor.fetchone())[0]
    return _format_dispatch_state(active_row, queued_count)


async def _get_dispatch_state_map(db, agent_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    active_cursor = await db.execute(
        f"""
        SELECT id, target_agent, from_agent, subject, status, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at, claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent IN ({placeholders}) AND status IN ('claimed', 'running')
        ORDER BY target_agent ASC, COALESCE(started_at, claimed_at, requested_at) ASC
        """,
        tuple(agent_ids),
    )
    active_rows = await active_cursor.fetchall()
    queued_cursor = await db.execute(
        f"SELECT target_agent, COUNT(*) AS queued_count FROM dispatch_runs WHERE target_agent IN ({placeholders}) AND status = 'queued' GROUP BY target_agent",
        tuple(agent_ids),
    )
    queued_rows = await queued_cursor.fetchall()
    queued_counts = {row["target_agent"]: int(row["queued_count"] or 0) for row in queued_rows}
    active_by_agent: dict[str, Any] = {}
    for row in active_rows:
        active_by_agent.setdefault(row["target_agent"], row)
    return {
        agent_id: _format_dispatch_state(active_by_agent.get(agent_id), queued_counts.get(agent_id, 0))
        for agent_id in agent_ids
    }
