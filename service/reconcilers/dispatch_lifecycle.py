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

`engine_status` AND ITS WARNING LEFT WITH `_close_orphaned_managed_runs` in v0.5.4 — that reaper was
this module's only reader of it, and a caution about importing the wrong function belongs in the file
that does the importing. See `service/reconcilers/orphaned_managed_runs.py`.

A docstring that contradicts its own module is worse than no docstring: the next person reading this
file would have "re-fixed" it back to the bug.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from service.api_core.orphaned_runs_query import _select_orphaned_managed_runs
from service.api_core.dispatch_run_state import _mark_dispatch_run_answered
from service.api_core.authored_failures import TURN_ENDED_WITHOUT_REPLY, turn_interrupted
from service.api_core.settings import _load_settings, DEFAULT_SETTINGS  # v0.5.1g: the leaf owner
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.live_process_probes import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.turn_state import _clear_status_state_in_turn
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger(__name__)








# Was a borrow shim: the owner lived in the control plane, which a reconciler cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.
from service.api_core.dispatch_sweeps import _mirror_missing_dispatch_handoff






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
    # THE REASON TEXT LIVES IN `api_core/authored_failures.py`, not here, and that is the fix for a
    # confirmed incident (2026-08-18): this string used to say "presumed dead (model 429, mid-turn
    # interrupt, or stall)", and the notification layer's throttle classifier matched the literal
    # "429" inside our own list of GUESSES and told the sender their target was being rate-limited as
    # a determined fact. The real cause was a provider safety refusal — a branch this list did not
    # even name. One source, so the writer and the consumer that must recognise it cannot drift.
    reason = TURN_ENDED_WITHOUT_REPLY
    # ONE QUERY, so attributing a cause costs a single read for the whole sweep rather than one per
    # run. Interrupts are rare; the map is almost always empty.
    interrupts: dict[str, tuple[str, str]] = {}
    for control in (await (await db.execute(
        """
        SELECT t.agent_id AS agent_id, c.requested_by AS requested_by, c.requested_at AS requested_at
        FROM terminal_controls c
        JOIN terminal_sessions t ON t.id = c.terminal_id
        WHERE c.action = 'interrupt'
          AND datetime(COALESCE(c.requested_at, '')) >= datetime('now', '-1 hour')
        ORDER BY c.requested_at ASC
        """,
    )).fetchall() or []):
        agent = str(control["agent_id"] or "").strip()
        if agent:
            # LAST one wins: a turn interrupted twice was ended by the second.
            interrupts[agent] = (
                str(control["requested_by"] or ""), str(control["requested_at"] or ""),
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
        # A CAUSE WE RECORDED IS NEVER A CAUSE WE COULD NOT DETERMINE. If this agent's turn was
        # interrupted while this run was open, say so: the undetermined text lists a throttle and a
        # policy refusal beside "a mid-turn interrupt", and sending a reader to investigate a provider
        # for something an operator did on purpose is the failure this lookup removes.
        run_reason = reason
        interrupted = interrupts.get(target)
        if interrupted and str(row["requested_at"] or "") <= interrupted[1]:
            run_reason = turn_interrupted(interrupted[0], interrupted[1])
        cur = await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', finished_at = ?, summary = ?, error_text = ?
            WHERE id = ? AND status = 'delivered' AND COALESCE(result_message_id, '') = ''
            """,
            (now, run_reason, run_reason, run_id),
        )
        if (cur.rowcount or 0) == 0:
            continue  # a reply / other transition won the race
        await _append_dispatch_event(db, run_id, "stranded_reply_failed", run_reason)
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
    terminal rr=1 runs the sender was never told about and mirror them. Idempotent: the mirror sets
    handoff_message_id, so a swept run is never re-mirrored. The CONTRACT itself stays open — a
    system notice is evidence of non-delivery, not an answer (H2). Bounded by window + limit.
    """
    rows = await (await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE require_reply = 1
          AND status IN ('failed', 'cancelled')
          -- The already-TOLD marker, not the answer-arrived one (H2, 2026-08-18): a failure notice
          -- no longer closes the contract, so keying on result_message_id here would re-mirror
          -- every swept run on every reconcile pass — a notice storm to the sender.
          AND COALESCE(handoff_message_id, '') = ''
          -- ...and still nothing to say when the target ANSWERED before being reaped:
          -- apologising for non-delivery would be false.
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


# _close_orphaned_managed_runs moved to service/reconcilers/orphaned_managed_runs.py in
# v0.5.4 — its own responsibility, calling nothing here and reading none of this module's
# constants. The sweep ordering that puts it LAST is unchanged.
