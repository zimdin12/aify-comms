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
`test_close_orphaned_managed_runs_split_is_inert.py` still inlines it back against a frozen fixture —
that proof reads whichever files these two live in, so it was re-aimed here in the same commit.

DB ACCESS: `db` is passed in and the CALLER commits — `sweep.py` wraps each step in `_commit_step`, so
a reconciler that committed on its own would break that batching.
"""
from __future__ import annotations

from service.api_core.events import _append_dispatch_event
from service.api_core.orphaned_runs_query import _select_orphaned_managed_runs
from service.api_core.settings import _load_settings
# NOT `derive`. There is a `derive` in `service.status_engine` AND an `engine_status`, and they
# are not the same function: `derive` is the pure state machine, `engine_status` is the
# DB-reading wrapper that gathers its inputs first. I imported the wrong one once; it compiled
# and passed the cycle smoke test. Importing the wrong one here would quietly change how
# orphaned managed runs are judged, which is why this warning travelled with the reaper rather
# than staying in the file the reaper left.
from service.api_core.status_inputs import engine_status
from service.api_core.turn_state import _clear_status_state_in_turn
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
        # Phase F1 (folds in the false-failed-busy-run fix): under the event
        # engine the reaper consults the TARGET'S real status before failing a
        # no-progress run, instead of blindly attributing it to a crashed bridge.
        #   - working/blocked  → the target is mid-turn; a long turn IS progress.
        #                         Do NOT fail it — skip/defer to a later cycle.
        #   - stale/offline/stopped → the target is genuinely gone; fail FAST with
        #                         an HONEST reason naming the target's state.
        #   - online/idle (genuinely orphaned past the window) → keep the existing
        #                         ceiling, but with an honest reason.
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
                    target_status = await engine_status(db, target_row, settings=settings)
                except Exception:
                    target_status = ""
                if target_status in {"working", "blocked"} and started_at:
                    # Mid-turn = progress. Leave the run alone this cycle. BUT only when this
                    # candidate actually STARTED (started_at set) — a claimed-never-started run
                    # (Branch 3) is itself what drives the agent's false `working`/active-run
                    # reading, so honoring that guard would shield the stuck run from reaping
                    # forever (the #233 catch-22: false-busy → guard skips → never reaped → still
                    # false-busy). An unstarted claim can't be a real turn, so reap it regardless.
                    continue
                if target_status in {"stale", "offline", "stopped"}:
                    honest_reason = (
                        f"target '{target_agent}' is {target_status}; "
                        f"run cannot be delivered."
                    )
                elif target_status and not started_at:
                    honest_reason = (
                        f"target '{target_agent}' is {target_status} but the run was claimed "
                        f"without ever starting a turn for {stale_seconds}s (the claim succeeded "
                        f"but turn-start silently failed); run cannot be delivered."
                    )
                elif target_status:
                    honest_reason = (
                        f"target '{target_agent}' is {target_status} but the claimed "
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
            # Clear turn_busy so the agent's status falls back to
            # available/online instead of staying "working" via stale
            # heartbeat.
            await db.execute(
                """
                INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
                VALUES (?, 0, '', '', '', ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    turn_busy = 0,
                    turn_run_id = '',
                    turn_bridge_id = '',
                    turn_runtime = '',
                    turn_updated_at = excluded.turn_updated_at
                """,
                (target_agent, now),
            )
            # Keep the v2 engine in sync (dual-table drift guard, review M3 2026-06-10).
            await _clear_status_state_in_turn(db, target_agent)
            await _invalidate_agent_live_state(db, target_agent)
        closed.append({"runId": run_id, "agentId": target_agent})
    return closed
