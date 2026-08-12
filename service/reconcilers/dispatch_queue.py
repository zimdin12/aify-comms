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
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from service.api_core.runtime import _normalize_runtime, _normalize_session_mode  # v0.5.1e: the leaf owner, not via the router
from service.api_core.settings import _load_settings, DEFAULT_SETTINGS  # v0.5.1g: the leaf owner
from service.api_core.events import _append_dispatch_event  # v0.5.1i: the leaf owner
from service.api_core.liveness import (  # v0.5.4: the leaf owner
    _has_live_claimer_lease,
    _has_recorded_claimer_lease,
    _resident_bridge_is_fresh,
)
from service.api_core.liveness import ACTIVE_RUN_BRIDGE_STALE_SECONDS, _has_live_channel_sidecar
from service.api_core.managed_env import _has_pending_or_booting_spawn_request
from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.api_core.channel_delivery import _CHANNEL_FLAG_GATED_RUNTIMES, _CHANNEL_MANAGED_RUNTIMES, _CHANNEL_SIDECAR_DELIVERY_RUNTIMES, _insert_messages_via_console

logger = logging.getLogger(__name__)








async def _agent_has_live_claimer(db, agent_row, *, settings: Optional[dict[str, Any]] = None) -> bool:
    """WS3 (2026-06-02): True when SOME process can claim + deliver a dispatch to
    this agent right now — the runtime-agnostic "live claimer" deliverability
    predicate used by the queued-run backstop (Task 3.2). (Task 3.3 deaf-target
    fail-fast was BLOCKED — see report — because a healthy wrapper-backed managed
    agent legitimately has a live console but no yet-registered claimer before its
    first /dispatch/claim poll, so this predicate cannot distinguish a deaf target
    from a not-yet-polled-healthy one at SEND time. The backstop reaper applies it
    only AFTER a long age window, where that ambiguity has resolved.)

    A live claimer is one of:
      - managed sidecar-delivery runtimes (claude-code / hermes): a fresh,
        non-superseded channel-sidecar bridge heartbeat (the claude-channel.js /
        hermes delivery loop that actually claims) — the SAME signal as the
        Task 3.1 status gate.
      - resident: a fresh resident bridge (its MCP bridge or its channel sidecar).
      - native managed (codex / pi / opencode): any fresh, non-superseded
        bridge_instances row for the agent (the managed env bridge / RPC worker
        that claims via /dispatch/claim).

    NOTE deliberately distinct from "available for cold lazy-autostart": a managed
    agent that is registered but has NO worker yet has no claimer here, but the
    send path still queues to it so the bridge can spawn-on-claim. This predicate
    only proves a claimer is ALIVE RIGHT NOW — callers decide whether absence is
    a fail-fast (up-but-deaf) or a benign cold start.
    """
    if agent_row is None:
        return False
    settings = settings or await _load_settings(db)
    runtime = _normalize_runtime(agent_row["runtime"] or "")
    session_mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    if session_mode == "resident":
        return await _resident_bridge_is_fresh(
            db, agent_row, lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150)
        )
    # Managed sidecar-delivery runtimes: the channel-sidecar / delivery loop IS
    # the claimer. WS5 Task 5.1 (2026-06-02): PREFER the explicit claimer lease.
    # A lease is the positive "the loop is a live claimer right now" signal the
    # delivery loop POSTs on ready and clears on teardown — it resolves the
    # lazy-claim ambiguity that BLOCKED the Task 3.3/5.1b deaf-target fail-fast.
    # Precedence:
    #   1. A lease has been recorded ⇒ the lease is AUTHORITATIVE:
    #        acquired+fresh ⇒ deliverable; released/stale ⇒ NOT deliverable
    #        (immediately — no waiting for the 180s sidecar staleness window).
    #   2. No lease has EVER been recorded ⇒ fall back to the channel-sidecar
    #        heartbeat (pre-existing/older loops + the lazy-claim contract: a
    #        not-yet-polled healthy claimer must NOT be treated as deaf).
    if runtime in _CHANNEL_SIDECAR_DELIVERY_RUNTIMES:
        if await _has_recorded_claimer_lease(db, agent_row["id"]):
            return await _has_live_claimer_lease(db, agent_row["id"])
        return await _has_live_channel_sidecar(db, agent_row["id"])
    # Native managed (codex / pi / opencode): a fresh, non-superseded bridge row
    # for the agent is the claiming worker. Channel sidecar also counts (defensive).
    if await _has_live_channel_sidecar(db, agent_row["id"]):
        return True
    try:
        stale_param = f"-{ACTIVE_RUN_BRIDGE_STALE_SECONDS} seconds"
        cursor = await db.execute(
            """
            SELECT 1 FROM bridge_instances
            WHERE agent_id = ?
              AND COALESCE(superseded_by, '') = ''
              AND datetime(last_seen) > datetime('now', ?)
            LIMIT 1
            """,
            (agent_row["id"], stale_param),
        )
        return await cursor.fetchone() is not None
    except Exception:
        return False



