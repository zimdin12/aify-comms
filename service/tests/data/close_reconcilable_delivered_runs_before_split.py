"""The pre-split `_close_reconcilable_delivered_runs`, frozen.

Not imported by anything. It is the ONE true original that
`test_close_reconcilable_delivered_runs_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/reconcilers/dispatch_queue.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


async def _close_reconcilable_delivered_runs(
    db,
    *,
    limit: int = 500,
    stale_hours: int = 24,
) -> list[dict[str, str]]:
    # Three classes of reconcilable lingering 'delivered' runs:
    # 1. Any with result_message_id already set (reply landed but path
    #    that linked it didn't close the run — close now).
    # 2. require_reply=0 runs older than `stale_hours` (info-only, no
    #    reply expected, should have been auto-completed).
    # 3. require_reply=1 + orphaned (no in-flight runs AND no alive
    #    session) older than `stale_hours` — the agent that owed the
    #    reply is gone.

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
    now = _now()
    closed: list[dict[str, str]] = []
    for row in rows:
        run_id = str(row["id"] or "").strip()
        if not run_id:
            continue
        has_result = bool(str(row["result_message_id"] or "").strip())
        needs_reply = bool(int((row["require_reply"] if "require_reply" in row.keys() else 0) or 0))
        if has_result:
            reason = "result_linked"
            summary = "Closed delivered run after result reply was linked."
        elif needs_reply:
            reason = "stale_delivery_orphaned_no_owner"
            summary = "Closed stale delivered run requiring a reply: no active owner remains to ever produce it."
        else:
            reason = "stale_delivery_no_reply_required"
            summary = "Closed stale delivered run that did not require a reply."
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'completed',
                summary = CASE WHEN COALESCE(summary, '') = '' THEN ? ELSE summary END,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ? AND status = 'delivered'
            """,
            (summary, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "reconciled", summary)
        closed.append({"runId": run_id, "reason": reason})
    return closed
