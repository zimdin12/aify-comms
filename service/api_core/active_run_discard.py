"""Discarding an active dispatch run that cannot proceed — and the four different reasons it cannot.

FOUR VERBS, ONE SUBJECT, and they are together because the distinctions between them are the whole
value. A run is discarded when it is:

    unclaimable   no bridge can claim it (the claiming bridge is gone or superseded)
    unusable      the target cannot serve it at all
    superseded    a newer bridge instance owns the agent now
    stale         it has sat past the point where claiming it could still mean anything

`_fail_pending_controls_for_run` is the shared tail: whichever reason applies, controls still waiting
on that run have to be failed or they wait forever. It is 35 lines and it is here because it is the
one thing all four paths must do.

REQUEUE IS NOT THE SAME AS FAIL, and `_discard_unclaimable_active_run` is the function that has to
know the difference — an undelivered claim whose bridge vanished should go back on the queue, not be
recorded as a failure, because nothing was ever attempted. It asks
`_requeue_instead_of_failing_undelivered_claim` (api_core/recovery_writes.py) rather than deciding
locally, and it reads `ACTIVE_RUN_BRIDGE_STALE_SECONDS` from api_core/liveness.py rather than
carrying its own threshold. Two readers of that constant only agree because there is one of it.

STATE-BASED, NOT EVENT-BASED. These key on the run's recorded state, not on some earlier cleanup call
having fired. That rule exists because a spawn once sat `running` for 97 minutes waiting on a
`report_terminal_dead` that one of ~26 terminal writers never made: cleanup that must hold for ALL
paths keys on the STATE.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction, and for a discard that matters: the caller decides whether the discard and whatever
prompted it land together.
"""

from __future__ import annotations

import time
from typing import Any

from service.api_core.events import _append_dispatch_event
from service.api_core.liveness import (
    ACTIVE_RUN_BRIDGE_STALE_SECONDS,
    _bridge_is_superseded,
)
from service.api_core.recovery_writes import _requeue_instead_of_failing_undelivered_claim
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _fail_pending_controls_for_run(
    db,
    run_id: str,
    *,
    handled_at: str,
    response_text: str,
):
    cursor = await db.execute(
        """
        SELECT id, action
        FROM dispatch_controls
        WHERE run_id = ? AND status IN ('pending', 'claimed')
        ORDER BY requested_at ASC, id ASC
        """,
        (run_id,),
    )
    controls = await cursor.fetchall()
    if not controls:
        return

    for control in controls:
        await db.execute(
            """
            UPDATE dispatch_controls
            SET status = 'failed', response_text = ?, handled_at = ?
            WHERE id = ?
            """,
            (response_text, handled_at, control["id"]),
        )
        await _append_dispatch_event(
            db,
            run_id,
            f"control:{control['action']}:failed",
            response_text,
        )


async def _discard_superseded_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    owner_bridge_id = str(active_run.get("claimBridgeId") or "").strip()
    if not owner_bridge_id or not await _bridge_is_superseded(db, owner_bridge_id, recipient_id):
        return False

    finished_at = _now()
    await db.execute(
        "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
        (
            f'Auto-healed before steer: bridge "{owner_bridge_id}" was already superseded',
            finished_at,
            active_run["runId"],
        ),
    )
    await _append_dispatch_event(
        db,
        active_run["runId"],
        "auto_heal",
        f"Steer fallback cleaned stale run owned by superseded bridge {owner_bridge_id}",
    )
    await _fail_pending_controls_for_run(
        db,
        active_run["runId"],
        handled_at=finished_at,
        response_text=f'Stale run cleaned before steer by live server path. Superseded bridge: "{owner_bridge_id}".',
    )
    return True


async def _fail_stale_active_run(
    db,
    active_run: dict[str, Any],
    *,
    reason: str,
    summary: str,
    event_body: str,
) -> bool:
    run_id = str(active_run.get("runId") or "").strip()
    if not run_id:
        return False
    if await _requeue_instead_of_failing_undelivered_claim(db, run_id, reason=reason):
        return True
    target_cursor = await db.execute("SELECT target_agent FROM dispatch_runs WHERE id = ?", (run_id,))
    target_row = await target_cursor.fetchone()
    target_agent = str((target_row["target_agent"] if target_row else "") or "").strip()
    finished_at = _now()
    await db.execute(
        "UPDATE dispatch_runs SET status = 'failed', summary = ?, error_text = ?, finished_at = ? WHERE id = ?",
        (summary, reason, finished_at, run_id),
    )
    await _append_dispatch_event(db, run_id, "auto_heal", event_body)
    await _fail_pending_controls_for_run(
        db,
        run_id,
        handled_at=finished_at,
        response_text=reason,
    )
    if target_agent:
        await _invalidate_agent_live_state(db, target_agent)
    return True


