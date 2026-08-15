"""Which claimed-or-running dispatch runs have been ORPHANED, as one query.

Extracted from `_close_orphaned_managed_runs` in `service/reconcilers/dispatch_lifecycle.py` in
v0.5.4; `test_close_orphaned_managed_runs_split_is_inert.py` inlines it back and AST-compares
against the pre-split fixture. The body is at its original 4-space column so the SQL is preserved
byte-for-byte, and the nineteen lines of comment above it travelled with the query they describe.

THE REAPER IS THE DANGEROUS KIND OF SWEEP: it fails runs that a bridge is still holding. A query
that is one clause too wide kills live work, and the failure looks to an operator like an agent that
gave up. So the conditions are conjunctive and each is there because something went wrong without
it — the two operator reports named in the comment are why the `reply_reminder_skipped` exclusion
and the ceiling exist at all.

THE CUTOFFS ARE PASSED IN, not computed. That is what makes the query testable without a clock: a
test hands it two timestamps and asserts which rows come back, instead of arranging for wall time to
pass. The caller keeps ownership of what "stale" means.

SECOND QUERY EXTRACTION IN THIS SERIES, after `reconcilable_runs_query.py`. Both follow the same
shape for the same reason: in a sweep, the query IS the decision and everything after it is
bookkeeping over whatever it returned.
"""
from __future__ import annotations


async def _select_orphaned_managed_runs(db, cutoff_param, ceiling_param, limit):
    """Return the runs the reaper may fail, oldest first.

    Every argument is passed under the caller's own name: the extract-method gate splices this body
    back over its call without substituting arguments, so it refuses a call whose argument name
    differs from the parameter it fills.
    """
    # Defense against false-positive reaping (code review C1, 2026-05-22):
    # an orphan candidate must satisfy ALL of:
    #   1. status claimed/running
    #   2. claim_bridge_id is empty (no bridge took ownership) OR the
    #      named bridge_instance is gone/stale (operator-reported
    #      2026-05-23: sc-coder's hermes managed run sat at "running"
    #      for 50+ min because claim_bridge_id pointed at a bridge
    #      that had since gone stale — original "claim_bridge_id = ''"
    #      check missed this case. A bridge that hasn't heartbeated
    #      within stale_seconds is dead from the dispatcher's POV;
    #      runs it claimed are orphaned).
    #   3. started_at + stale_seconds is in the past
    #   4. NO recent dispatch_events of PROGRESS kind (run hasn't
    #      progressed since the cutoff). reply_reminder_skipped is a
    #      service-side METADATA event the reminder loop emits about
    #      the run, not progress FROM the runtime — exclude it (same
    #      operator-report: reply_reminder_skipped fired every minute,
    #      kept resetting this cutoff window even after the controller
    #      had died).
    cursor = await db.execute(
        """
        SELECT id, target_agent, subject, started_at, requested_at, execution_mode, dispatch_mode, claim_bridge_id
        FROM dispatch_runs r
        WHERE r.status IN ('claimed', 'running')
          AND (
            -- Branch 1: no owning bridge (empty OR stale) + no progress for the
            -- stale window — the original fast bridge-liveness reaper.
            (
              (
                COALESCE(r.claim_bridge_id, '') = ''
                OR NOT EXISTS (
                  SELECT 1 FROM bridge_instances bi
                  WHERE bi.id = r.claim_bridge_id
                    AND datetime(bi.last_seen) > datetime('now', ?)
                )
              )
              AND datetime(COALESCE(r.started_at, r.requested_at)) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_events de
                WHERE de.run_id = r.id
                  AND datetime(de.created_at) > datetime('now', ?)
                  AND de.event_type NOT IN ('reply_reminder_skipped')
              )
            )
            -- Branch 2 (FIX 5): absolute wall-clock ceiling, applied REGARDLESS of
            -- bridge liveness. A run that has made no progress for the ceiling
            -- window is aged out even if the bridge is still heartbeating (the
            -- inner controller died without PATCHing the run terminal).
            OR (
              datetime(COALESCE(r.started_at, r.claimed_at, r.requested_at)) <= datetime('now', ?)
              AND NOT EXISTS (
                SELECT 1 FROM dispatch_events de
                WHERE de.run_id = r.id
                  AND datetime(de.created_at) > datetime('now', ?)
                  AND de.event_type NOT IN ('reply_reminder_skipped')
              )
            )
            -- Branch 3 (2026-06-18): CLAIMED but never STARTED past the stale window, regardless
            -- of bridge liveness. A live claim bridge does NOT prove the turn began — a managed/
            -- hermes claim whose prompt.submit silently failed to start a turn sits 'claimed'
            -- (so the target reads falsely 'busy' and reply-reminders skip with "target is busy")
            -- until the 30-min wall ceiling. Once claimed, a turn starts within seconds; started_at
            -- still NULL past stale_seconds means the start silently failed. The per-row
            -- working/blocked status guard below still protects a genuinely mid-turn target from a
            -- false reap (a real long turn has started_at set, so it isn't even a candidate here).
            OR (
              r.started_at IS NULL
              AND datetime(COALESCE(r.claimed_at, r.requested_at)) <= datetime('now', ?)
            )
          )
        ORDER BY r.requested_at ASC
        LIMIT ?
        """,
        (cutoff_param, cutoff_param, cutoff_param, ceiling_param, ceiling_param, cutoff_param, limit),
    )
    rows = await cursor.fetchall()
    return rows
