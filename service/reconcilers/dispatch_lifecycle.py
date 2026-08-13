"""Dispatch lifecycle reconcilers: stranded replies, dead-bridge turn state, orphaned managed runs.

v0.5 slices 8 and 9, extracted from `service/routers/api_v2.py` as one module. The dependency scan
showed both groups share the same five borrowed names, and splitting them would mean two modules
reaching back for the same helpers — so they landed together.

REVIEW ANCHOR: `_close_orphaned_managed_runs` is 210 lines, over the reviewer's ≥200 bound. It is
named here rather than buried among five so the diff has an obvious focal point; if it should have
been its own slice, that is the call to make on this one.

BORROWED, on measured caller count as in slices 4-7: `_append_dispatch_event`,
`_clear_status_state_in_turn`, `_mark_dispatch_run_answered`, `_mirror_missing_dispatch_handoff`,
`_load_settings`, plus the constants `DEFAULT_SETTINGS` and `ACTIVE_RUN_BRIDGE_STALE_SECONDS` — each
read through exactly one owner so no second copy can drift.

`engine_status` IS BORROWED FROM THE ROUTER WRAPPER, and this sentence used to claim the opposite —
which the reviewer caught as a tag blocker, correctly, because the stale claim sat right on top of
the near-miss it describes. There is a `derive` in `service.status_engine` AND an `engine_status` in
`api_v2` (a DB-reading wrapper), they are not interchangeable, and I imported the wrong one first. It
compiled and passed the cycle smoke test. The shim below borrows the router's, deliberately.

A docstring that contradicts its own module is worse than no docstring: the next person reading this
file would have "re-fixed" it back to the bug.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from service.api_core.dispatch_run_state import _mark_dispatch_run_answered
from service.api_core.settings import _load_settings, DEFAULT_SETTINGS  # v0.5.1g: the leaf owner
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.liveness import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.turn_state import _clear_status_state_in_turn
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger(__name__)


# NOT `derive`. There is a `derive` in `service.status_engine` AND an `engine_status`, and they are
# not the same function: `derive` is the pure state machine, `engine_status` is the DB-reading wrapper
# that gathers its inputs first. This was a borrow shim precisely because the wrapper lived in the
# control plane and importing that from a reconciler is a cycle. It moved to a layer-1 module in
# v0.5.4, so the indirection is gone — but the near-miss it guarded is not: importing the wrong one
# would compile, pass a cycle smoke test, and quietly change how orphaned managed runs are judged.
from service.api_core.status_inputs import engine_status






async def _mirror_missing_dispatch_handoff(*a, **k):
    from service.control_plane import _mirror_missing_dispatch_handoff as _i
    return await _i(*a, **k)






async def _fail_stranded_delivered_reply_runs(db, *, stale_minutes: Optional[int] = None, limit: int = 200) -> list[dict[str, str]]:
    """Fail a delivered require_reply run whose worker turn died without replying.

    A managed hermes turn that dies to a model-429, a mid-turn interrupt, or a stall leaves
    its dispatch run at `delivered, require_reply=1, result_message_id=''` FOREVER — the
    turn-end→auto-mirror close never fires (that's the same hermes turn-end signal that
    flaps the status), so the run looks like the agent is idle/ignoring it (sc-manager live
    repro 2026-07-10: architect's 3 runs, all model-429'd before any work). This reaper
    closes that gap: past a staleness window WELL beyond the reminder cycle, a still-delivered
    rr=1 run with no reply is presumed dead and FAILED with a clear cause. The existing
    `_sweep_unmirrored_failed_handoffs` (next in the reconcile loop) then mirrors the failure
    to the sender — so instead of a silent `delivered`, the sender sees a visible FAILED.

    Keyed on STALENESS (not turn_busy) so it is robust even while the hermes turn-status
    flaps. SAFETY: a run the agent is CURRENTLY working (live turn on this exact run) is
    skipped, and the UPDATE re-checks `status='delivered'` so a reply landing concurrently
    wins the race. Idempotent (a failed run is never re-selected).
    """
    settings = await _load_settings(db)
    if stale_minutes is None:
        stale_minutes = int(settings.get("stranded_reply_fail_minutes", DEFAULT_SETTINGS["stranded_reply_fail_minutes"]) or 0)
    if stale_minutes <= 0:
        return []  # disabled
    stale_minutes = max(10, int(stale_minutes))
    cutoff = f"-{stale_minutes} minutes"
    rows = await (await db.execute(
        """
        SELECT id, target_agent, from_agent, subject, requested_at
        FROM dispatch_runs
        WHERE status = 'delivered'
          AND COALESCE(require_reply, 0) = 1
          AND COALESCE(result_message_id, '') = ''
          AND datetime(COALESCE(requested_at, '')) <= datetime('now', ?)
        ORDER BY requested_at ASC
        LIMIT ?
        """,
        (cutoff, max(1, int(limit or 200))),
    )).fetchall()
    failed: list[dict[str, str]] = []
    now = _now()
    reason = (
        "Turn ended without a reply — the worker turn is presumed dead (model 429, mid-turn "
        "interrupt, or stall). Failed by reconcile so the run isn't stranded as 'delivered'."
    )
    for row in (rows or []):
        run_id = str(row["id"] or "").strip()
        target = str(row["target_agent"] or "").strip()
        if not run_id or not target:
            continue
        # SAFETY: never fail a run the agent is actively working RIGHT NOW (live turn on
        # this exact run). A flap can transiently show turn_busy=1; skipping (not failing)
        # is the conservative side — the run is retried next pass.
        turn = await (await db.execute(
            "SELECT turn_busy, turn_run_id FROM agent_turn_state WHERE agent_id = ?",
            (target,),
        )).fetchone()
        if turn is not None and int(turn["turn_busy"] or 0) == 1 and str(turn["turn_run_id"] or "").strip() == run_id:
            continue
        cur = await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', finished_at = ?, summary = ?, error_text = ?
            WHERE id = ? AND status = 'delivered' AND COALESCE(result_message_id, '') = ''
            """,
            (now, reason, reason, run_id),
        )
        if (cur.rowcount or 0) == 0:
            continue  # a reply / other transition won the race
        await _append_dispatch_event(db, run_id, "stranded_reply_failed", reason)
        await _invalidate_agent_live_state(db, target)
        failed.append({"runId": run_id, "agentId": target})
    return failed


