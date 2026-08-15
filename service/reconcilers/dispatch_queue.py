"""Dispatch-queue reconcilers: runs that nobody will claim, deliver, or close.

v0.5 slice 7, extracted from `service/routers/api_v2.py`. Dependency table measured with the
every-name scan before the move.

THIS SLICE BORROWS THE MOST — ten functions and four constants as of v0.5.3, and the function count
went UP rather than down, which is worth stating plainly. `_load_settings`, `_append_dispatch_event`
and the runtime normalizers were repointed at their leaf owners in v0.5.1e/g/i, and two helpers
(`_mirror_undeliverable_queued_run_to_sender`, `_agent_has_live_claimer`) were RETIRED into this
module in v0.5.3 because this file held their only callers. But `_agent_has_live_claimer` calls three
router-owned helpers of its own, so retiring it traded one borrow for three. That is the right trade
— a 70-line predicate now lives with its caller instead of 700 lines away — but it is a trade, not a
free win, and the borrow table is the place to admit it. Each name still borrowed is either a many-caller helper
(`_create_dispatch_runs`, `_finalize_dispatch_runs`, `_insert_messages_via_console`) or a constant whose
duplication would be a drift hazard. Moving them would convert a 471-line relocation into a rewrite
of the dispatch core, which is not what an empty-behaviour-changelog release does.

The `_load_settings` DEPARTURE noted at extraction — borrowed rather than seamed, because three of
these five read settings and one does it inside a loop over environments — was resolved by the leaf
extraction rather than by the seam: `service/api_core/settings.py` owns it now, so the read happens
in the same place it always did and no caller had to hoist anything.

The remaining borrows are the visible debt of this extraction. Together with slice 3b's
`_agent_liveness` and slice 4/5/6's terminal helpers, they are the decision waiting at the end of
slice 10: consolidate, or accept a router that leaf modules still reach into.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from service.api_core.channel_replay_query import _select_undelivered_channel_messages
from service.api_core.reconcilable_runs_query import _select_reconcilable_delivered_runs
from service.api_core.dispatch_run_state import _finalize_dispatch_runs
from service.api_core.managed_env import _managed_environment_unavailable_reason
from service.api_core.runtime import (
    _normalize_runtime,
    _normalize_session_mode,
)
from service.api_core.settings import _load_settings  # v0.5.1g: the leaf owner
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.liveness import (
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
    _has_live_channel_sidecar,
)
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.api_core.channel_delivery import (
    _CHANNEL_FLAG_GATED_RUNTIMES,
    _CHANNEL_MANAGED_RUNTIMES,
    _insert_messages_via_console,
)

logger = logging.getLogger(__name__)


















# Was a borrow shim: the owner lived in the control plane, which a reconciler cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.
from service.api_core.dispatch_runs import _create_dispatch_runs





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

    rows = await _select_reconcilable_delivered_runs(db, limit, stale_hours)
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


async def _replay_undelivered_channel_messages_on_env_recovery(
    db, *, horizon_hours: Optional[int] = None, limit: int = 200
) -> list[dict[str, str]]:
    """Task #238: replay a channel post to a member whose managed environment was
    OFFLINE at send time, once that environment recovers.

    ``send_channel_message`` drops any member whose managed environment is
    effectively offline from ``dispatch_recipients`` (via
    ``_preflight_live_send_recipients`` → ``_managed_environment_unavailable_reason``):
    the canonical message + the member's inbox copy are stored with
    ``dispatch_requested=1`` but NO ``dispatch_run`` is created. The env-recovery
    heartbeat only invalidates the status cache — nothing turns that stored
    message into a wake. So a cold team that recovers stays silent (the
    "sc-manager's broadcasts left targets available, no answers" class, #191).

    This reconciler closes the gap: for each stored-but-un-dispatched channel
    inbox message whose member's env is now AVAILABLE, it creates the queued
    dispatch run the send would have made. The existing queued-run backstop
    (``_reap_undeliverable_queued_runs``, later in the same sweep) then claims or
    cold-start-rescues it, so no separate coldstart is needed here.

    Idempotent + double-dispatch-safe: every channel run records the member's
    fanout inbox id in ``dispatch_runs.message_id`` (see
    ``_dispatch_message_id_for_recipient``), so a member who already has a run
    (launchable at send, even if still queued/unread) is excluded by the
    ``NOT EXISTS`` guard, and a member we replay is excluded on the next pass by
    the same guard. Already-read messages are skipped too. A horizon bounds how
    far back we look so an env down for days doesn't resurrect stale roll-calls.
    """
    settings = await _load_settings(db)
    if horizon_hours is None:
        horizon_hours = int(
            settings.get("channel_offline_replay_horizon_hours", 24) or 24
        )
    horizon_hours = max(1, int(horizon_hours))
    cutoff_param = f"-{horizon_hours} hours"
    rows = await _select_undelivered_channel_messages(db, cutoff_param, limit)
    replayed: list[dict[str, str]] = []
    for row in rows:
        message_id = str(row["id"] or "").strip()
        member = str(row["to_agent"] or "").strip()
        if not message_id or not member:
            continue
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (member,))).fetchone()
        if agent_row is None:
            # Tombstoned member — its stored messages are drained by agent-delete.
            continue
        if _normalize_session_mode(agent_row["session_mode"] or "resident") != "managed":
            # The offline-env drop is a managed-binding concern; resident delivery
            # is owned by the channel-sidecar, not this reconciler.
            continue
        if await _managed_environment_unavailable_reason(db, agent_row):
            # Env still not available — leave the message stored, retry next pass.
            continue
        # Env recovered → create the queued run the send would have made. Mirror the
        # channel-send call exactly, keyed on this member's fanout inbox id so the run
        # carries message_id = m.id (the idempotency/double-dispatch guard above).
        runs = await _create_dispatch_runs(
            db,
            [member],
            from_agent=str(row["from_agent"] or ""),
            message_type=str(row["type"] or "message"),
            subject=str(row["subject"] or ""),
            body=str(row["body"] or ""),
            priority=str(row["priority"] or "normal"),
            in_reply_to=None,
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            source_message_ids={member: message_id},
            steer=False,
            require_reply=False,
            # #238: never merge a replay into a pre-existing queued run — the merge keeps
            # the OTHER run's message_id, so this replayed message's fanout id would never
            # be recorded on a run and the sweep would re-replay it forever. Insert a
            # dedicated run keyed on this message_id so the watermark records it.
            allow_merge=False,
        )
        await _finalize_dispatch_runs(db, runs, [(member, "managed")], [])
        await _invalidate_agent_live_state(db, member)
        replayed.append({"messageId": message_id, "agentId": member})
    return replayed


async def _requeue_orphaned_claimed_runs(db, *, grace_seconds: int = 90, limit: int = 200) -> list[dict[str, str]]:
    """Requeue dispatch_runs stranded at 'claimed' by a dead claiming bridge.

    Confirmed live bug (2026-06-02): a bridge claims a run (status -> 'claimed')
    and then dies/restarts (wrapper restart, all hermes.exe killed) BEFORE it
    transitions the run claimed -> delivered. The dead bridge never delivers, a
    NEW bridge will NOT re-claim an already-'claimed' run, so the run is stranded:
    the agent shows falsely busy/working, the message never reaches the console,
    and the sender never gets a reply. (Observed: 3 hermes [STATE CHECK] runs
    stuck at 'claimed' for 15+ min — a `claimed` event, NO `delivered` event,
    only repeated `reply_reminder_skipped "target is busy"`.)

    The existing reapers don't cover this promptly:
      - `_repair_unusable_active_runs` skips a run unless it is the agent's
        CURRENT active run; an orphaned claim by a dead bridge isn't current.
      - `_close_orphaned_managed_runs` only acts after a long stale window /
        wall-clock ceiling, and it FAILS the run rather than recovering it.

    This recovers fast and non-destructively: a run is requeued only when ALL of:
      1. status = 'claimed' (NOT delivered/running/terminal — those reached the
         agent; leave them to the existing reapers),
      2. claimed_at is older than `grace_seconds` ago (long enough that a live
         bridge would have transitioned claimed -> delivered in seconds; short
         enough to recover fast without racing an in-flight delivery),
      3. there is NO `delivered` dispatch_event for the run (never delivered),
      4. the `claim_bridge_id` is NOT a fresh/live bridge_instances row — uses the
         SAME staleness definition as the active-run reaper
         (ACTIVE_RUN_BRIDGE_STALE_SECONDS heartbeat window). An empty
         claim_bridge_id also qualifies (no owner at all).

    GUARD: a claimed run whose claim bridge IS fresh is genuinely delivering right
    now — it is left untouched.

    For each match: requeue it (status='queued', clear claim_bridge_id /
    claim_machine_id / claimed_at), append a `requeued_orphaned_claim` event noting
    the dead bridge id, and invalidate the agent's live-state cache so the false
    busy/working status clears. A live bridge then re-claims + delivers.
    """
    grace_param = f"-{max(1, int(grace_seconds))} seconds"
    stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
    cursor = await db.execute(
        """
        SELECT id, target_agent, claim_bridge_id
        FROM dispatch_runs r
        WHERE r.status = 'claimed'
          AND COALESCE(r.claimed_at, '') != ''
          AND datetime(r.claimed_at) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_events de
            WHERE de.run_id = r.id AND de.event_type = 'delivered'
          )
          AND (
            COALESCE(r.claim_bridge_id, '') = ''
            OR NOT EXISTS (
              SELECT 1 FROM bridge_instances bi
              WHERE bi.id = r.claim_bridge_id
                AND datetime(bi.last_seen) > datetime('now', ?)
            )
          )
        ORDER BY r.claimed_at ASC
        LIMIT ?
        """,
        (grace_param, stale_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    requeued: list[dict[str, str]] = []
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        dead_bridge = str(row["claim_bridge_id"] or "").strip() or "(none)"
        if not run_id:
            continue
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'queued',
                claim_bridge_id = '',
                claim_machine_id = '',
                claimed_at = ''
            WHERE id = ?
            """,
            (run_id,),
        )
        await _append_dispatch_event(
            db,
            run_id,
            "requeued_orphaned_claim",
            f"Requeued: claim bridge '{dead_bridge}' is dead/stale and the run was "
            f"never delivered (stranded at 'claimed' >{grace_seconds}s). A live "
            f"bridge will re-claim.",
        )
        if target_agent:
            await _invalidate_agent_live_state(db, target_agent)
        requeued.append({"runId": run_id, "agentId": target_agent})
    return requeued


