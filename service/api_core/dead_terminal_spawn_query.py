"""The two questions the dead-terminal spawn sweep asks, and the accessor they share.

Extracted from `_finalize_spawns_with_dead_terminals` in `service/reconcilers/spawn_lifecycle.py`
in v0.5.4; `test_finalize_spawns_with_dead_terminals_split_is_inert.py` inlines both back and
AST-compares against the pre-split fixture. Bodies are at their original 4-space column.

ONE QUERY FINDS WORK; THE OTHER COUNTS WHAT THE GUARD REFUSED. A spawn is finalizable when its
session has a DEAD terminal and NO live sibling -- the live-sibling clause is a rebind-race guard,
because a session mid-rebind briefly shows both, and failing the spawn then would kill a healthy
worker. That guard is correct and SILENT, so the second query exists purely to say how many rows it
held back: without it "0 finalized" cannot be told apart from "the sweep never ran".

THE TWO MUST STAY IN STEP, which is why they live together. They share `end_statuses` and the same
notion of dead-and-live; if one learned a new end status and the other did not, the counter would
report a number about a different question than the sweep was asking.

DECLARED SUBSTITUTION: `_terminal_end_statuses_ordered` travelled here with its only two callers,
and its DOCSTRING is corrected rather than moved verbatim. It said the constant was owned by "the
router" and that a module-level import "would be a cycle" -- both were true when it was written and
neither is now: `_TERMINAL_END_STATUSES_ORDERED` lives in `service/api_core/terminal_status.py`,
which imports nothing. The lazy call is therefore vestigial. It is kept as-is rather than inlined
because v0.5.x is the refactor line and the bodies that call it must stay byte-identical; what is
fixed is the prose that had stopped being true.
"""
from __future__ import annotations


def _terminal_end_statuses_ordered() -> tuple[str, ...]:
    """The ONE owner of which terminal statuses mean "ended", borrowed rather than forked.

    Forking a second copy is exactly the divergence that produced finding N7 -- two managed-worker
    sweeps disagreeing about `degraded`. `test_terminal_status_sets_agree` pins the owner.

    The import is function-scope because it was written when the constant lived in a router and a
    module-level import would have been a cycle. It now lives in `service/api_core/terminal_status.py`,
    which imports nothing, so the laziness no longer buys anything -- left in place because the
    call sites around it are under a byte-identity proof.
    """
    from service.api_core.terminal_status import _TERMINAL_END_STATUSES_ORDERED

    return _TERMINAL_END_STATUSES_ORDERED


async def _select_spawns_with_dead_terminals(db, end_statuses, limit):
    """Spawns whose session has a dead terminal and no live sibling, oldest first."""
    cursor = await db.execute(
        f"""
        SELECT s.id AS spawn_id,
               s.agent_id AS agent_id,
               t.id AS terminal_id,
               t.status AS terminal_status,
               t.output AS terminal_output,
               t.error AS terminal_error,
               COALESCE(NULLIF(t.stopped_at, ''), t.updated_at) AS died_at
        FROM spawn_requests s
        JOIN terminal_sessions t ON t.session_id = s.session_id
        WHERE s.status IN ('starting', 'running')
          AND COALESCE(s.finished_at, '') = ''
          AND COALESCE(s.session_id, '') != ''
          AND LOWER(COALESCE(t.status, '')) IN ({end_statuses})
          AND NOT EXISTS (
            SELECT 1 FROM terminal_sessions live
            WHERE live.session_id = s.session_id
              AND LOWER(COALESCE(live.status, '')) NOT IN ({end_statuses})
          )
        ORDER BY s.created_at ASC
        LIMIT ?
        """,
        (*_terminal_end_statuses_ordered(), *_terminal_end_statuses_ordered(), max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    return rows


async def _count_spawns_masked_by_live_sibling(db, end_statuses):
    """How many finalizable-looking spawns the live-sibling guard held back."""
    masked_row = await (await db.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM spawn_requests s
        WHERE s.status IN ('starting', 'running')
          AND COALESCE(s.finished_at, '') = ''
          AND COALESCE(s.session_id, '') != ''
          AND EXISTS (
            SELECT 1 FROM terminal_sessions dead
            WHERE dead.session_id = s.session_id
              AND LOWER(COALESCE(dead.status, '')) IN ({end_statuses})
          )
          AND EXISTS (
            SELECT 1 FROM terminal_sessions live
            WHERE live.session_id = s.session_id
              AND LOWER(COALESCE(live.status, '')) NOT IN ({end_statuses})
          )
        """,
        (*_terminal_end_statuses_ordered(), *_terminal_end_statuses_ordered()),
    )).fetchone()
    return masked_row