async def _clear_turn_busy_for_dead_bridges(db, *, limit: int = 200) -> list[dict[str, str]]:
    """Clear a stuck turn_busy=1 whose owning bridge (turn_bridge_id) is dead.

    BUG 1 (2026-06-03): a managed delivery loop / resident channel-sidecar that
    sets turn_busy=1 on submit (hermes-managed-host.js / claude-channel.js) clears
    it on a turn-END EVENT (gateway idle, /turn-end). When that loop process DIES
    (terminal closed, crash) it fires NO turn-end event, so turn_busy sticks until
    the long TURN_BUSY_BACKSTOP_SECONDS ceiling (~30 min) and the agent falsely
    shows `working` the whole time. (Confirmed live: ci-senior-dev stuck `working`,
    turn_bridge_id `hermes-managed-host-wsl:laputa-ci-senior-dev`, turn_updated_at
    ~174s ago, that loop process gone.)

    This is the DEAD-CLAIMER complement to the pure-event turn model — NOT a
    staleness window on normal `working`. It clears turn_busy ONLY when the bridge
    that SET it is no longer live, using the SAME staleness definition as the
    orphaned-claim requeue (ACTIVE_RUN_BRIDGE_STALE_SECONDS heartbeat window):

      1. turn_busy = 1 (the agent is marked mid-turn), AND
      2. turn_bridge_id identifies a REAL owning BRIDGE — excludes both the empty
         owner AND the resident-claude hook marker 'user-prompt-submit'. Both are
         harness/hook-owned turns (the UserPromptSubmit/PostToolUse hook sets
         turn_bridge_id='user-prompt-submit', which is NOT a bridge_instances id),
         validated by the agent's own liveness + the turn-end (Stop) hook, not by a
         bridge row. Sweeping them treated every hook-driven turn as "owned by a
         dead bridge" and wiped turn_busy each reconcile cycle → working agents
         flickered to online (#233). Left to the live-gate (a dead session reads
         offline) + the 30-min ceiling so a genuinely-working agent is never cut off. AND
      3. that turn_bridge_id is NOT a fresh bridge_instances row — either no such
         row exists (superseded-away / never-registered) OR its last_seen is past
         the stale window (the loop stopped heartbeating ⇒ dead).

    A bridge whose last_seen is fresh is genuinely mid-delivery — left untouched
    (the running turn keeps `working`). For each match: zero turn_busy via the
    SAME write the /turn-end endpoint uses and invalidate the agent's live-state
    cache so the false `working` clears immediately. ANTI-FEEDBACK-LOOP safe: this
    only ever CLEARS, keyed on the bridge's heartbeat truth, never on the server's
    derived status.
    """
    stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
    cursor = await db.execute(
        """
        SELECT ats.agent_id, ats.turn_bridge_id
        FROM agent_turn_state ats
        WHERE ats.turn_busy = 1
          AND COALESCE(ats.turn_bridge_id, '') NOT IN ('', 'user-prompt-submit')
          AND NOT EXISTS (
            SELECT 1 FROM bridge_instances bi
            WHERE bi.id = ats.turn_bridge_id
              AND COALESCE(bi.agent_id, '') = ats.agent_id
              AND datetime(bi.last_seen) > datetime('now', ?)
          )
        ORDER BY ats.turn_updated_at ASC
        LIMIT ?
        """,
        (stale_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    cleared: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        agent_id = str(row["agent_id"] or "").strip()
        raw_bridge = str(row["turn_bridge_id"] or "").strip()
        dead_bridge = raw_bridge or "(none)"
        if not agent_id:
            continue
        # COMPARE-AND-SWAP (2026-07-10 review S2): scope the clear to the EXACT stale
        # bridge id this row was selected for. Between the SELECT above and here (multiple
        # awaits on the single loop) a POST /heartbeat {turnBusy:true} from a NEWLY-LIVE
        # bridge can rewrite this row (turn_busy=1, turn_bridge_id=B_new). An unconditional
        # clear would then blow away that fresh turn (last-writer-wins) → the just-resumed
        # agent flaps to `online` for a pulse cycle. Guarding on the observed bridge id +
        # turn_busy=1 makes the concurrent-rewrite case a no-op. Mirrors the heartbeat
        # clear path, which already keys on the stored bridge id before clearing.
        cur = await db.execute(
            """
            UPDATE agent_turn_state
            SET turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = ?
            WHERE agent_id = ?
              AND turn_busy = 1
              AND COALESCE(turn_bridge_id, '') = ?
            """,
            (now, agent_id, raw_bridge),
        )
        if not cur.rowcount:
            # CAS miss: a fresh heartbeat from a new live bridge rewrote the row — the
            # agent is legitimately busy again; do NOT clear its in_turn/cache.
            continue
        # Keep the v2 engine in sync — the dead bridge's heartbeats are exactly what set
        # in_turn=1, and no turn_end will ever arrive from it (review M3, 2026-06-10).
        await _clear_status_state_in_turn(db, agent_id)
        await _invalidate_agent_live_state(db, agent_id)
        cleared.append({"agentId": agent_id, "deadBridgeId": dead_bridge})
    return cleared


async def _close_steered_contracts_for_parent_run(
    db,
    parent_row,
    *,
    result_message_id: str,
) -> int:
    """Close same-sender steer contracts that were injected into a terminal run.

    Steer controls are extra guidance for the active turn. If the active turn's
    final result answers the same sender, that result also satisfies same-sender
    steered contracts that were delivered into the run.
    """
    result_message_id = str(result_message_id or "").strip()
    if not parent_row or not result_message_id:
        return 0
    parent_run_id = str(parent_row["id"] or "").strip()
    from_agent = str(parent_row["from_agent"] or "").strip()
    target_agent = str(parent_row["target_agent"] or "").strip()
    if not parent_run_id or not from_agent or not target_agent:
        return 0

    cursor = await db.execute(
        """
        SELECT r.id
        FROM dispatch_runs r
        JOIN dispatch_controls c ON c.source_message_id = r.message_id
        WHERE c.run_id = ?
          AND r.dispatch_mode = 'steer'
          AND r.status = 'delivered'
          AND r.from_agent = ?
          AND r.target_agent = ?
          AND COALESCE(r.result_message_id, '') = ''
        """,
        (parent_run_id, from_agent, target_agent),
    )
    rows = await cursor.fetchall()
    closed = 0
    for row in rows:
        await _mark_dispatch_run_answered(db, row["id"], result_message_id, "delivered")
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Closed by parent run {parent_run_id} result {result_message_id}",
        )
        closed += 1
    if closed:
        await _append_dispatch_event(
            db,
            parent_run_id,
            "handoff",
            f"Closed {closed} same-sender steered contract(s) with result {result_message_id}",
        )
    return closed


async def _prune_orphaned_dispatch_runs(
    db,
    *,
    ttl_hours: int = 24,
    chunk: int = 2000,
    max_chunks: int = 50,
) -> int:
    """Reclaim TERMINAL dispatch_runs whose endpoints have no live owner (WS4 Task 4.3).

    A tombstoned/removed agent leaves its dispatch_runs behind forever — the
    rows reference an agent that no longer exists, so they accrue with every
    test agent and every team teardown. This prunes only runs that are SAFE to
    drop, conservatively:

      DELETE a run iff ALL of:
        - status is TERMINAL ('completed', 'failed', 'cancelled') — never an
          in-flight 'queued'/'claimed'/'delivered'/'running' run; and
        - it is older than `ttl_hours` (keyed on finished_at, falling back to
          requested_at) — recent audit history of a just-removed agent is kept; and
        - NEITHER `target_agent` NOR `from_agent` is a CURRENTLY-LIVE agent
          (present in the `agents` table). A live agent is one still registered;
          a tombstoned/removed/unknown ref is not. This is the hard safety
          guarantee: a run touching ANY live agent is its history and is NEVER
          deleted.

    Endpoints like 'dashboard' or an external sender are 'unknown' (not in
    `agents`) and so do not protect a row — but a row is only pruned when BOTH
    ends lack a live owner, so no live agent ever loses inbound or outbound
    history. Chunked so a live control plane is never locked for long.
    """
    cutoff = f"-{max(1, int(ttl_hours))} hours"
    removed = 0
    for _ in range(max_chunks):
        cur = await db.execute(
            """
            DELETE FROM dispatch_runs WHERE id IN (
                SELECT id FROM dispatch_runs r
                WHERE r.status IN ('completed', 'failed', 'cancelled')
                  AND datetime(COALESCE(r.finished_at, r.requested_at)) < datetime('now', ?)
                  AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = r.target_agent)
                  AND NOT EXISTS (SELECT 1 FROM agents a WHERE a.id = r.from_agent)
                ORDER BY datetime(COALESCE(finished_at, requested_at)) ASC
                LIMIT ?
            )
            """,
            (cutoff, int(chunk)),
        )
        await db.commit()
        n = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
        removed += n
        if n < chunk:
            break
    return removed


async def _sweep_unmirrored_failed_handoffs(db, *, window_hours: int = 6, limit: int = 50) -> int:
    """Mirror sender notices for require_reply runs FAILED by a reaper, not a PATCH.

    _mirror_missing_dispatch_handoff fires only on PATCH /dispatch/runs/{id} (the bridge
    reporting) and the manual repair endpoint — runs failed by the reapers (orphan close,
    claim-path auto-heal, stale-active fail) never notified the sender; the contract just
    read `failed` if they happened to poll (review must-fix, 2026-06-10). Sweep recent
    terminal rr=1 runs with no result message and mirror them. Idempotent: the mirror sets
    result_message_id, so a swept run is never re-mirrored. Bounded by window + limit.
    """
    rows = await (await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE require_reply = 1
          AND status IN ('failed', 'cancelled')
          AND COALESCE(result_message_id, '') = ''
          AND COALESCE(finished_at, '') != ''
          AND datetime(finished_at) > datetime('now', ?)
        ORDER BY finished_at DESC
        LIMIT ?
        """,
        (f"-{max(1, int(window_hours))} hours", max(1, int(limit))),
    )).fetchall()
    mirrored = 0
    for row in (rows or []):
        try:
            if await _mirror_missing_dispatch_handoff(db, row):
                mirrored += 1
        except Exception:
            continue  # best-effort per row; the next pass retries
    return mirrored


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
