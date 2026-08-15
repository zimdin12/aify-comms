"""Which managed worker terminals have been idle long enough to close, as one query.

Extracted from `_close_idle_virtual_rpc_workers` in `service/reconcilers/terminals.py` in v0.5.4;
`test_close_idle_virtual_rpc_workers_split_is_inert.py` inlines it back and AST-compares against
the pre-split fixture. The body is at its original 4-space column so the SQL is preserved
byte-for-byte.

CLOSING A WORKER IS DESTRUCTIVE, so every clause here is a reason NOT to. A candidate must be a live
terminal, running a recognised worker command, belonging to a MANAGED session, untouched for the
idle window, AND owing no work. Relax any one and the sweep closes a console an operator is looking
at or a worker that is about to be handed a run.

THE OWED-WORK CLAUSE IS THE SUBTLE ONE. It counts queued, claimed and running runs -- and ALSO
`delivered` runs that still require a reply, because a delivered run whose answer has not come back
is work the agent still owes even though nothing is executing. Dropping that half would close the
worker that was about to produce the reply.

MANAGED-NESS IS ASKED THREE WAYS -- the agent row, the session owner_mode, and the session mode
prefix -- because the three disagree in practice during mode switches and adoptions, and the safe
reading here is the permissive one: if ANY of them says managed, this is ours to close.

FOURTH QUERY EXTRACTION IN THIS SERIES. In a sweep the query IS the decision; everything after it is
bookkeeping over whatever it returned.
"""
from __future__ import annotations

from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET


async def _select_idle_virtual_rpc_workers(db, minutes, limit):
    """Return the idle managed worker terminals, least-recently-updated first.

    `minutes` arrives already validated by the caller, which is also what gates the sweep running
    at all. Every argument is passed under the caller's own name: inline-back does not substitute
    arguments.
    """
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
    return rows
