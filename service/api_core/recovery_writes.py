"""Two recovery writes: a sidecar saying it is alive, and a stranded claim being rescued.

Layer-0 slice of the v0.5.4 decomposition.

`_record_channel_sidecar_heartbeat` is the write behind the liveness READS in
`api_core/liveness.py` — the delivery loop stamping "I am still here". They are deliberately in
different modules: one is the observation, the other is the judgement made from it, and the judgement
has several readers the write does not.

`_requeue_instead_of_failing_undelivered_claim` is the rescue in the restart bug. A run CLAIMED by a
sidecar that then dies is never delivered, and the failing path used to take the spawn down with it —
the operator hit Restart and got no worker. This requeues instead, bounded by
`UNDELIVERED_CLAIM_REQUEUE_LIMIT` so a run that can never be delivered still terminates rather than
cycling forever. The bound moved with it: it is the difference between a rescue and an infinite loop,
and it belongs beside the code it bounds.

DB ACCESS: `db` passed to both, writes issued on it, and neither opens a connection, commits, or
rolls back — each joins its caller's transaction.
"""

from __future__ import annotations

from service.api_core.events import _append_dispatch_event
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _normalize_machine_id
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


UNDELIVERED_CLAIM_REQUEUE_LIMIT = 3


async def _record_channel_sidecar_heartbeat(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    machine_id: str,
    runtime: str,
    session_mode: str,
    now: str,
) -> None:
    """Task 1.6b (2026-05-30): upsert the standalone channel sidecar's
    bridge_instances row from its /dispatch/claim poll, so the continuous idle
    poll itself is the liveness heartbeat.

    A standalone channel sidecar (hermes-channel.js / claude-channel.js) polls
    /dispatch/claim continuously even when idle, but until it has actually
    claimed a run there is no bridge_instances row to refresh — so the plain
    `UPDATE ... SET last_seen` in claim_dispatch matched zero rows and
    `_has_live_channel_sidecar` saw nothing, flapping the agent's status to
    `available`. Inserting (or refreshing) a `bridge_kind='channel-sidecar'`
    row keyed by the sidecar's own bridge_id makes the poll a true heartbeat.

    This deliberately does NOT run the supersession/active-run-failing pass that
    `_record_bridge_registration` does — it is a lightweight idempotent liveness
    stamp, not a (re)registration, so it must never disturb other bridge rows or
    in-flight runs. The row it writes matches exactly the columns
    `_has_live_channel_sidecar` predicates on (agent_id, bridge_kind, last_seen,
    superseded_by='').
    """
    if not bridge_id:
        return
    normalized_machine = _normalize_machine_id(machine_id)
    normalized_runtime_value = str(runtime or "generic")
    normalized_session_mode_value = _normalize_session_mode(session_mode or "managed")
    # Refresh in place if the row already exists (the common case after the
    # first poll); otherwise insert a fresh, non-superseded liveness row. Keyed
    # on the PRIMARY KEY (bridge_id) so repeated polls are idempotent.
    updated = await db.execute(
        """
        UPDATE bridge_instances
        SET last_seen = ?,
            machine_id = COALESCE(NULLIF(?, ''), machine_id),
            runtime = ?,
            session_mode = ?,
            bridge_kind = 'channel-sidecar'
        WHERE id = ? AND agent_id = ?
        """,
        (
            now,
            normalized_machine,
            normalized_runtime_value,
            normalized_session_mode_value,
            bridge_id,
            agent_id,
        ),
    )
    if getattr(updated, "rowcount", 0):
        return
    await db.execute(
        """
        INSERT OR IGNORE INTO bridge_instances (
            id, agent_id, machine_id, runtime, session_mode, session_handle,
            terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            bridge_id,
            agent_id,
            normalized_machine,
            normalized_runtime_value,
            normalized_session_mode_value,
            "",
            "",
            "channel-sidecar",
            now,
            now,
            "",
            None,
        ),
    )
    # If the INSERT OR IGNORE was a no-op because a row with this id already
    # existed (race / pre-existing non-sidecar row), still refresh its
    # heartbeat and kind so the liveness signal is correct.
    await db.execute(
        """
        UPDATE bridge_instances
        SET last_seen = ?,
            machine_id = COALESCE(NULLIF(?, ''), machine_id),
            runtime = ?,
            session_mode = ?,
            bridge_kind = 'channel-sidecar'
        WHERE id = ? AND agent_id = ?
        """,
        (
            now,
            normalized_machine,
            normalized_runtime_value,
            normalized_session_mode_value,
            bridge_id,
            agent_id,
        ),
    )
    # FIX/available->online-promptness (2026-06-03): reaching this point means a
    # channel-sidecar row was newly INSERTed — the ongoing-poll case returns early
    # above on the in-place UPDATE (rowcount>0), so we only get here when the
    # sidecar JUST came alive. That flips `_has_live_channel_sidecar` -> True, so
    # the agent's derived status goes available->online. Invalidate the cached
    # live-state so the NEXT read recomputes immediately instead of waiting out
    # `agent_live_state.refresh_after` (keyed on heartbeat freshness, NOT worker
    # presence) — otherwise the operator sees the agent "spontaneously" flip to
    # online up to a poll-interval later, with no operator action.
    await _invalidate_agent_live_state(db, agent_id)


async def _requeue_instead_of_failing_undelivered_claim(db, run_id: str, *, reason: str) -> bool:
    """Prefer RECOVERY over failure for a run that was claimed and never delivered.

    THE RESTART BUG'S SECOND PATH (KNOWN_ISSUES, `claimed_at` set; traced on
    `run_1785537062959_4da30337`, 2026-07-31T22:31). A restart's new initial-brief run is
    CLAIMED by the OLD worker's channel sidecar one second before that sidecar is torn down.
    The sidecar dies holding the claim, so the run is never delivered and the spawn fails
    with it — the operator hits Restart and gets no worker.

    Two existing paths have the same trigger and opposite outcomes:

      _requeue_orphaned_claimed_runs      RECOVERS (requeue → a live bridge re-claims)
      _discard_unclaimable_active_run     FAILS the run, taking the spawn with it

    Both gate on the SAME `ACTIVE_RUN_BRIDGE_STALE_SECONDS`, so they become eligible together.
    KNOWN_ISSUES called that a race whose coin flip recovery kept losing. Verified 2026-08-07,
    it is worse than a race and the reason is ORDERING, not luck: inside one reconcile sweep
    `_repair_unusable_active_runs` (which reaches the failing path) runs at `main.py:113` and
    `_requeue_orphaned_claimed_runs` at `main.py:171`, with a commit between. The failing path
    is 58 steps earlier in the same pass, so recovery can never rescue a run it already
    failed. It is a DETERMINISTIC loss.

    The asymmetry is even wider than the ordering, and wider still than I first wrote.
    Recovery has exactly ONE call site — that sweep step. The failing funnel is reached from
    the sweep (`:9114`), from two SEND paths (`:7242`, `:9191`), AND — reviewer's catch,
    verified — from `GET /agents` itself (`list_agents`, `:13515`, one of the read-path writes
    DECISIONS.md deliberately keeps). The dashboard polls that roster, so pre-fix the failing
    path effectively ran on a dashboard cadence while recovery ran once a minute, strictly
    later. Recovery was not losing a coin flip; it was being lapped.

    That is why this belongs in the funnel rather than in the recovery path or in the sweep
    order: only the funnel covers all four callers at once. Reordering the sweep would have
    fixed one of them and left the read path and both send paths untouched.

    So the tie is broken here, at the single funnel every failing branch passes through:
    a run still at `claimed` with NO `delivered` event never reached the agent, and requeueing
    it is strictly better than failing it — a live bridge re-claims and delivers.

    NOT re-attempting the reverted shape (`0b948d2` → `70e03aa`). That one superseded the
    sidecar at terminal death, which cannot work: the claim happens one second BEFORE the
    death, and `_discard_superseded_active_run` then failed the run instantly with no grace,
    DELETING the rescue window. This change does the opposite — it widens the window rather
    than closing it, and adds no new machinery: the requeue is the existing, tested one.

    Deliberately narrow. Returns False (→ caller fails the run as before) unless ALL of:
      - the run is STILL `claimed` (a `running`/`delivered`/terminal run reached the agent —
        failing those is correct and untouched),
      - it has NO `delivered` event (same proof `_requeue_orphaned_claimed_runs` uses),
      - it has been rescued fewer than `UNDELIVERED_CLAIM_REQUEUE_LIMIT` times, so a
        genuinely undeliverable run still terminates instead of cycling forever.
    """
    row = await (await db.execute(
        "SELECT status, target_agent FROM dispatch_runs WHERE id = ?", (run_id,)
    )).fetchone()
    if not row or str(row["status"] or "").strip().lower() != "claimed":
        return False
    counts = await (await db.execute(
        """
        SELECT
          SUM(CASE WHEN event_type = 'delivered' THEN 1 ELSE 0 END) AS delivered,
          SUM(CASE WHEN event_type = 'requeued_orphaned_claim' THEN 1 ELSE 0 END) AS requeues
        FROM dispatch_events
        WHERE run_id = ?
        """,
        (run_id,),
    )).fetchone()
    if int((counts["delivered"] if counts else 0) or 0) > 0:
        return False  # it reached the agent — failing it is the caller's correct behaviour
    if int((counts["requeues"] if counts else 0) or 0) >= UNDELIVERED_CLAIM_REQUEUE_LIMIT:
        return False  # rescued enough times; accept that it is undeliverable
    await db.execute(
        """
        UPDATE dispatch_runs
        SET status = 'queued', claim_bridge_id = '', claim_machine_id = '', claimed_at = ''
        WHERE id = ? AND status = 'claimed'
        """,
        (run_id,),
    )
    await _append_dispatch_event(
        db,
        run_id,
        "requeued_orphaned_claim",
        "Requeued INSTEAD of failed: the run was claimed and never delivered, so it never "
        f"reached the agent and a live bridge can still deliver it. Would have failed with: {reason}",
    )
    target_agent = str(row["target_agent"] or "").strip()
    if target_agent:
        await _invalidate_agent_live_state(db, target_agent)
    return True