async def _coldstart_spawn_request_for_dispatch(*a, **k):
    from service.control_plane import _coldstart_spawn_request_for_dispatch as _i
    return await _i(*a, **k)




async def _mirror_undeliverable_queued_run_to_sender(db, row, *, reason: str) -> Optional[str]:
    """Write a reply/handoff message from the target back to the original sender
    so an undeliverable queued run (Task 3.2) surfaces instead of vanishing.

    Mirrors the shape of `_mirror_missing_dispatch_handoff` but works for a
    QUEUED run that never reached the agent (no result handoff path applies).
    Skips dashboard senders (the dashboard reads the failed run directly).
    """
    from_agent = str((row["target_agent"] if row else "") or "").strip()
    to_agent = str((row["from_agent"] if row else "") or "").strip()
    if not to_agent or to_agent == "dashboard" or not from_agent:
        return None
    subject = str((row["subject"] if row else "") or (row["id"] if row else "") or "dispatch").strip()
    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    body = (
        "Your queued message was never delivered: the target has no live worker "
        f"(no live claimer) and the run was failed by the queued-run backstop.\n\n{reason}"
    )
    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            from_agent,
            to_agent,
            "direct",
            "error",
            f"[NOT DELIVERED] {subject}",
            body,
            str((row["priority"] if row else "") or "normal"),
            0,
            str((row["message_id"] if row and "message_id" in row.keys() else "") or "") or None,
            ts,
        ),
    )
    return message_id



async def _create_dispatch_runs(*a, **k):
    from service.control_plane import _create_dispatch_runs as _i
    return await _i(*a, **k)


async def _finalize_dispatch_runs(*a, **k):
    from service.control_plane import _finalize_dispatch_runs as _i
    return await _i(*a, **k)


async def _managed_environment_unavailable_reason(*a, **k):
    from service.control_plane import _managed_environment_unavailable_reason as _i
    return await _i(*a, **k)

















