"""Queued runs nobody can deliver: deciding that, and telling the sender.

RELOCATED from `service/reconcilers/dispatch_queue.py` in v0.5.4, byte-identical. Three functions
that call only each other — is there a live claimer, mirror the bad news back to the sender, and the
reaper that uses both. Nothing outside the module called the first two; only the reaper is imported
elsewhere.

THE MIRROR IS THE POINT, not the reap. Failing a queued run is easy; the failure that this code
exists to prevent is a SILENT one — a sender that asked for work, got a run id, and then never heard
anything because the run was quietly failed by a sweep it cannot see. `_mirror_undeliverable_queued_-
run_to_sender` is what makes the reap visible in the conversation that started it.

`_agent_has_live_claimer` IS DELIBERATELY GENEROUS. It counts a live claimer lease, a recorded lease,
a fresh resident bridge, a channel sidecar and a pending-or-booting spawn — because every false
positive here fails work that would have run. Being slow to reap costs a delay; being wrong costs the
message.

DB ACCESS: `db` is passed in and the CALLER commits — `sweep.py` wraps each step in `_commit_step`.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from service.api_core.channel_delivery import _CHANNEL_SIDECAR_DELIVERY_RUNTIMES
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
from service.api_core.events import _append_dispatch_event
from service.api_core.liveness import _has_live_claimer_lease, _has_recorded_claimer_lease
from service.api_core.live_process_probes import (
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
    _has_live_channel_sidecar,
    _resident_bridge_is_fresh,
)
from service.api_core.managed_env import _has_pending_or_booting_spawn_request
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


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