async def _reroute_orphaned_managed_channel_runs(db, *, limit: int = 200) -> int:
    """Reconcile (2026-06-03): a managed_via_wrapper agent's QUEUED run can be
    stuck at execution_mode='managed' when it was created BEFORE the agent's
    channel-sidecar/flag came up — the SPAWN-INITIAL message is the common case
    (the spawn creates the run, THEN the agent registers; _apply_channel_routing
    runs only at create-time and never re-runs for that run). The generic managed
    worker never claims it — managed claude/hermes delivery is owned by the
    channel-sidecar loop, which claims only channel/resident — so it sits queued
    forever (the live test confirmed: a fresh send routed to 'channel' and the
    agent replied 'ALIVE', but the spawn-initial run stayed 'managed' + unclaimed).
    This re-applies channel routing to ANY queued 'managed' run whose target now
    has a LIVE channel-sidecar (the authoritative delivery mechanism). Idempotent;
    skips when insert_messages_via_console is enabled. Returns rows re-routed."""
    settings = await _load_settings(db)
    if _insert_messages_via_console(settings):
        return 0
    channel_runtimes = sorted(_CHANNEL_MANAGED_RUNTIMES | _CHANNEL_FLAG_GATED_RUNTIMES)
    if not channel_runtimes:
        return 0
    rt_ph = ",".join("?" for _ in channel_runtimes)
    rows = await (
        await db.execute(
            f"""
            SELECT dr.id AS run_id, dr.target_agent AS target_agent
            FROM dispatch_runs dr
            JOIN agents a ON a.id = dr.target_agent
            WHERE dr.status = 'queued'
              AND dr.execution_mode = 'managed'
              AND LOWER(COALESCE(a.runtime, '')) IN ({rt_ph})
              AND a.session_mode = 'managed'
            LIMIT ?
            """,
            [*channel_runtimes, limit],
        )
    ).fetchall()
    reroute_ids: list[str] = []
    sidecar_cache: dict[str, bool] = {}
    for row in rows:
        target = str(row["target_agent"] or "")
        if target not in sidecar_cache:
            sidecar_cache[target] = await _has_live_channel_sidecar(db, target)
        if sidecar_cache[target]:
            reroute_ids.append(str(row["run_id"]))
    if not reroute_ids:
        return 0
    ph = ",".join("?" for _ in reroute_ids)
    await db.execute(
        f"UPDATE dispatch_runs SET execution_mode = 'channel' "
        f"WHERE id IN ({ph}) AND execution_mode != 'channel'",
        reroute_ids,
    )
    await db.commit()
    return len(reroute_ids)


# _reap_undeliverable_queued_runs, _agent_has_live_claimer and
# _mirror_undeliverable_queued_run_to_sender moved to
# service/reconcilers/undeliverable_queued_runs.py in v0.5.4 — they called only each other,
# and only the reaper had an importer outside this module.
