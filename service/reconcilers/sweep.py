"""The reconcile sweep: every reconciler, in an order that is load-bearing.

RELOCATED from `service/main.py` in v0.5.4, byte-identical. It was 287 of that file's 579 lines —
half the application entry point given over to one function that starts no server, builds no app and
reads no config. It orchestrates the reconcilers, so it belongs at the top of the reconciler layer,
not in the module whose job is to boot the process. Same reasoning that moved
`_reconcile_terminal_controls` out of `service/db.py`: a reconciler in the connection layer is in the
wrong place however well it works there.

THE ORDER IS NOT ARBITRARY AND IT HAS COST REAL TIME. `service/tests/test_reconcile_sweep_ordering.py`
pins seven pairs with the incident behind each — a spawn sat `running` for 97 minutes on 2026-08-07
because the superseded reaper ran before the dead-terminal finaliser and only clears a dead spawn once
a NEWER live session exists. That test reads this file by path; it was re-aimed here in the same
commit as the move, which is the cost of a location pin and the reason the pin is worth keeping
anyway.

THE IMPORTS STAY FUNCTION-SCOPE. They were written that way in `main.py` to keep application startup
from dragging in the whole reconciler layer before the app object exists, and hoisting them would be
a behaviour change to import order dressed as tidying. A byte-identical move does not get to decide
that; it is a separate question with its own answer.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def _run_dispatch_reconcile_once() -> dict[str, int]:
    from service.db import get_db as _get_db
    # v0.5 slice 1a: these two moved out of api_v2 into their own module. Imported here in the
    # SAME commit as the move so there is never a tree with mixed old/new sources.
    # v0.5 slice 2: spawn lifecycle moved out of api_v2 in the same commit as this import change.
    from service.reconcilers.terminal_history import _prune_terminal_history
    from service.reconcilers.console_binding import rebind_orphaned_live_consoles
    # v0.5 slice 3a: session reconcilers moved; imported here in the same commit as the move.
    # v0.5 slice 4.
    from service.reconcilers.managed_workers import (
        _reconcile_managed_worker_hygiene,
        _repair_unusable_active_runs,
    )
    from service.reconcilers.orphaned_managed_runs import _close_orphaned_managed_runs
    from service.reconcilers.dispatch_lifecycle import (
        _clear_turn_busy_for_dead_bridges,
        _close_steered_contracts_for_parent_run,
        _fail_stranded_delivered_reply_runs,
        _prune_orphaned_dispatch_runs,
        _sweep_unmirrored_failed_handoffs,
    )
    from service.reconcilers.dispatch_queue import (
        _close_reconcilable_delivered_runs,
        _reap_undeliverable_queued_runs,
        _replay_undelivered_channel_messages_on_env_recovery,
        _requeue_orphaned_claimed_runs,
        _reroute_orphaned_managed_channel_runs,
    )
    from service.reconcilers.terminal_runs import (
        _close_active_terminal_runs_for_terminal,
        _close_idle_claude_terminal_run_without_reply,
        _close_idle_pi_terminal_run_without_reply,
        _fail_pending_terminal_controls,
        _reconcile_ended_terminal_controls,
        _reconcile_stuck_terminal_and_session_rows,
    )
    from service.reconcilers.terminals import (
        _close_idle_virtual_rpc_workers,
        _reconcile_resurrected_managed_consoles,
        _reconcile_stale_managed_terminals_for_resident_agents,
    )
    from service.reconcilers.terminal_consistency import _repair_terminal_session_consistency
    from service.reconcilers.dead_session_status import _reconcile_dead_session_status
    from service.reconcilers.sessions import (
        _reconcile_duplicate_resident_sessions,
    )
    from service.reconcilers.spawn_lifecycle import (
        _fail_orphaned_running_spawn_requests,
        _fail_running_spawns_superseded_by_current_session,
        _finalize_spawns_with_dead_terminals,
        _repair_spawn_requests_from_initial_dispatch_failures,
    )
    from service.reconcilers.status_cache import (
        _prune_superseded_bridges,
        _reap_stale_orphan_bridges,
        stale_seconds_from_settings,
    )
    from service.api_core.settings import _load_settings
    from service.api_core.status_refresh import _refresh_expired_agent_live_states
    from service.api_core.dispatch_sweeps import _run_contract_reminders_once

    db = await _get_db()
    try:
        # CRITICAL (perf/correctness): commit after EACH reconciler step. These steps are
        # independent, idempotent cleanups with no cross-step atomicity requirement. A single
        # trailing commit kept ONE write transaction open across the whole multi-second sweep,
        # holding SQLite's single writer lock the entire time — so every bridge claim/heartbeat
        # write (which the fleet polls constantly) got SQLITE_BUSY and 503'd as "database is
        # locked" once per minute. Committing per step releases the lock between steps (held for
        # ms, not seconds) so bridge writes interleave. `_commit_step` keeps that one-liner DRY.
        async def _commit_step(result):
            await db.commit()
            return result

        # v0.5 slice 2: one settings snapshot for the pass, loaded before the first consumer that
        # needs it. Same normalization slice 1a declared — per-pass rather than per-step.
        _reconcile_settings = await _load_settings(db)
        repaired_active = await _commit_step(await _repair_unusable_active_runs(db, limit=500))
        # Moved off the GET /spawn-requests read path (2026-06-29): these writes ran on every
        # ~15s dashboard poll, contending with all reads. Run them here in the 60s sweep instead.
        await _commit_step(await _repair_spawn_requests_from_initial_dispatch_failures(db))
        await _commit_step(await _fail_orphaned_running_spawn_requests(
            db, offline_seconds=int(_reconcile_settings.get("environment_offline_seconds", 90) or 90)
        ))
        # Runs BEFORE the superseded-by-current-session reaper: that one only clears a
        # dead spawn once a NEWER live session exists, which is what left the 2026-08-07
        # spawn `running` for 97 minutes (its replacement did not arrive until 15:13).
        # This one needs no successor — the dead terminal is proof enough.
        await _commit_step(await _finalize_spawns_with_dead_terminals(db))
        await _commit_step(await _fail_running_spawns_superseded_by_current_session(db))
        closed_delivered_total = 0
        for _ in range(10):  # hard cap: <= 10 * 500 = 5k runs per pass
            batch = await _close_reconcilable_delivered_runs(db, limit=500)
            await db.commit()  # release the lock between batches
            closed_delivered_total += len(batch)
            if len(batch) < 500:
                break
        pruned = await _commit_step(await _prune_terminal_history(db))
        # Supersede bridge rows whose process died without a clean supersede BEFORE the
        # prune below — otherwise a crashed bridge lingers superseded_by='' forever,
        # counted "live" by every status/dispatch scan (idle-CPU + orphan re-accumulation,
        # 2026-07-11 perf report). This is the missing durable reaper.
        # Settings for this pass were loaded once at the top (slice 1a/2 normalization).
        reaped_orphan_bridges = await _commit_step(await _reap_stale_orphan_bridges(
            db, stale_seconds=stale_seconds_from_settings(_reconcile_settings)
        ))
        pruned_bridges = await _commit_step(await _prune_superseded_bridges(db))
        # WS4 Task 4.3: GC TERMINAL dispatch_runs whose endpoints have no live
        # owner (tombstoned/removed/unknown), past the retention TTL. Never
        # touches non-terminal runs or any run referencing a live agent.
        pruned_orphaned_runs = await _commit_step(await _prune_orphaned_dispatch_runs(
            db,
            ttl_hours=int(
                _reconcile_settings.get("orphaned_dispatch_run_retention_hours", 24) or 24
            ),
        ))
        reminders = await _commit_step(await _run_contract_reminders_once(db, limit=50, recent_only=True))
        # Event-driven (service-start event): clear stale managed PTY rows
        # for agents that are currently registered as resident. A previous
        # service container died holding those PTY processes; the rows
        # still show "attached" but no bridge owns them. Without this
        # the dashboard renders ghost consoles for resident agents.
        # Cross-team report 2026-08-11: warm rotation leaves a LIVE console bound to the session it
        # just ended, so the dashboard offers "Start console" for an agent whose PTY is alive and
        # clicking it spawns a second one. State-based on purpose — agent_sessions is written from
        # many sites, so keying on "live terminal, ended owner, current row unbound" cannot be
        # defeated by a new rotation path. Runs AFTER the resurrect healer, which owns the DEAD
        # terminal case; this one only ever touches `attached`.
        rebound_consoles = await _commit_step(await rebind_orphaned_live_consoles(db))
        stale_resident_terminals = await _commit_step(await _reconcile_stale_managed_terminals_for_resident_agents(db))
        # Auto-close persistent workers idle longer than the configured
        # window (default 0 = disabled). Returns the closed terminals
        # so the periodic-reconcile log shows them.
        closed_idle_workers = await _commit_step(await _close_idle_virtual_rpc_workers(db, limit=200,
            idle_close_enabled=bool(_reconcile_settings.get("worker_idle_close_enabled", False)),
            idle_close_minutes=int(_reconcile_settings.get("worker_idle_close_minutes", 0) or 0)))
        # Tight-window cleanup for managed-mode runs whose bridge
        # didn't report failure (bridge crashed or failure PATCH was
        # dropped during a transient connection blip). 5-min default.
        # Prompt recovery for runs stranded at 'claimed' by a bridge that died/
        # restarted before delivering (confirmed 2026-06-02: a kill/restart left
        # 3 hermes runs stuck at 'claimed' for 15+ min — never delivered, agent
        # falsely busy, sender never replied to). Requeue them so a live bridge
        # re-claims + delivers, instead of waiting for the long stale reaper to
        # FAIL them. Non-destructive: only touches claimed-never-delivered runs
        # whose claim bridge is dead/stale; a live-bridge claim is left alone.
        # MUST run BEFORE _close_orphaned_managed_runs: that reaper would FAIL the
        # same claimed-never-delivered orphan (recovery is preferable to failure).
        requeued_orphaned_claims = await _commit_step(await _requeue_orphaned_claimed_runs(db, limit=200))
        # Spawn-initial channel-routing fix (2026-06-03): re-route a queued
        # 'managed' run to 'channel' when its target has a live channel-sidecar.
        # The spawn-initial message is created before the agent's sidecar/flag is
        # up, so it stays 'managed' and the channel-sidecar (which claims only
        # channel/resident) never picks it up — "managed agents can't talk on the
        # first message". MUST run BEFORE _reap_undeliverable_queued_runs so the
        # re-routed run is claimed, not failed.
        rerouted_channel_runs = await _commit_step(await _reroute_orphaned_managed_channel_runs(db, limit=200))
        # Task #238: replay channel posts to members whose managed env was OFFLINE at
        # send time, now that it has recovered. The live send stores the inbox copy but
        # creates NO dispatch_run for an offline member, and nothing else revisits it —
        # so a recovered cold team stays silent. This mints the queued run the send would
        # have made; the queued-run backstop below then claims / cold-start-rescues it.
        # MUST run BEFORE _reap_undeliverable_queued_runs so the fresh run gets its full
        # backstop window (it's created with requested_at=now, so it's outside the cutoff
        # this pass regardless — belt-and-suspenders ordering).
        replayed_channel_msgs = await _commit_step(await _replay_undelivered_channel_messages_on_env_recovery(db, limit=200))
        # BUG 1 (2026-06-03): clear a stuck turn_busy=1 whose owning bridge
        # (agent_turn_state.turn_bridge_id) is dead/stale. A managed delivery loop
        # or resident channel-sidecar that set turn_busy on submit fires NO
        # turn-end event when its process dies (terminal closed / crash), so the
        # agent falsely shows `working` until the ~30-min ceiling. This is the
        # dead-claimer complement to the pure-event turn model — keyed on the
        # bridge's heartbeat, never the derived status, and only ever CLEARS.
        cleared_dead_turn_busy = await _commit_step(await _clear_turn_busy_for_dead_bridges(db, limit=200))
        # WS3 Task 3.2 (2026-06-02): backstop for `queued` runs no other reaper
        # covers — a queued run whose target has NO live claimer past the backstop
        # window would otherwise pile up to buffer_full. FAIL it + mirror to the
        # sender. MUST run AFTER requeue (a requeued orphan becomes `queued` and a
        # live bridge should get a chance to re-claim it first) and BEFORE
        # _close_orphaned_managed_runs.
        reaped_queued = await _commit_step(await _reap_undeliverable_queued_runs(db, limit=200))
        closed_orphaned_managed = await _commit_step(await _close_orphaned_managed_runs(db, limit=200))
        # A delivered require_reply run whose worker turn DIED without replying (model 429 /
        # mid-turn interrupt / stall) sits `delivered` forever — looks idle, strands the
        # contract (sc-manager live repro 2026-07-10). Past a staleness window well beyond the
        # reminder cycle, FAIL it with a clear cause. MUST run BEFORE the failed-handoff sweep
        # below so the SAME pass mirrors the failure notice to the sender.
        failed_stranded_replies = await _commit_step(await _fail_stranded_delivered_reply_runs(db, limit=200))
        # Sender notices for runs the REAPERS failed (vs a bridge PATCH, which mirrors
        # inline): without this sweep a require_reply run failed by the orphan-closer /
        # claim auto-heal never told the sender (review, 2026-06-10). Idempotent.
        mirrored_failed_handoffs = await _commit_step(await _sweep_unmirrored_failed_handoffs(db))
        # Managed console↔worker lifetime coupling (Workstream B): reap ghost
        # console rows (dead worker, terminal still 'attached') and detect
        # headless orphan workers (live sidecar, no console PTY) so a managed
        # claude is either online-with-console or fully down — never a headless
        # background worker (visible-TUI hard requirement).
        managed_hygiene = await _commit_step(await _reconcile_managed_worker_hygiene(db))
        # Self-heal the inverse: a console ghost-reaped on an INFERRED death (heartbeat lapse /
        # host starvation) whose worker is provably alive again (live channel-sidecar + fresh
        # output) is re-activated so the agent recovers `online` instead of staying stranded
        # `available` while it works headless (the next-manager incident, 2026-06-08). Runs AFTER
        # the reaper — they never fight in one pass (the reaper only reaps when signals are stale;
        # this only resurrects when they are fresh). Strictly scoped to the ghost-reap reason.
        resurrected_consoles = await _commit_step(await _reconcile_resurrected_managed_consoles(db))
        # Downgrade a live-status agent_sessions row to 'stopped' once its backing is
        # dead (2026-06-03): a managed session whose terminal is failed/stopped/
        # exited/lost, a session whose agent is stopped, or a resident session whose
        # owning bridge is stale/gone. Without this the dashboard shows a
        # contradictory "Stopped … running" / "Stale … running" row. MUST run AFTER
        # _reconcile_managed_worker_hygiene so the terminal-failed signal is already
        # set when case (a) reads terminal_status. Keyed only on bridge heartbeat for
        # the resident case — never on the derived 'stale' — so a live resident with
        # a fresh bridge is never stopped.
        dead_sessions_stopped = await _commit_step(await _reconcile_dead_session_status(db, lease_seconds=int(_reconcile_settings.get("resident_lease_seconds", 150) or 150), limit=500))
        # Collapse duplicate/stale resident sessions to one-per-agent so the
        # dashboard stops showing 2+ resident_* rows the operator can't tell apart
        # (2026-06-03). Keeps the freshest; retires the rest.
        deduped_resident_sessions = await _commit_step(await _reconcile_duplicate_resident_sessions(db, lease_seconds=int(_reconcile_settings.get("resident_lease_seconds", 150) or 150)))
        # Self-heal wedged 'stopping' PTYs + ended-but-not-closed sessions (2026-06-18 audit).
        stuck_rows = await _commit_step(await _reconcile_stuck_terminal_and_session_rows(db))
        ended_terminal_controls_failed = await _commit_step(await _reconcile_ended_terminal_controls(db, limit=500))
        # Server-side status self-heal. The live-status cache is otherwise
        # refreshed only on request (GET /agents, send, GET /agents/{id}), and
        # the only periodic driver was a CLIENT-SIDE dashboard setInterval that
        # browsers throttle/pause for background tabs. Without this sweep the
        # whole roster freezes on its last-computed verdict whenever no
        # dashboard is actively polling — e.g. a transient env-offline blip
        # sticking for 10+ minutes. Recomputes only rows whose refresh_after
        # has passed, so it is cheap.
        await _refresh_expired_agent_live_states(db)
        await db.commit()
        # WAL checkpoint hygiene (2026-06-18). WAL mode + connection-per-request +
        # CONTINUOUS dashboard polling (~40 short reads/s across both dashboards) means
        # the passive auto-checkpoint (1000 pages) can almost never advance past the
        # oldest live reader snapshot, so the -wal file grew unbounded (observed 83 MB).
        # A bloated WAL slows every read and lengthens each commit → longer SQLite
        # write-lock windows → more `database is locked` collisions. Run an explicit
        # TRUNCATE checkpoint each reconcile pass: it checkpoints as far as readers
        # allow and truncates the file whenever a reader gap appears (returns busy
        # otherwise — non-fatal). Bounds WAL growth without touching the hot path.
        try:
            import time as _t
            _ck_start = _t.monotonic()
            row = await (await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            checkpoint_result = tuple(row) if row else None
            _ck_ms = int((_t.monotonic() - _ck_start) * 1000)
            # Diagnostic (2026-06-29): row = (busy, log_pages, checkpointed_pages). busy=1 means a
            # live reader blocked the truncate (checkpoint starvation → WAL bloat → the documented
            # longer write-lock windows). Surface it so the "database is locked" cause is provable.
            if checkpoint_result and (checkpoint_result[0] == 1 or _ck_ms >= 1000):
                logger.warning(f"WAL-CHECKPOINT busy={checkpoint_result[0]} log_pages={checkpoint_result[1]} ckpt_pages={checkpoint_result[2]} took={_ck_ms}ms")
        except Exception as exc:
            checkpoint_result = f"skipped: {exc}"
        return {
            "wal_checkpoint": checkpoint_result,
            "repaired_active": repaired_active,
            "closed_delivered": closed_delivered_total,
            "reply_reminders": len(reminders.get("reminded", [])),
            "reply_reminder_skipped": len(reminders.get("skipped", [])),
            # Reported in the sweep log so a repair is VISIBLE. A silent healer is
            # indistinguishable from one that never ran — this repo's recurring lesson.
            "rebound_orphaned_consoles": rebound_consoles,
            "stale_resident_terminals_cleared": stale_resident_terminals,
            "idle_workers_closed": len(closed_idle_workers),
            "orphaned_managed_runs_closed": len(closed_orphaned_managed),
            "orphaned_claims_requeued": len(requeued_orphaned_claims),
            "rerouted_channel_runs": rerouted_channel_runs,
            "channel_msgs_replayed_on_env_recovery": len(replayed_channel_msgs),
            "deduped_resident_sessions": deduped_resident_sessions,
            "stuck_stopping_terminals_closed": stuck_rows.get("stuck_stopping_terminals_closed", 0),
            "ended_sessions_backfilled": stuck_rows.get("ended_sessions_backfilled", 0),
            "ended_terminal_controls_failed": ended_terminal_controls_failed,
            "dead_sessions_stopped": dead_sessions_stopped,
            "dead_bridge_turn_busy_cleared": len(cleared_dead_turn_busy),
            "undeliverable_queued_runs_failed": len(reaped_queued),
            "stranded_reply_runs_failed": len(failed_stranded_replies),
            "managed_ghost_rows_reaped": managed_hygiene.get("managed_ghost_rows_reaped", 0),
            "orphan_workers_reaped": managed_hygiene.get("orphan_workers_reaped", 0),
            "resurrected_consoles": resurrected_consoles,
            "reaped_orphan_bridges": reaped_orphan_bridges,
            "pruned_superseded_bridges": pruned_bridges,
            "pruned_orphaned_dispatch_runs": pruned_orphaned_runs,
            **{f"pruned_{key}": int(value or 0) for key, value in pruned.items()},
        }
    finally:
        await db.close()
