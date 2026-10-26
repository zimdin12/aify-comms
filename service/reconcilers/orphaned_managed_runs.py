"""Closing a managed run whose worker is never coming back.

RELOCATED from `service/reconcilers/dispatch_lifecycle.py` in v0.5.4, byte-identical. That module held
six independent lifecycle reconcilers and 518 lines; this was 135 of them and calls no sibling in the
file and reads none of its constants. One module per responsibility is how `service/reconcilers/` is
organised.

IT IS THE LAST RESORT, NOT THE FIRST. `sweep.py` deliberately runs other repairs BEFORE this one and
says so at the call site: this reaper would FAIL runs that an earlier pass can still recover, so
ordering is load-bearing and `test_reconcile_sweep_ordering.py` pins it. Anything that can requeue,
re-adopt or re-deliver gets first refusal; only what nothing can rescue reaches here.

THE SELECT LIVES ELSEWHERE ON PURPOSE. `_select_orphaned_managed_runs` was extracted to
`service/api_core/orphaned_runs_query.py` earlier in this release, and
`test_close_orphaned_managed_runs_split_is_inert.py` (retired in v0.6.13) inlined it back against a frozen fixture —
that proof reads whichever files these two live in, so it was re-aimed here in the same commit.

DB ACCESS: `db` is passed in and the CALLER commits — `sweep.py` wraps each step in `_commit_step`, so
a reconciler that committed on its own would break that batching.
"""
from __future__ import annotations

from service.api_core.events import _append_dispatch_event
from service.api_core.orphaned_runs_query import _select_orphaned_managed_runs
from service.api_core.settings import _load_settings
# The reaper reads the operational view's typed facts (0.9.8 switch); the legacy `engine_status` derivation stays in the
# tree for the rollback and is no longer what judges an orphaned run.
from service.api_core import operational_view, switched_view
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _close_orphaned_managed_runs(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Close managed/channel/resident dispatch_runs whose owning bridge
    didn't report a terminal status within `active_managed_run_stale_minutes`.

    Operator-reported case (2026-05-22): hermes-test's createHermesController
    spawn failed (provider missing) but the dispatch_run lingered in
    'running' state for 30 minutes before the generic 30-min stale repair
    caught it. The bridge's failure-PATCH may have hit a transient
    connection error and was logged-but-dropped — bridge-side retry
    logic now catches most of these, but a service-side safety net is
    still worth having for cases where the bridge crashed entirely.

    Only called from the periodic reconciler — NOT from preflight —
    because preflight's stale-repair call uses a different (terminal-
    only) discriminator that older steer-preflight tests pin against.
    This function catches orphaned runs regardless of dispatch_mode:
    a terminal-mode run with empty claim_bridge_id means the wrapper
    PTY backing was supposed to drive it but the bridge that spawned
    the PTY is gone — same orphan condition as managed-mode runs,
    deserves the same fast cleanup. Operator-reported 2026-05-22:
    hermes-test queued run sat blocked behind a terminal-mode running
    run with empty bridge_id for 45+ min waiting for the 30-min
    generic stale reaper.
    """
    settings = await _load_settings(db)
    stale_minutes = int(settings.get("active_managed_run_stale_minutes", 5) or 5)
    stale_seconds = max(60, stale_minutes * 60)
    cutoff_param = f"-{stale_seconds} seconds"
    # Absolute wall-clock ceiling (FIX 5, 2026-06-01): applied regardless of
    # bridge liveness, so a run pinned `working` by a live bridge whose inner
    # controller died is still aged out. Always >= stale_seconds so it never
    # narrows the existing bridge-staleness reaper. Keyed on no-progress for the
    # ceiling window (same dispatch_events check) so progressing runs are safe.
    ceiling_minutes = int(settings.get("active_managed_run_wall_ceiling_minutes", 30) or 30)
    ceiling_seconds = max(stale_seconds, ceiling_minutes * 60)
    ceiling_param = f"-{ceiling_seconds} seconds"
    rows = await _select_orphaned_managed_runs(db, cutoff_param, ceiling_param, limit)
    closed: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        dispatch_mode = str(row["dispatch_mode"] or "").strip()
        execution_mode = str(row["execution_mode"] or "").strip()
        started_at = str(row["started_at"] or "").strip()
        if not run_id:
            continue
        # THE SAFETY CALL READS TYPED FACTS (0.9.8 switch, SW5 review R1), never the displayed word: row 5 promotes a
        # fresh idle record to `working` BECAUSE of the run judged here, which protected the run from itself forever.
        #   - busy, or eligibility unknown -> the target may be mid-turn; a long turn IS progress, and comms cannot
        #                                     read otherwise. Leave it for a later cycle (decision R3's rule).
        #   - offline / stopped            -> the target provably cannot work it; fail FAST, naming that.
        #   - known idle                   -> aify-env says no turn is open, so the run made no progress on its own:
        #                                     the existing window and ceiling apply, with an honest reason.
        honest_reason = None
        if target_agent:
            try:
                target_row = await (await db.execute(
                    "SELECT * FROM agents WHERE id = ?", (target_agent,)
                )).fetchone()
            except Exception:
                target_row = None
            if target_row is not None:
                try:
                    view = await switched_view.view_for(db, target_row)
                    safety = operational_view.orphan_safety(view)
                except Exception:
                    view, safety = None, 'protect'  # an unreadable view is not evidence the run is orphaned
                if safety == 'protect':
                    continue
                if safety == 'gone':
                    honest_reason = (
                        f"target '{target_agent}' is {view.status}; "
                        f"run cannot be delivered."
                    )
                elif not started_at:
                    honest_reason = (
                        f"target '{target_agent}' is idle (aify-env reports no open turn) but the run was claimed "
                        f"without ever starting a turn for {stale_seconds}s (the claim succeeded "
                        f"but turn-start silently failed); run cannot be delivered."
                    )
                else:
                    honest_reason = (
                        f"target '{target_agent}' is idle (aify-env reports no open turn) but the claimed "
                        f"run made no progress for {stale_seconds}s and exceeded the "
                        f"{ceiling_seconds}s wall-clock ceiling; run cannot be delivered."
                    )
        reason = honest_reason or (
            f"Active run (dispatch_mode={dispatch_mode or '(default)'}, "
            f"execution_mode={execution_mode or '(default)'}) has no owning bridge "
            f"and made no progress for {stale_seconds}s, or exceeded the "
            f"{ceiling_seconds}s wall-clock ceiling with no progress — bridge "
            f"crashed, the inner controller died without reporting, the failure "
            f"PATCH was dropped, or the wrapper PTY never claimed."
        )
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed',
                error_text = ?,
                finished_at = COALESCE(finished_at, ?)
            WHERE id = ?
            """,
            (reason, now, run_id),
        )
        await _append_dispatch_event(db, run_id, "failed", reason)
        if target_agent:
            await _invalidate_agent_live_state(db, target_agent)
        closed.append({"runId": run_id, "agentId": target_agent})
    return closed
