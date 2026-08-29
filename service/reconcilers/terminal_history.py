"""Deleting terminal history that has outlived its usefulness.

RELOCATED from `service/reconcilers/terminals.py` in v0.5.4, byte-identical. That module holds
reconcilers that decide whether a terminal is in the right STATE; this one decides what to forget.
It calls no sibling there and reads none of its constants, which is what makes this a relocation
rather than a split.

RETENTION IS NOT RECONCILIATION. Everything else in the file it left answers "does this row still
describe reality" and repairs the row when it does not. This answers "is anyone ever going to read
this again" and deletes when the answer is no — a different question, a different failure mode, and
the only one here where being wrong loses data rather than mis-reporting it.

IT DELETES IN CHUNKS ON PURPOSE. `_chunked_delete` is nested because it exists only for this
function: an unbounded DELETE over terminal_events on a busy host takes SQLite's single write lock
for as long as it runs, and every dispatch, heartbeat and console write queues behind it. Bounded
batches keep the lock hold short even when the backlog is large.

DB ACCESS: `db` is passed in and the CALLER commits — `sweep.py` wraps each step in `_commit_step`,
so a reconciler that committed on its own would break that batching.
"""
from __future__ import annotations

from service.api_core.tuning import (
    TERMINAL_EVENTS_KEPT_PER_TERMINAL,
    TERMINAL_LIFECYCLE_EVENTS_KEPT_PER_TERMINAL,
)


