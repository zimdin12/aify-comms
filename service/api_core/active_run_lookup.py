"""Which dispatch run is CURRENTLY live for an agent — and which queued one a new send may join.

v0.5.4 layer 0. Four queries that each answer one question about an agent's in-flight work, moved out of
the control plane together because they are asked about the same thing and were interleaved with 3,700
lines that are not. Sibling of `api_core/active_run_discard.py`, which acts on the run this module finds.

Each is a SELECT and nothing more: no writes, no commit, no rollback, `db` passed in. That is what let
them move as a group without touching a transaction boundary.

WHY FOUR AND NOT ONE. They look near-identical — same table, similar WHERE — and unifying them behind a
status filter is the obvious tidy-up. It would also be a behaviour change: `_get_blocking_active_run`
gates whether a NEW dispatch may be created, `_current_active_run_row` reports what is running,
`_current_channel_awaiting_reply_run_row` finds the run a reply belongs to, and `_find_mergeable_queued_run`
decides whether a send may join an existing queued run instead of creating a second one. Their status sets
differ because the questions differ, and a shared helper would make each caller depend on the others'
requirements.

A LEAF: imports `_get_dispatch_state_for_agent` (api_core/dispatch_state.py) and the standard library.
It does not import the control plane; the control plane is a CALLER.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.dispatch_state import _get_dispatch_state_for_agent


async def _current_active_run_row(db, agent_id: str):
    # Only a genuinely claimed/running dispatch run counts as "working".
    # NOTE: terminal-delivery runs sit 'delivered'+unfinished as their
    # normal lingering state long after the agent finished (they reconcile
    # lazily), so 'delivered' is NOT a reliable working signal — treating it
    # as one pins idle agents to "working" (worse failure mode). Accurate
    # mid-turn detection needs a bridge-reported turn-busy signal, tracked
    # separately; do not re-add a delivered-run heuristic here.
    cursor = await db.execute(
        """
        SELECT id, status, subject, from_agent, dispatch_mode, execution_mode, runtime, requested_at, claimed_at, started_at, claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent = ? AND status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT 1
        """,
        (agent_id,),
    )
    return await cursor.fetchone()


async def _current_channel_awaiting_reply_run_row(db, agent_id: str):
    # claude-channel.js delivers both 'channel' and 'resident' execution_mode
    # dispatches and now (post-fix) marks any require_reply=1 run as
    # 'delivered' to preserve the reply contract. While in 'delivered'
    # awaiting the agent's reply, the agent IS working — surface that as
    # "working" in the dashboard. _current_active_run_row deliberately
    # excludes 'delivered' to avoid pinning idle terminal-delivery agents
    # to working. The discriminator that lets us treat THIS case safely
    # is execution_mode IN ('channel', 'resident') — terminal-delivery
    # runs carry execution_mode='managed', so they're filtered out.
    cursor = await db.execute(
        """
        SELECT id, subject, from_agent, execution_mode, runtime, requested_at, claimed_at, started_at
        FROM dispatch_runs
        WHERE target_agent = ?
          AND status = 'delivered'
          AND execution_mode IN ('channel', 'resident')
          AND require_reply = 1
        ORDER BY COALESCE(started_at, claimed_at, requested_at) DESC
        LIMIT 1
        """,
        (agent_id,),
    )
    return await cursor.fetchone()


async def _get_blocking_active_run(db, agent_id: str, exclude_run_id: str = "") -> Optional[dict[str, Any]]:
    state = await _get_dispatch_state_for_agent(db, agent_id)
    active = state.get("activeRun")
    if not active:
        return None
    if exclude_run_id and active.get("runId") == exclude_run_id:
        return None
    return active


async def _find_mergeable_queued_run(
    db,
    *,
    recipient_id: str,
    from_agent: str,
):
    # Keep queued merge ownership scoped to one sender. Cross-sender merge
    # loses the contract owner and makes handoff replies go to the wrong agent.
    cursor = await db.execute(
        """
        SELECT *
        FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        (recipient_id, from_agent),
    )
    return await cursor.fetchone()