async def _discard_unclaimable_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    """Fail active runs whose owner cannot possibly consume controls anymore.

    Steer controls are only useful while the owning bridge is current and
    heartbeating. If the environment is offline or the bridge row is stale, a
    normal send would otherwise appear successful while its control sits
    unclaimed forever.
    """
    owner_bridge_id = str(active_run.get("claimBridgeId") or "").strip()
    if not owner_bridge_id:
        if str(active_run.get("dispatchMode") or "").strip().lower() != "terminal":
            return False
        started_at = str(active_run.get("startedAt") or active_run.get("requestedAt") or "").strip()
        started_epoch = _iso_to_epoch(started_at)
        if not started_epoch:
            return False
        settings = await _load_settings(db)
        stale_seconds = max(300, int(settings.get("active_run_stale_minutes", 30) or 30) * 60)
        if time.time() - started_epoch <= stale_seconds:
            return False
        return await _fail_stale_active_run(
            db,
            active_run,
            reason=f"Active run has no owning bridge and has exceeded {stale_seconds}s.",
            summary="Active run failed because no bridge owner was recorded and no reply completed before the stale-run timeout.",
            event_body="Stale unowned active run cleaned by periodic repair.",
        )
    execution_mode = str(active_run.get("executionMode") or "").strip().lower()
    channel_owned = execution_mode == "channel"

    agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
    agent = await agent_cursor.fetchone()
    runtime_state = _json_loads_or(agent["runtime_state"], {}) if agent else {}
    current_agent_bridge = str(runtime_state.get("bridgeInstanceId") or "").strip()
    environment_id = str(runtime_state.get("environmentId") or "").strip()

    if agent and _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
        if not environment_id:
            session_cursor = await db.execute(
                """
                SELECT environment_id
                FROM agent_sessions
                WHERE agent_id = ?
                ORDER BY last_seen DESC
                LIMIT 1
                """,
                (recipient_id,),
            )
            session = await session_cursor.fetchone()
            environment_id = str((session["environment_id"] if session else "") or "").strip()
        if environment_id:
            settings = await _load_settings(db)
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
            env = await env_cursor.fetchone()
            env_status = _environment_effective_status(
                env,
                offline_seconds=settings.get("environment_offline_seconds", 90),
            ) if env else "offline"
            env_bridge = str((env["bridge_id"] if env else "") or "").strip()
            if env_status not in {"online", "degraded"}:
                return await _fail_stale_active_run(
                    db,
                    active_run,
                    reason=f'Managed environment "{environment_id}" is {env_status}; active run owner bridge "{owner_bridge_id}" can no longer receive controls.',
                    summary=f'Active run failed because environment "{environment_id}" is {env_status}. Restart the environment bridge and retry.',
                    event_body=f"Stale active run cleaned before send: environment {environment_id} is {env_status}",
                )
            if env_bridge and env_bridge != owner_bridge_id and not channel_owned:
                return await _fail_stale_active_run(
                    db,
                    active_run,
                    reason=f'Active run owner bridge "{owner_bridge_id}" is not the current environment bridge "{env_bridge}".',
                    summary=f'Active run failed because bridge "{owner_bridge_id}" was replaced by "{env_bridge}". Retry after the current bridge is stable.',
                    event_body=f"Stale active run cleaned before send: {owner_bridge_id} -> {env_bridge}",
                )

    if current_agent_bridge and current_agent_bridge != owner_bridge_id and not channel_owned:
        # Scope-narrow: don't fail the run just because the agent's stored
        # bridgeInstanceId changed. With same-logical-owner re-register
        # (slice 4dbb2e2) the prior bridge stays NOT-superseded; it's still
        # a valid owner. Only fail when the owner bridge has actually been
        # superseded — that's a real ownership change.
        owner_state_cursor = await db.execute(
            "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
            (owner_bridge_id, recipient_id),
        )
        owner_state = await owner_state_cursor.fetchone()
        owner_is_superseded = bool(owner_state and str(owner_state["superseded_by"] or "").strip())
        if owner_is_superseded:
            return await _fail_stale_active_run(
                db,
                active_run,
                reason=f'Active run owner bridge "{owner_bridge_id}" is not the current agent bridge "{current_agent_bridge}".',
                summary=f'Active run failed because bridge "{owner_bridge_id}" was replaced by "{current_agent_bridge}". Retry after the current bridge is stable.',
                event_body=f"Stale active run cleaned before send: {owner_bridge_id} -> {current_agent_bridge}",
            )

    bridge_cursor = await db.execute(
        "SELECT last_seen FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (owner_bridge_id, recipient_id),
    )
    bridge = await bridge_cursor.fetchone()
    bridge_last_seen = _iso_to_epoch((bridge["last_seen"] if bridge else "") or "")
    if bridge and bridge_last_seen and time.time() - bridge_last_seen > ACTIVE_RUN_BRIDGE_STALE_SECONDS:
        return await _fail_stale_active_run(
            db,
            active_run,
            reason=f'Active run owner bridge "{owner_bridge_id}" has not heartbeated for more than {ACTIVE_RUN_BRIDGE_STALE_SECONDS}s.',
            summary=f'Active run failed because bridge "{owner_bridge_id}" stopped heartbeating. Restart the bridge and retry.',
            event_body=f"Stale active run cleaned before send: bridge heartbeat expired for {owner_bridge_id}",
        )

    return False


async def _discard_unusable_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    if await _discard_superseded_active_run(db, recipient_id, active_run):
        return True
    return await _discard_unclaimable_active_run(db, recipient_id, active_run)