async def _reap_undeliverable_queued_runs(db, *, backstop_seconds: Optional[int] = None, limit: int = 200) -> list[dict[str, str]]:
    """WS3 Task 3.2 (2026-06-02): backstop reaper for `queued` dispatch_runs that
    no other reaper covers.

    The existing reapers select `claimed`/`running`/`delivered` only — a `queued`
    run whose target has NO live claimer is invisible to all of them. It piles up
    in the merged buffer until `_DISPATCH_BUFFER_CAP`, then NEW sends hard-reject
    with `buffer_full`. Only an agent-delete drains it. This reaper closes that
    gap: a queued run older than `queued_run_backstop_seconds` whose target is NOT
    deliverable (no live claimer, same predicate as the Task 3.1 status gate /
    Task 3.3 fail-fast) is FAILED with an actionable error, mirrored back to the
    sender, and the target's status cache is invalidated.

    GUARD: a queued run whose target HAS a live claimer is left alone — it will be
    claimed and delivered on the next poll. A run inside the backstop window is
    also left alone (a cold `available` agent may still lazy-autostart on claim).
    """
    settings = await _load_settings(db)
    if backstop_seconds is None:
        backstop_seconds = int(settings.get("queued_run_backstop_seconds", DEFAULT_SETTINGS["queued_run_backstop_seconds"]) or 180)
    backstop_seconds = max(30, int(backstop_seconds))
    cutoff_param = f"-{backstop_seconds} seconds"
    # A run that was just requeued from an orphaned claim (_requeue_orphaned_claimed_runs)
    # keeps its original (old) requested_at, so it would trip this backstop in the
    # SAME reconcile pass and defeat the requeue. Such a run HAD a live claimer
    # once; give it a fresh backstop window to be re-claimed by excluding any
    # queued run with a `requeued_orphaned_claim` event newer than the cutoff.
    cursor = await db.execute(
        """
        SELECT id, target_agent, from_agent, subject, message_id, priority, requested_at
        FROM dispatch_runs r
        WHERE r.status = 'queued'
          AND datetime(COALESCE(r.requested_at, '')) <= datetime('now', ?)
          AND NOT EXISTS (
            SELECT 1 FROM dispatch_events de
            WHERE de.run_id = r.id
              AND de.event_type IN ('requeued_orphaned_claim', 'coldstart_rescue')
              AND datetime(de.created_at) > datetime('now', ?)
          )
        ORDER BY r.requested_at ASC
        LIMIT ?
        """,
        (cutoff_param, cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
    reaped: list[dict[str, str]] = []
    now = _now()
    for row in rows:
        run_id = str(row["id"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        if not run_id or not target_agent:
            continue
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (target_agent,))).fetchone()
        if agent_row is None:
            # Tombstoned target — its runs are drained by agent-delete; skip here.
            continue
        if await _agent_has_live_claimer(db, agent_row, settings=settings):
            # Deliverable — a live claimer will pick it up on the next poll.
            continue
        # Bug D fix (2026-07-02): SELF-HEAL before failing. The run may be queued because
        # the send-path coldstart was suppressed (e.g. a fresh-but-dead wrapper-child row
        # from a worker that crashed at boot) and no second send ever re-triggered it. Try
        # ONE cold-start; if a worker spawn is now (or already) in flight, grant the run a
        # fresh backstop window instead of failing it. One-shot per run — the
        # 'coldstart_rescue' event both grants the window (query exclusion above) and
        # disqualifies a second rescue here.
        run_session_mode = str(agent_row["session_mode"] or "").strip().lower()
        run_runtime = _normalize_runtime(str(agent_row["runtime"] or ""))
        already_rescued = await (await db.execute(
            "SELECT 1 FROM dispatch_events WHERE run_id = ? AND event_type = 'coldstart_rescue' LIMIT 1",
            (run_id,),
        )).fetchone()
        if (
            not already_rescued
            and run_session_mode == "managed"
            and run_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}
        ):
            rescued = await _coldstart_spawn_request_for_dispatch(
                db,
                target_agent,
                runtime=run_runtime,
                settings=settings,
                requested_by="queued-run-backstop",
            )
            if rescued or await _has_pending_or_booting_spawn_request(db, target_agent):
                await _append_dispatch_event(
                    db,
                    run_id,
                    "coldstart_rescue",
                    f"Backstop cold-started a managed worker for '{target_agent}' instead of failing the run; one fresh window granted.",
                )
                continue
        reason = (
            f"Queued for >{backstop_seconds}s with no live claimer for target "
            f'"{target_agent}" (no live channel sidecar / no claiming bridge). The '
            f"agent is up-but-deaf or never started a worker — failed by the "
            f"queued-run backstop so the send does not pile up to buffer_full. "
            f"Restart the agent's worker (managed: respawn its delivery loop / "
            f"console; resident: relaunch its *-aify wrapper), then resend."
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
        await _mirror_undeliverable_queued_run_to_sender(db, row, reason=reason)
        await _invalidate_agent_live_state(db, target_agent)
        reaped.append({"runId": run_id, "agentId": target_agent})
    return reaped


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
    cursor = await db.execute(
        """
        SELECT m.id, m.from_agent, m.to_agent, m.channel, m.type, m.subject, m.body, m.priority
        FROM messages m
        LEFT JOIN read_receipts rr ON rr.message_id = m.id AND rr.agent_id = m.to_agent
        WHERE m.source = 'channel'
          AND m.to_agent IS NOT NULL AND m.to_agent != '' AND m.to_agent != 'dashboard'
          AND m.dispatch_requested = 1
          -- `messages.timestamp` is epoch MILLISECONDS, not ISO. `datetime(1786402075333)` returns
          -- NULL, so this comparison was NULL — never true — and this reconciler could not match a
          -- single row it exists to replay. Measured on the live DB: 0 candidates under the old
          -- predicate, 115 under this one.
          --
          -- Same class as the `finished_at` guard that excluded its own target rows for two months,
          -- and the sixth lexical/epoch timestamp bug recorded in this repo. Other code already
          -- knew the shape and did it correctly (`datetime(timestamp / 1000, 'unixepoch')`), which
          -- is what makes this a copy that drifted rather than a misunderstanding.
          AND datetime(m.timestamp / 1000, 'unixepoch') >= datetime('now', ?)
          AND rr.message_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM dispatch_runs dr WHERE dr.message_id = m.id)
        ORDER BY m.timestamp ASC
        LIMIT ?
        """,
        (cutoff_param, max(1, int(limit or 200))),
    )
    rows = await cursor.fetchall()
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
