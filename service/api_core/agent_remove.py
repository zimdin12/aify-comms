"""Removing an agent end to end: stop its managed worker, wait briefly for the host, then tombstone.

The DELETE route ran this inline. It moved here because a removal request's `done` runs the same
removal (P0 C4), behind a fence the route does not need. It owns its commits, which is why it is not
in `agent_removal.py`, whose two functions run inside their caller's one transaction.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from service.api_core.agent_removal import _remove_agent_record
from service.api_core.agent_terminal_ops import _await_stop_claims, _request_stop_agent_terminals
from service.api_core.runtime import _normalize_session_mode
from service.clock import now as _now

#: Asked before anything changes and again inside the transaction that deletes; a non-empty answer
#: is why nothing is removed.
Refusal = Callable[[object], Awaitable[str]]


async def remove_agent(db, agent_id: str, *, actor: str, reason: str,
                       refusal: Optional[Refusal] = None) -> tuple[int, str]:
    """Remove `agent_id`, returning (rows deleted, why nothing was removed or "").

    A refusal that appears only at the second asking, after a managed worker was told to stop, leaves
    that worker stopped and the agent in place: stopping is undone by starting, a removal is not.
    """
    if refusal is not None:
        why = await refusal(db)
        if why:
            return 0, why
    # fix/hermes-leak P2 (REMOVE): for a MANAGED agent, tear the triad down by
    # signalling the bridge BEFORE the agent record is gone. We cannot use a
    # terminal_control here: deleting the agent cascades agents → agent_sessions
    # → terminal_sessions → terminal_controls, so any control emitted in this
    # request is wiped by the same delete. Instead REMOVE drives the triad reap
    # through the SAME agent-control STOP path (status=stopped + the bridge's
    # managed-hermes terminal stop reaps the triad), committed in its own
    # transaction, THEN tombstones. This makes REMOVE = STOP-then-tombstone, so
    # the surviving stop control (claimed before the tombstone delete) carries
    # the triad-reap. Resident agents are skipped (operator's own session).
    cursor = await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))
    agent_row = await cursor.fetchone()
    managed = bool(agent_row) and _normalize_session_mode(agent_row["session_mode"] or "resident") == "managed"
    if managed:
        now = _now()
        await db.execute(
            "UPDATE agents SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ? WHERE id = ?",
            ("Removed from dashboard; tearing down managed session.", now, agent_id),
        )
        signalled = await _request_stop_agent_terminals(
            db, agent_id, requested_by=actor, now=now, reap_triad=True,
        )
        await db.commit()
        # AND WAIT, BRIEFLY, FOR THE HOST TO TAKE IT. The comment above says this path depends on
        # the stop control being "claimed before the tombstone delete" -- and nothing made that
        # true. `terminal_controls` cascades from `terminal_sessions`, which cascades from
        # `agents`, so the delete below WIPES the control this request just wrote. The two
        # commits are milliseconds apart and it lost three times on 2026-09-07: aify-env streamed
        # into 404s for ten minutes, correctly refusing to kill workers that were still
        # producing, until its own silence guard stopped them.
        #
        # A BOUND, NOT A GUARANTEE. A host that is not listening cannot block a removal for ever;
        # the deadline expires and the delete proceeds exactly as it did before, so this is never
        # worse than the behaviour it replaces. See `_await_stop_claims`.
        if signalled:
            await _await_stop_claims(db, agent_id)
    if refusal is not None:
        await db.execute("BEGIN IMMEDIATE")
        why = await refusal(db)
        if why:
            await db.rollback()
            return 0, why
    deleted = await _remove_agent_record(db, agent_id, removed_by=actor, reason=reason)
    await db.commit()
    return deleted, ""