async def _prune_terminal_history(
    db,
    *,
    terminal_event_ttl_hours: int = 24,
    dispatch_event_ttl_hours: int = 72,
    ended_output_ttl_hours: int = 24,
    terminal_control_ttl_hours: int = 24,
    keep_terminal_rows_per_agent: int = 8,
    chunk: int = 5000,
    max_chunks: int = 200,
) -> dict[str, int]:
    """Bounded history retention so the DB does not grow forever.

    The live console scrollback is the (already 64KB-capped)
    terminal_sessions.output column — that is what the dashboard reads and is
    NOT touched for active sessions. This only trims redundant audit history:
    per-chunk terminal_events past a TTL, dispatch_events past a TTL, and the
    output blob of long-ended terminals. Chunked deletes keep each statement
    short so a live control plane is never locked for long.
    """
    counts = {"terminal_events": 0, "terminal_events_capped": 0, "dispatch_events": 0, "ended_output_cleared": 0, "terminal_controls": 0, "terminal_sessions": 0}
    keep_events_per_terminal = TERMINAL_EVENTS_KEPT_PER_TERMINAL
    keep_lifecycle_per_terminal = TERMINAL_LIFECYCLE_EVENTS_KEPT_PER_TERMINAL

    async def _chunked_delete(sql: str, params: tuple) -> int:
        removed = 0
        for _ in range(max_chunks):
            cur = await db.execute(sql, params)
            await db.commit()
            n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            removed += n
            if n < chunk:
                break
        return removed

    counts["terminal_events"] = await _chunked_delete(
        f"DELETE FROM terminal_events WHERE id IN ("
        f"SELECT id FROM terminal_events WHERE datetime(created_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(terminal_event_ttl_hours))} hours",),
    )
    counts["dispatch_events"] = await _chunked_delete(
        f"DELETE FROM dispatch_events WHERE id IN ("
        f"SELECT id FROM dispatch_events WHERE datetime(created_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(dispatch_event_ttl_hours))} hours",),
    )
    # Per-terminal cap: chatty long-lived consoles produce hundreds of
    # thousands of event rows *within* the TTL window, so age alone cannot
    # bound them. Keep only the most recent N per terminal. Per-terminal
    # indexed deletes (idx_terminal_events_terminal on terminal_id,id) stay
    # fast and short even on a large table.
    term_ids = [
        r["terminal_id"]
        for r in await (await db.execute("SELECT DISTINCT terminal_id FROM terminal_events")).fetchall()
    ]
    # PER KIND, because one kind was starving the other. `terminal_output` rows are the fallback
    # recording; everything else is the lifecycle trail that says what happened to the terminal.
    # Measured 2026-08-29: 4,605 output rows against 326 lifecycle rows, and 3 of the 21 terminals at
    # the cap held ZERO lifecycle events -- their entire window was output chatter.
    #
    # The two passes are identical except for the predicate, so it is a parameter rather than two
    # copies of a delete loop that would drift.
    for tid in term_ids:
        for predicate, keep in (
            ("event_type = 'terminal_output'", keep_events_per_terminal),
            ("event_type != 'terminal_output'", keep_lifecycle_per_terminal),
        ):
            cutoff_row = await (await db.execute(
                f"SELECT id FROM terminal_events WHERE terminal_id = ? AND {predicate} "
                "ORDER BY id DESC LIMIT 1 OFFSET ?",
                (tid, keep),
            )).fetchone()
            if not cutoff_row:
                continue
            cutoff_id = cutoff_row["id"]
            for _ in range(max_chunks):
                cur = await db.execute(
                    f"DELETE FROM terminal_events WHERE id IN ("
                    f"SELECT id FROM terminal_events WHERE terminal_id = ? AND {predicate} "
                    f"AND id <= ? ORDER BY id ASC LIMIT {int(chunk)})",
                    (tid, cutoff_id),
                )
                await db.commit()
                n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                counts["terminal_events_capped"] += n
                if n < chunk:
                    break

    cur = await db.execute(
        "UPDATE terminal_sessions SET output = '' "
        "WHERE status IN ('stopped', 'failed', 'ended', 'cancelled') "
        "AND COALESCE(output, '') != '' "
        "AND datetime(updated_at) < datetime('now', ?)",
        (f"-{max(1, int(ended_output_ttl_hours))} hours",),
    )
    await db.commit()
    counts["ended_output_cleared"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # terminal_controls retention (2026-06-07): this is the runtime command QUEUE — once a
    # control is HANDLED (handled_at set) it is pure delivered-keystroke audit history. It was
    # never pruned, so it grew unbounded (13k+ rows over 4 days, dominated by per-keystroke
    # dashboard input). Delete ONLY handled controls past the TTL — a control with handled_at
    # IS NULL is still PENDING (a bridge has not claimed/executed it yet) and MUST never be
    # touched here, or a queued keystroke/resize/stop would be silently dropped. Chunked +
    # indexed on id so a live control plane is never locked for long.
    counts["terminal_controls"] = await _chunked_delete(
        f"DELETE FROM terminal_controls WHERE id IN ("
        f"SELECT id FROM terminal_controls "
        f"WHERE handled_at IS NOT NULL AND datetime(handled_at) < datetime('now', ?) "
        f"ORDER BY id ASC LIMIT {int(chunk)})",
        (f"-{max(1, int(terminal_control_ttl_hours))} hours",),
    )
    # terminal_sessions ROW retention (2026-06-17): the rows themselves were never pruned
    # — only their events/output blobs — so ENDED consoles accumulated forever (one managed
    # claude had 184 rows; 99% of the table was stopped/failed cruft). Keep the newest N per
    # agent (any status, so every LIVE console and recent history survives) and delete only
    # the OLDER ended (stopped/failed/ended/cancelled) rows. The status filter guarantees a
    # live console is NEVER deleted; the per-agent keep window guarantees recent debugging
    # history survives. Chunked so the control plane is never locked for long.
    keep_n = max(1, int(keep_terminal_rows_per_agent))
    counts["terminal_sessions"] = await _chunked_delete(
        f"DELETE FROM terminal_sessions WHERE id IN ("
        f"  SELECT t.id FROM terminal_sessions t"
        f"  WHERE LOWER(COALESCE(t.status,'')) IN ('stopped','failed','ended','cancelled')"
        f"    AND (SELECT COUNT(*) FROM terminal_sessions t2"
        f"         WHERE t2.agent_id = t.agent_id AND t2.updated_at > t.updated_at) >= {keep_n}"
        f"  ORDER BY t.updated_at ASC LIMIT {int(chunk)})",
        (),
    )
    return counts
