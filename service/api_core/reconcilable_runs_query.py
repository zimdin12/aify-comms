"""Which lingering `delivered` runs are safe to close, as one query.

Extracted from `_close_reconcilable_delivered_runs` in `service/reconcilers/dispatch_queue.py` in
v0.5.4; `test_close_reconcilable_delivered_runs_split_is_inert.py` inlines it back and AST-compares
against the pre-split fixture. The body is at its original 4-space column so the SQL is preserved
byte-for-byte.

IT IS THE QUERY THAT DECIDES, and the query is the part that was wrong. Everything after it in the
caller is bookkeeping over whatever rows come back; this is where "reconcilable" is actually
defined, across three classes:

    1. the reply LANDED and something failed to close the run
    2. no reply was ever owed and the run is older than `stale_hours`
    3. a reply was owed, the run is stale, and nobody is left to produce it

WHY CLASS 1 IGNORES `finished_at`, which is the defect this module exists around (2026-08-04): it
used to sit behind an outer `finished_at = ''` guard, and the path that links a reply sets
`result_message_id` AND `finished_at` together. So every row the class was written for was filtered
out before the clause was reached. Found live with seven such rows, the oldest from 2026-05-30 —
permanently stuck at `delivered`, and never once eligible for the reconciler that exists to repair
them. A row that is delivered WITH a finish stamp is inconsistent by definition; that is the repair,
not a reason to skip it.

CLASSES 2 AND 3 STILL REQUIRE AN EMPTY `finished_at`, deliberately: an unfinished run is one nobody
closed, whereas class 1 is a run somebody DID close without moving its status.
"""
from __future__ import annotations


async def _select_reconcilable_delivered_runs(db, limit, stale_hours):
    """Return the rows the reconciler may close, oldest first.

    Every argument is passed under the caller's own name: the extract-method gate splices this body
    back over its call without substituting arguments, so it refuses a call whose argument name
    differs from the parameter it fills.
    """
    cursor = await db.execute(
        """
        SELECT id, result_message_id, require_reply, requested_at
        FROM dispatch_runs
        WHERE status = 'delivered'
          AND (
            -- Class 1 is evaluated REGARDLESS of finished_at (2026-08-04). It used to sit behind
            -- an outer `finished_at = ''` guard, which excluded precisely the rows it was written
            -- for: the path that links a reply sets result_message_id AND finished_at together, so
            -- every run in this class was filtered out before the clause was reached. Result: a run
            -- whose reply LANDED and which was stamped finished stayed at status='delivered'
            -- forever, and the reconciler that exists to repair that could never see it. Found live
            -- with 7 such rows, the oldest 2026-05-30 — permanently stuck, never once eligible.
            -- A row that is delivered WITH a finish stamp is inconsistent by definition; that is
            -- the repair, not a reason to skip it.
            COALESCE(result_message_id, '') != ''
            OR (
              COALESCE(finished_at, '') = ''
              AND (
                require_reply = 0
                AND datetime(requested_at) <= datetime('now', ?)
              )
            )
            OR (
              COALESCE(finished_at, '') = ''
              AND (
              -- #20: a require_reply run that is stale AND has no active owner
              -- to ever produce the reply is orphaned — nothing will close it
              -- otherwise, so it lingers as a false "reply pending" forever.
              require_reply = 1
              AND datetime(requested_at) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_runs r2
                WHERE r2.target_agent = dispatch_runs.target_agent
                  AND r2.id != dispatch_runs.id
                  AND r2.status IN ('queued', 'claimed', 'running')
              )
              AND NOT EXISTS (
                SELECT 1 FROM agent_sessions s
                WHERE s.agent_id = dispatch_runs.target_agent
                  AND s.status IN ('starting', 'running', 'recovering', 'restarting', 'cli-takeover')
              )
            )
          )
        )
        ORDER BY requested_at ASC
        LIMIT ?
        """,
        (
            f"-{max(1, int(stale_hours or 24))} hours",
            f"-{max(1, int(stale_hours or 24))} hours",
            limit,
        ),
    )
    rows = await cursor.fetchall()
    return rows
