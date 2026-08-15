"""The pre-split `_close_orphaned_managed_runs`, frozen.

Not imported by anything. It is the ONE true original that
`test_close_orphaned_managed_runs_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/reconcilers/dispatch_lifecycle.py` at the commit before the
extraction, decoded as utf-8 rather than through the locale codec.
"""


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
