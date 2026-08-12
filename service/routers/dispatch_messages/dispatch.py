"""The `dispatch` route surface: create, claim, inspect, update and repair dispatch runs.

v0.5.2l, one half of the dispatch+messages package.

`_claim_dispatch_once` (422 lines) is the largest single body moved anywhere in this series and moves
WHOLE, byte-identical. `create_dispatch` (320) likewise. Neither is method-split here; the first
method split remains `get_analytics`, and it needs characterization tests first.

Local helpers are the ones used by dispatch handlers and nothing else. Anything shared with the
message handlers lives in `shared.py`, which also owns the borrow shims.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.liveness import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.ntfy import notify_operator
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import (
    DispatchClaimRequest,
    DispatchControlClaimRequest,
    DispatchControlRequest,
    DispatchControlUpdate,
    DispatchRequest,
    DispatchRunUpdate,
)
from service.routers.dispatch_messages.shared import (
    VALID_STATUSES,
    _NATIVE_MANAGED_RUNTIMES,
    _adopt_live_resident_driver,
    _agent_tombstone,
    _append_terminal_control,
    _auto_handoff_subject_for_run,
    _borrowed_dispatch_terminal_statuses,
    _borrowed_merged_dispatch_header,
    _borrowed_unthreaded_handoff_window_ms,
    _bridge_claim_block_reason,
    _close_reconcilable_delivered_runs,
    _close_steered_contracts_for_parent_run,
    _dispatch_conversation_context,
    _dispatch_reply_pending,
    _dispatch_reply_state,
    _has_claimable_steerable_run,
    _is_replaceable_auto_handoff_message,
    _machine_ids_same_host,
    _mark_dispatch_run_answered,
    _mark_dispatch_source_messages_read,
    _message_satisfies_reply_contract,
    _pending_dispatch_count,
    _record_channel_sidecar_heartbeat,
    _release_stale_console_owner_for_claim,
    _active_terminal_for_agent,
    _agent_execution_mode,
    _append_dispatch_control,
    _auto_return_resident_to_managed_if_possible,
    _clear_turn_busy_if_no_open_reply_owing_run,
    _coldstart_refusal_message,
    _coldstart_spawn_request_for_dispatch,
    _console_dispatch_input_body,
    _create_dispatch_runs,
    _delete_messages_by_ids,
    _delete_messages_where,
    _dispatch_fix_hint,
    _dispatch_requires_reply,
    _ensure_managed_pty_for_dispatch,
    _fail_pending_controls_for_run,
    _finalize_dispatch_runs,
    _get_blocking_active_run,
    _get_dispatch_state_for_agent,
    _get_recipient_info,
    _has_claimable_spawn_request,
    _has_live_managed_wrapper_child,
    _is_delivery_only_claude_run,
    _link_reply_message_to_dispatch_run,
    _managed_environment_unavailable_reason,
    _managed_terminal_backing_enabled,
    _managed_via_wrapper_for_runtime,
    _message_type_expects_reply,
    _mirror_missing_dispatch_handoff,
    _preflight_live_send_recipients,
    _primary_result_message_id,
    _record_terminal_delivery_contract,
    _reject_sender_truncated_body,
    _resolve_recipient_ids,
    _resolve_reply_parent_message_id,
    _run_contract_reminders_once,
    _touch_agent,
    _touch_current_agent_session,
    _turn_busy_holds_delivery,
    _wake_agent,
)
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES, _CHANNEL_MANAGED_RUNTIMES
from service.api_core.channel_delivery import (
    _apply_channel_routing_to_claude_runs,
    _insert_messages_via_console,
)

logger = logging.getLogger("aify_comms.routers.dispatch_messages.dispatch")

router = domain_router()


async def _apply_pending_resident_takeover_if_ready(db, agent_id: str) -> bool:
    # Manual ownership model: a resident CLI registration must not take over a
    # managed identity at a turn boundary. Operators use /session-mode.
    return False


async def _claim_dispatch_controls_once(req: DispatchControlClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        machine_id = req.machineId or ""
        if machine_id and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], machine_id):
            await db.rollback()
            return {"ok": True, "controls": []}

        # Claim pending controls for this agent. No filter on run status —
        # Claude resident runs complete immediately on delivery, so their
        # controls would never be claimable under the old ('claimed','running')
        # filter. The channel bridge polls for controls independently and
        # delivers them as notifications regardless of run state.
        controls_cursor = await db.execute(
            """
            SELECT dc.*, dr.target_agent, dr.status as run_status
            FROM dispatch_controls dc
            JOIN dispatch_runs dr ON dr.id = dc.run_id
            WHERE dr.target_agent = ? AND dc.status = 'pending'
              AND (? = '' OR dc.run_id = ?)
            ORDER BY dc.requested_at ASC, dc.id ASC
            LIMIT 20
            """,
            (req.agentId, req.runId or "", req.runId or "")
        )
        controls = await controls_cursor.fetchall()
        if not controls:
            await db.commit()
            return {"ok": True, "controls": []}

        claimed_at = _now()
        results = []
        for control in controls:
            await db.execute(
                "UPDATE dispatch_controls SET status = 'claimed', claim_machine_id = ?, claimed_at = ? WHERE id = ?",
                (machine_id, claimed_at, control["id"])
            )
            results.append({
                "id": control["id"],
                "runId": control["run_id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "requestedAt": control["requested_at"],
                "claimedAt": claimed_at,
            })

        await db.commit()
        return {"ok": True, "controls": results}
    finally:
        await db.close()


async def _claim_dispatch_once(req: DispatchClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        # Plan 5 (2026-05-25): settings is needed below for the
        # _agent_execution_mode call (so the wrapper-backed channel route
        # at line 1047 fires symmetrically with the dispatch-create path).
        claim_settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            tombstone = await _agent_tombstone(db, req.agentId)
            if tombstone:
                await db.rollback()
                raise HTTPException(410, f"Agent '{req.agentId}' was intentionally removed")
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        if req.machineId and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], req.machineId):
            await db.rollback()
            return {"ok": True, "run": None}

        # Phase H1 (status v2): an explicitly DISABLED agent (status "stopped")
        # must terminate its worker + polling bridge, not poll forever. Surface a
        # terminal `stopped` signal (reversible — unlike 410 removed, this does NOT
        # clear the agent's session binding) so the channel-sidecar / delivery loop
        # self-exits and the orphan terminal reaps itself.
        if str(agent["status"] or "").strip().lower() == "stopped":
            await db.commit()
            return {"ok": True, "run": None, "stopped": True}

        agent_runtime = _normalize_runtime(agent["runtime"] or "generic")

        # Mode FSM release signal (Task 4.1, 2026-05-30). A DISPLACED managed
        # sidecar (claude-channel.js / hermes-channel.js, bridgeKind="channel-sidecar")
        # must STOP driving once the operator switches the agent to resident.
        # We surface `release: true` in the claim response so the sidecar exits
        # its poll loop / goes idle. This is the one-driver invariant in action:
        # the managed driver releases so the resident TUI can take the session.
        #
        # driver_state guard (operator-reported 2026-05-31, sc-manager): the
        # original condition was the blunt `session_mode != managed`, which ALSO
        # fired for a NATIVELY-resident agent whose channel sidecar is its SOLE
        # delivery path — so every resident claude/hermes agent's sidecar was told
        # to release and queued runs never got claimed (delivery silently stalled).
        # A live resident driver has driver_state='driving' (set on resident
        # register/claim); a managed→resident switch sets driver_state='idle'
        # (the displaced managed driver, awaiting a resident takeover). Release
        # ONLY when not actively driven, so the resident delivery sidecar keeps
        # claiming for a live resident session.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") != "managed"
            and str((agent["driver_state"] if "driver_state" in agent.keys() else "") or "").strip().lower() != "driving"
        ):
            # Live resident bridge ⇒ this is the resident's OWN delivery sidecar, not a
            # displaced managed driver — adopt driving and keep claiming instead of
            # releasing (see _adopt_live_resident_driver; the sc-manager strand).
            if not await _adopt_live_resident_driver(db, agent["id"]):
                await db.commit()
                return {"ok": True, "run": None, "release": True, "sessionMode": _normalize_session_mode(agent["session_mode"] or "resident")}

        # Self-heal a superseded channel-sidecar (operator-reported 2026-05-31,
        # sc-claude). A managed agent's channel sidecar and the visible TUI's
        # managed-wrapper-child bridge legitimately COEXIST (complementary pair).
        # During managed-PTY churn the sidecar's row briefly goes stale and a
        # wrapper-child registration superseded it (the 5-min-stale clause
        # overrode the complementary-pair carve-out in _record_bridge_registration).
        # Once superseded, _bridge_claim_block_reason permanently BLOCKED the
        # still-live sidecar — and the block fires BEFORE the heartbeat upsert,
        # so it could never recover; queued channel runs were never delivered.
        # A live channel-sidecar poll is proof of life: un-supersede its OWN row
        # so it resumes claiming. The mode-FSM release above (driver_state-gated)
        # is the ONLY legitimate "stop driving" signal for a channel sidecar.
        # KEPT (Task A' #154, 2026-06-01): the 30s liveness beat does NOT revive a
        # superseded bridge (it short-circuits on superseded rows — see
        # test_liveness_beat_does_not_revive_superseded_bridge), so only this
        # claim-path self-heal can un-supersede a still-live sidecar. Removal
        # probe broke test_superseded_channel_sidecar_self_heals_on_claim.
        if str(req.bridgeKind or "").strip().lower() == "channel-sidecar" and req.bridgeId:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = '', superseded_at = NULL, last_seen = ?
                WHERE id = ? AND agent_id = ? AND COALESCE(superseded_by, '') != ''
                """,
                (_now(), req.bridgeId, req.agentId),
            )

        # Reject claims from stale stdio bridges. The bridge_instances row
        # catches normal supersession, while runtimeState.bridgeInstanceId
        # catches the more dangerous case where an old process keeps polling
        # after its bridge row has disappeared or been compacted away.
        blocked_by = await _bridge_claim_block_reason(
            db,
            bridge_id=req.bridgeId or "",
            agent_id=req.agentId,
            agent_row=agent,
            execution_modes=req.executionModes or [],
            bridge_kind_hint=req.bridgeKind or "",
        )
        if blocked_by:
            await db.commit()
            return {
                "ok": True,
                "run": None,
                "blockedBy": blocked_by,
            }

        # Update bridge liveness — the claim poll itself is the heartbeat.
        if req.bridgeId:
            is_channel_sidecar_claim = (
                str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            )
            if is_channel_sidecar_claim:
                # Task 1.6b: a standalone channel sidecar (hermes-channel.js /
                # claude-channel.js) has no bridge row until it claims a run, so
                # a plain UPDATE would no-op for an idle poller and status would
                # flap to `available`. Upsert its channel-sidecar liveness row so
                # the continuous idle poll keeps last_seen fresh and
                # `_has_live_channel_sidecar` stays true. Claude is unaffected
                # (its liveness is the wrapper PTY terminal_session) but this is
                # harmless if claude-channel.js also declares the flag.
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=req.bridgeId,
                    agent_id=req.agentId,
                    machine_id=req.machineId or "",
                    runtime=agent_runtime,
                    session_mode=agent["session_mode"] or "managed",
                    now=_now(),
                )
            else:
                await db.execute(
                    "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                    (_now(), req.bridgeId, req.agentId),
                )

        # Stale-run cleanup.
        #
        # The bridge-side gate (ACTIVE_RUNS in server.js) prevents a live
        # bridge from calling /dispatch/claim while it has work in flight.
        # That only proves the *polling* bridge is idle. Wrapper-backed
        # managed agents have two bridge identities in play: the environment
        # bridge keeps polling, while the wrapper-child bridge owns the
        # active turn. Treat a different owner as stale only after the owner
        # bridge itself stops heartbeating or is superseded.
        active_state = await _get_dispatch_state_for_agent(db, req.agentId)
        active_run = active_state.get("activeRun")
        if active_run:
            owner = (active_run.get("claimBridgeId") or "").strip()
            if owner and owner == req.bridgeId:
                await db.commit()
                return {"ok": True, "run": None, "blockedBy": active_run}
            if owner:
                owner_cursor = await db.execute(
                    """
                    SELECT last_seen, superseded_by, bridge_kind
                    FROM bridge_instances
                    WHERE id = ? AND agent_id = ?
                    """,
                    (owner, req.agentId),
                )
                owner_bridge = await owner_cursor.fetchone()
                owner_superseded_by = str((owner_bridge["superseded_by"] if owner_bridge else "") or "").strip()
                owner_last_seen = _iso_to_epoch(str((owner_bridge["last_seen"] if owner_bridge else "") or ""))
                owner_heartbeat_age = time.time() - owner_last_seen if owner_last_seen else None
                if (
                    owner_bridge
                    and not owner_superseded_by
                    and owner_heartbeat_age is not None
                    and owner_heartbeat_age < ACTIVE_RUN_BRIDGE_STALE_SECONDS
                ):
                    await db.commit()
                    return {
                        "ok": True,
                        "run": None,
                        "blockedBy": {
                            **active_run,
                            "reason": "active_run_owner_bridge_still_heartbeating",
                            "ownerBridgeId": owner,
                            "ownerBridgeKind": str(owner_bridge["bridge_kind"] or ""),
                            "currentBridgeId": req.bridgeId or "",
                            "retryAfterSeconds": max(1, int(ACTIVE_RUN_BRIDGE_STALE_SECONDS - owner_heartbeat_age)),
                            "hint": "The active run owner bridge is still heartbeating; waiting avoids killing a live wrapper-managed turn.",
                        },
                    }
            active_since = _iso_to_epoch(active_run.get("startedAt") or active_run.get("requestedAt"))
            if owner:
                stale_seconds = ACTIVE_RUN_BRIDGE_STALE_SECONDS
                wait_hint = "A previous bridge claimed this run recently. Waiting avoids killing a run that may still complete."
            else:
                stale_seconds = max(300, int(claim_settings.get("active_run_stale_minutes", 30) or 30) * 60)
                wait_hint = "An unowned terminal turn is still within its stale timeout. Waiting avoids interrupting a visible PTY turn."
            active_age = time.time() - active_since if active_since else stale_seconds + 1
            if active_age < stale_seconds:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": {
                        **active_run,
                        "reason": "active_run_owned_by_previous_bridge",
                        "ownerBridgeId": owner or "",
                        "currentBridgeId": req.bridgeId or "",
                        "retryAfterSeconds": max(1, int(stale_seconds - active_age)),
                        "hint": wait_hint,
                    },
                }
            finished_at = _now()
            owner_label = owner or "unowned"
            await db.execute(
                "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
                (
                    f'Auto-healed: bridge "{owner_label}" replaced by "{req.bridgeId}"',
                    finished_at,
                    active_run["runId"],
                ),
            )
            await _append_dispatch_event(db, active_run["runId"], "auto_heal", f"Stale run cleanup: {owner_label} -> {req.bridgeId}")
            await _fail_pending_controls_for_run(db, active_run["runId"], handled_at=finished_at, response_text=f'Stale run cleaned by live bridge "{req.bridgeId}".')
        owner_cursor = await db.execute(
            """
            SELECT id, environment_id, owner_mode, terminal_id, terminal_status
            FROM agent_sessions
            WHERE agent_id = ?
              AND status IN ('starting','running','recovering','restarting')
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (req.agentId,),
        )
        owner_session = await owner_cursor.fetchone()
        supported_modes = {str(mode or "").strip().lower() for mode in (req.executionModes or []) if str(mode or "").strip()}
        # See _CHANNEL_CLAIM_RUNTIMES and _bridge_claim_block_reason: managed
        # wrapper-backed Codex/Hermes channel runs are claimable only by the
        # wrapper PTY child bridge, not the main environment bridge.
        channel_claim = agent_runtime in _CHANNEL_CLAIM_RUNTIMES and "channel" in supported_modes
        if owner_session and str(owner_session["owner_mode"] or "").strip().lower() == "console" and not channel_claim:
            blocked_by_console = await _release_stale_console_owner_for_claim(db, owner_session, req)
            if blocked_by_console:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": blocked_by_console,
                }
        # Turn-busy claim gate: if the raw harness state says the agent is mid-turn
        # (turn_busy=1),
        # don't return queued runs. Operator-asked 2026-05-22:
        # "queue should wait until agent stops working" — without this
        # gate, the SENDER's queueIfBusy=true correctly held the run
        # in 'queued' state, but the bridge's next claim cycle picked
        # it up and delivered immediately because the claim endpoint
        # didn't respect turn_busy. The turn-end event is the authoritative clear;
        # once that fires, next claim
        # picks up the queued run as designed.
        #
        # CHANNEL/RESIDENT STEER CARVE-OUT (2026-06-02, send-deadlock fix):
        # a channel/resident-mode run to a STEER-capable target (a managed or
        # channelEnabled Claude/Hermes — `steer` in _row_capabilities, the
        # same signal used by the send-time steer path) is INJECTED into the
        # agent's native mid-turn input path; these harnesses accept multiple injects
        # in order. Deferring an ordinary send behind turn_busy was the deadlock, so when the
        # target can steer AND a claimable channel/resident run is queued, do NOT
        # defer — fall through and let it claim + inject immediately. The gate is
        # PRESERVED for every other case (non-steer-capable runtimes, managed
        # headless runs) so a genuinely-uninjectable target still waits for the
        # turn to end.
        hold_explicit_queue = False
        try:
            # Explicit queue means exactly "after this turn": key on the RAW harness signal,
            # never on derived status (that reinterpretation is what let queued sends land
            # mid-turn, #236). The ONE bound is the anti-strand ceiling — see
            # _turn_busy_holds_delivery: an abandoned turn_busy=1 that the dead-bridge sweeper
            # cannot reach (hook-owned turns, live bridge) would otherwise hold queued work
            # FOREVER, and for a target without `steer` the early return below makes that agent
            # permanently deaf while status already reports it idle.
            if await _turn_busy_holds_delivery(db, req.agentId):
                hold_explicit_queue = True
                if not await _has_claimable_steerable_run(
                    db,
                    agent_row=agent,
                    supported_modes=supported_modes,
                    agent_runtime=agent_runtime,
                ):
                    await db.commit()
                    return {"ok": True, "run": None}
        except Exception:
            # If turn state is unreadable, fall through and let the normal claim
            # flow proceed — better to deliver than block.
            pass

        run_cursor = await db.execute(
            """
            SELECT * FROM dispatch_runs
            WHERE target_agent = ? AND status = 'queued'
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT 25
            """,
            # Exclude runs already answered from the inbox before being claimed
            # (bughunt 2026-07-03): a require_reply run answered while still 'queued'
            # kept status='queued' (the answered-run reconcile only scans 'delivered'),
            # so the claimer re-woke the target to redo already-answered work.
            (req.agentId,)
        )
        runs = await run_cursor.fetchall()
        selected_run = None
        for run in runs:
            if hold_explicit_queue and (
                bool(run["queue_if_busy"]) or not bool(run["steer_if_busy"])
            ):
                continue
            run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
            if supported_modes and run_execution_mode not in supported_modes:
                continue
            if run["dispatch_mode"] == "message_only":
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (_now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", "Dispatch mode is message_only")
                continue
            requested_runtime = run["requested_runtime"] or ""
            if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
                continue

            # Plan 5 (2026-05-25): pass settings so the wrapper-backed
            # channel route (line 1047) matches what _agent_execution_mode
            # returned when the run was created. Without settings here, the
            # helper short-circuits to 'managed', then line 11258 below sees
            # run.execution_mode='channel' != 'managed' and cancels the run.
            execution_mode, reason = _agent_execution_mode(agent, requested_runtime or None, settings=claim_settings)
            if reason or not execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason or "active dispatch unavailable", _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason or "active dispatch unavailable")
                continue
            if (run["execution_mode"] or execution_mode) != execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                reason = (
                    f'Run execution mode "{run["execution_mode"] or "unknown"}" does not match the '
                    f'current capabilities of agent "{req.agentId}" ({execution_mode}).'
                )
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason, _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason)
                continue

            selected_run = run
            break

        if not selected_run:
            await db.commit()
            return {"ok": True, "run": None}

        claimed_at = _now()
        await db.execute(
            "UPDATE dispatch_runs SET status = 'claimed', claimed_at = ?, claim_machine_id = ?, claim_bridge_id = ?, runtime = ? WHERE id = ?",
            (claimed_at, req.machineId or "", req.bridgeId or "", agent_runtime, selected_run["id"])
        )
        # One-driver FSM (Task 4.1): a managed sidecar that successfully claims a
        # run for a managed agent is the live driver -> mark driving so a
        # cross-mode resident attach is rejected by the collision guard.
        if (
            str(req.bridgeKind or "").strip().lower() == "channel-sidecar"
            and _normalize_session_mode(agent["session_mode"] or "resident") == "managed"
        ):
            await db.execute(
                "UPDATE agents SET driver_state = 'driving' WHERE id = ?",
                (req.agentId,),
            )
        await _invalidate_agent_live_state(db, req.agentId)
        await _touch_current_agent_session(
            db,
            req.agentId,
            _json_loads_or(agent["runtime_state"], {}),
            claimed_at,
        )
        marked_read = await _mark_dispatch_source_messages_read(db, selected_run, req.agentId, claimed_at)
        await _append_dispatch_event(db, selected_run["id"], "claimed", f"machine={req.machineId or ''}")
        if marked_read > 1:
            await _append_dispatch_event(db, selected_run["id"], "read_receipts", f"Marked {marked_read} dispatched source messages read")
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_claimed", {"runId": selected_run["id"], "targetAgentId": req.agentId})

        return {
            "ok": True,
            "run": {
                "id": selected_run["id"],
                "messageId": selected_run["message_id"],
                "from": selected_run["from_agent"],
                "targetAgentId": selected_run["target_agent"],
                "type": selected_run["message_type"],
                "subject": selected_run["subject"],
                "body": selected_run["body"],
                "priority": selected_run["priority"],
                "inReplyTo": selected_run["in_reply_to"],
                "status": "claimed",
                "mode": selected_run["dispatch_mode"],
                "executionMode": selected_run["execution_mode"] or "managed",
                "queueIfBusy": bool(selected_run["queue_if_busy"]),
                "steerIfBusy": bool(selected_run["steer_if_busy"]),
                "runtime": agent_runtime,
                "requireReply": _row_require_reply(selected_run),
                "conversationContext": await _dispatch_conversation_context(db, selected_run),
                "claimBridgeId": req.bridgeId or "",
                "requestedRuntime": selected_run["requested_runtime"] or None,
                "claimedAt": claimed_at,
            }
        }
    finally:
        await db.close()


async def _maybe_report_async_manager_result_to_dashboard(db, row) -> Optional[str]:
    """Store manager/operator async run summaries in dashboard chat.

    The bridge already captures managed runtime final text as the run summary.
    Older running agents may not have the latest prompt/skill telling them to
    call comms_send(to="dashboard") after teammate replies arrive, so make the
    operator-visible report a backend invariant for manager-style coordinators.
    """
    if not row:
        return None
    if _row_require_reply(row):
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() == "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    if not summary:
        return None

    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not target_agent:
        return None

    event_cursor = await db.execute(
        "SELECT 1 FROM dispatch_events WHERE run_id = ? AND event_type = 'dashboard_report' LIMIT 1",
        (row["id"],),
    )
    if await event_cursor.fetchone():
        return None

    agent_cursor = await db.execute("SELECT role FROM agents WHERE id = ?", (target_agent,))
    agent_row = await agent_cursor.fetchone()
    role = str((agent_row["role"] if agent_row else "") or "").strip().lower()
    if role not in {"manager", "operator", "lead", "coordinator"}:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    if source_message_id:
        source_cursor = await db.execute("SELECT timestamp FROM messages WHERE id = ? LIMIT 1", (source_message_id,))
        source_row = await source_cursor.fetchone()
        if source_row:
            start_ms = max(start_ms, int(source_row["timestamp"] or 0))
    explicit_cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    if await explicit_cursor.fetchone():
        await _append_dispatch_event(
            db,
            row["id"],
            "dashboard_report_skipped",
            "Skipped async dashboard summary mirror because an explicit dashboard message already exists for this run window.",
        )
        return None

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = str((row["subject"] if "subject" in row.keys() else "") or "").strip()
    if subject and not subject.lower().startswith(("re:", "update:")):
        subject = f"Update: {subject}"
    elif not subject:
        subject = "Update from managed run"

    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "info",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            row["message_id"],
            ts,
        ),
    )
    await _append_dispatch_event(
        db,
        row["id"],
        "dashboard_report",
        f"Stored async manager/operator report for dashboard as {message_id}",
    )
    return message_id


async def _mirror_dashboard_run_summary_to_chat(db, row) -> Optional[str]:
    """Persist dashboard-started managed run final text as a chat reply.

    Work Loop reply debt and operator-visible chat delivery are separate
    concerns. Routine dashboard `info` asks should not become contracts, but
    their managed runtime final text still needs to land in dashboard chat.
    """
    if not row:
        return None
    if str((row["from_agent"] if "from_agent" in row.keys() else "") or "").strip() != "dashboard":
        return None
    if str((row["status"] if "status" in row.keys() else "") or "").strip().lower() != "completed":
        return None
    if str((row["result_message_id"] if "result_message_id" in row.keys() else "") or "").strip():
        return None
    if _is_delivery_only_claude_run(row):
        return None
    current_cursor = await db.execute("SELECT result_message_id FROM dispatch_runs WHERE id = ?", (row["id"],))
    current_row = await current_cursor.fetchone()
    if str((current_row["result_message_id"] if current_row else "") or "").strip():
        return None

    summary = str((row["summary"] if "summary" in row.keys() else "") or "").strip()
    target_agent = str((row["target_agent"] if "target_agent" in row.keys() else "") or "").strip()
    if not summary or not target_agent:
        return None

    start_ms = int(
        _iso_to_epoch(
            (row["started_at"] if "started_at" in row.keys() else "")
            or (row["claimed_at"] if "claimed_at" in row.keys() else "")
            or (row["requested_at"] if "requested_at" in row.keys() else "")
        )
        * 1000
    )
    source_message_id = str((row["message_id"] if "message_id" in row.keys() else "") or "").strip()
    explicit_cursor = await db.execute(
        """
        SELECT id
        FROM messages
        WHERE from_agent = ?
          AND to_agent = 'dashboard'
          AND source = 'direct'
          AND timestamp >= ?
        ORDER BY timestamp ASC, id ASC
        LIMIT 1
        """,
        (target_agent, max(0, start_ms)),
    )
    explicit = await explicit_cursor.fetchone()
    if explicit:
        message_id = str(explicit["id"] or "").strip()
        await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Linked existing dashboard reply {message_id}",
        )
        return message_id

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    subject = _auto_handoff_subject_for_run(row)
    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            target_agent,
            "dashboard",
            "direct",
            "response",
            subject,
            summary,
            row["priority"] or "normal",
            0,
            source_message_id or None,
            ts,
        ),
    )
    await db.execute("UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?", (message_id, row["id"]))
    await _append_dispatch_event(
        db,
        row["id"],
        "handoff",
        f"Stored dashboard-visible final reply as {message_id}",
    )
    return message_id


def _serialize_dispatch_run_row(row, *, blocked_by=None, include_body: bool = False, include_events=None, include_controls=None) -> dict[str, Any]:
    body_text = str((row["body"] if row and "body" in row.keys() else "") or "")
    merged_from_agents = []
    if body_text.startswith(_borrowed_merged_dispatch_header()):
        merged_from_agents = _dedupe_preserve(
            match.group(1).strip()
            for match in re.finditer(r"^From:\s*(.+)$", body_text, flags=re.MULTILINE)
            if match.group(1).strip()
        )
    payload = {
        "id": row["id"],
        "messageId": row["message_id"],
        "from": row["from_agent"],
        "originalFrom": row["from_agent"],
        "targetAgentId": row["target_agent"],
        "status": row["status"],
        "mode": row["dispatch_mode"],
        "executionMode": row["execution_mode"] or "managed",
        "runtime": row["runtime"] or "",
        "claimBridgeId": row["claim_bridge_id"] or "",
        "requestedRuntime": row["requested_runtime"] or "",
        "subject": row["subject"],
        "summary": row["summary"] or "",
        "error": row["error_text"] or "",
        "resultMessageId": row["result_message_id"] or "",
        "requireReply": _row_require_reply(row),
        "queueIfBusy": bool(row["queue_if_busy"]),
        "steerIfBusy": bool(row["steer_if_busy"]),
        "replyState": _dispatch_reply_state(row),
        "replyPending": _dispatch_reply_pending(row),
        "requestedAt": row["requested_at"],
        "claimedAt": row["claimed_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "blockedByActiveRun": blocked_by,
    }
    if len(merged_from_agents) > 1:
        payload["from"] = "multiple"
        payload["mergedFromAgents"] = merged_from_agents
        payload["mergedDispatchCount"] = _pending_dispatch_count(body_text)
    if include_body:
        payload.update(
            {
                "type": row["message_type"],
                "body": row["body"],
                "priority": row["priority"],
                "inReplyTo": row["in_reply_to"],
                "externalThreadId": row["external_thread_id"] or "",
                "externalTurnId": row["external_turn_id"] or "",
            }
        )
    if include_events is not None:
        payload["events"] = include_events
    if include_controls is not None:
        payload["controls"] = include_controls
    return payload


@router.post("/dispatch/claim")
async def claim_dispatch(req: DispatchClaimRequest, request: Request):
    # Long-poll wrapper (2026-06-30): hold the request open until work is claimable
    # or the client-requested wait elapses, instead of the bridge re-polling every 3s.
    # `_claim_dispatch_once` is the unchanged, self-contained atomic claim — calling it
    # repeatedly here is identical to the bridge calling it repeatedly over HTTP, so
    # claim/supersession/grace semantics are untouched. waitMs defaults to 0 → legacy
    # single-attempt behaviour (old bridges keep working unchanged). The per-iteration
    # fallback (3s = the legacy poll interval) bounds latency even if a notify() is missed.
    # See service/longpoll.py + DECISIONS.md "claim endpoints are long-poll".
    def _is_empty(result: dict) -> bool:
        # Keep waiting ONLY for a pure "nothing to do" result. Any actionable signal
        # (a claimed run, or a stopped/release/blockedBy directive) returns immediately.
        return (
            result.get("run") is None
            and not result.get("stopped")
            and not result.get("release")
            and not result.get("blockedBy")
        )

    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_once(req, request),
        _is_empty,
        scope="dispatch",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "run": None},
    )


@router.post("/dispatch/controls/claim")
async def claim_dispatch_controls(req: DispatchControlClaimRequest, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only while the
    # controls list is exactly empty; any pending control returns immediately.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_controls_once(req, request),
        lambda r: r.get("controls") == [],
        scope="control",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "controls": []},
    )


@router.post("/dispatch")
async def create_dispatch(req: DispatchRequest, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    _reject_sender_truncated_body(req.body)
    if req.mode == "message_only":
        raise HTTPException(400, "Dispatch no longer supports mode='message_only'. Use comms_send for normal live messaging or comms_dispatch without message_only for tracked work.")

    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; dispatch was sent unthreaded.'
            )
        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": [], "runs": []}

        not_started = []
        launchable_recipients = []
        console_recipients = {}
        recipient_rows = {}
        settings = await _load_settings(db)
        for recipient_id in recipients:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            row = await cursor.fetchone()
            if row:
                row, _transition = await _auto_return_resident_to_managed_if_possible(db, row, settings=settings)
            if row:
                recipient_rows[recipient_id] = row
                # Plan 2 (2026-05-25) pi flip: reject new dispatches to a pi
                # agent that's currently mid-flip from resident -> managed.
                # _drain_and_flip_pi_resident_agents will migrate it once
                # any active runs drain (~5s). The operator should retry
                # after the flip completes. Without this gate, dispatches
                # queue against a session_mode the runtime no longer
                # supports.
                if _normalize_runtime(row["runtime"] or "") == "pi":
                    _rs = _json_loads_or(row["runtime_state"], {})
                    if _rs.get("pi_resident_pending_flip"):
                        raise HTTPException(
                            409,
                            f'Agent "{recipient_id}" is migrating from resident '
                            f"to managed (pi flip pending). Retry in a few "
                            f"seconds — the drain loop will flip the agent "
                            f"once any active runs complete."
                        )
            execution_mode = None
            reason = None if row else "agent is not registered"
            if row:
                runtime = _normalize_runtime(row["runtime"] or "generic")
                if req.requestedRuntime and _normalize_runtime(req.requestedRuntime) != runtime:
                    reason = f'requested runtime "{req.requestedRuntime}" does not match registered runtime "{runtime}"'
                elif runtime in _NATIVE_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings so
                    # _agent_execution_mode can detect wrapper-backed managed
                    # runtimes (managed_via_wrapper) and return
                    # execution_mode='channel'. Without settings the helper
                    # short-circuits to 'managed' (line 1065) and the
                    # wrapper-backed dispatch path never fires.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    # Plan 5 follow-up (2026-05-26): the PTY-input
                    # (console_recipients) downgrade below MUST only fire
                    # for execution_mode='managed'. When the helper returns
                    # 'channel' for wrapper-backed codex/hermes, leave
                    # the run as channel-mode so the wrapper PTY's child
                    # bridge can pick it up. Falling through to
                    # console_recipients would route the message via raw PTY
                    # keystrokes — the scrambled-text failure mode the
                    # operator explicitly banned.
                    if (
                        not reason
                        and execution_mode == "channel"
                        and _managed_terminal_backing_enabled(settings)
                        and _managed_via_wrapper_for_runtime(settings, runtime)
                    ):
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if not console_terminal:
                            reason = f"Managed {runtime} wrapper PTY is unavailable; recover or restart the environment-managed session."
                    if not reason and execution_mode == "managed":
                        if (
                            _managed_terminal_backing_enabled(settings)
                            and _insert_messages_via_console(settings)
                            and runtime not in {"pi", "opencode"}
                        ):
                            console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                            if not console_terminal:
                                console_terminal = await _ensure_managed_pty_for_dispatch(
                                    db,
                                    recipient_id,
                                    runtime=runtime,
                                    settings=settings,
                                    requested_by=req.from_agent,
                                )
                            if console_terminal:
                                console_recipients[recipient_id] = console_terminal
                                execution_mode = None
                            else:
                                reason = await _managed_environment_unavailable_reason(db, row)
                elif runtime in _CHANNEL_MANAGED_RUNTIMES:
                    # Plan 5 (2026-05-25): pass settings (parity with the
                    # NATIVE_MANAGED branch above). _agent_execution_mode
                    # uses settings to gate the wrapper-backed channel route.
                    execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                    if not reason and execution_mode == "channel":
                        reason = await _managed_environment_unavailable_reason(db, row)
                    if not reason and execution_mode == "channel" and _insert_messages_via_console(settings):
                        # PTY-input path — only the opt-in via-console
                        # delivery mode goes through here. Default-false
                        # routing leaves the run launchable and the post-
                        # create _apply_channel_routing_to_claude_runs
                        # flips execution_mode='channel' so claude-channel.js
                        # claims it.
                        console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                        if not console_terminal:
                            console_terminal = await _ensure_managed_pty_for_dispatch(
                                db,
                                recipient_id,
                                runtime=runtime,
                                settings=settings,
                                requested_by=req.from_agent,
                            )
                        if console_terminal:
                            console_recipients[recipient_id] = console_terminal
                            execution_mode = None
                        else:
                            reason = "Claude claude-aify backing PTY is unavailable; restart the environment bridge or recover the session."
                    elif reason:
                        execution_mode = None
                else:
                    console_terminal = await _active_terminal_for_agent(db, recipient_id, settings=settings)
                    if not console_terminal:
                        console_terminal = await _ensure_managed_pty_for_dispatch(
                            db,
                            recipient_id,
                            runtime=runtime,
                            settings=settings,
                            requested_by=req.from_agent,
                        )
                    if console_terminal:
                        console_recipients[recipient_id] = console_terminal
                    else:
                        # Plan 5 (2026-05-25): pass settings — see sibling
                        # branches above for rationale.
                        execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime, settings=settings)
                        if not reason and execution_mode:
                            reason = await _managed_environment_unavailable_reason(db, row)
            if reason or not execution_mode:
                if recipient_id not in console_recipients:
                    not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
            else:
                launchable_recipients.append((recipient_id, execution_mode))

        if req.mode == "require_start" and not_started:
            details = "; ".join(f"{item['targetAgentId']}: {item['reason']}" for item in not_started)
            return {
                "ok": False,
                "error": f"Active dispatch unavailable for: {details}",
                "recipients": recipients,
                "runs": [],
                "notStarted": not_started,
            }

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        source_message_ids = {}
        ts = int(time.time() * 1000)
        for recipient_id in recipients:
            recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
            source_message_ids[recipient_id] = recipient_message_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    recipient_message_id,
                    req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                    req.priority, 0 if recipient_id in console_recipients else 1, resolved_in_reply_to, ts
                )
            )
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=_primary_result_message_id(message_id, recipients),
                reply_type=req.type,
                reply_body=req.body,
            )

        runs = []
        if launchable_recipients:
            require_reply = _dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type))
            runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode=req.mode,
                execution_mode="managed",
                requested_runtime=req.requestedRuntime,
                message_id=message_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=req.steer,
                require_reply=require_reply,
            )
            runs = await _finalize_dispatch_runs(db, runs, launchable_recipients, not_started)
            await _apply_channel_routing_to_claude_runs(db, runs, settings)

        console_deliveries = []
        for recipient_id, terminal in console_recipients.items():
            terminal_id = str(terminal["terminal_id"] or "").strip()
            recipient_message_id = source_message_ids.get(recipient_id, message_id)
            terminal_runtime = _normalize_runtime(terminal["runtime"] or "")
            control_id = await _append_terminal_control(
                db,
                terminal_id=terminal_id,
                environment_id=terminal["environment_id"],
                bridge_id=terminal["bridge_id"] or "",
                action="input",
                requested_by=req.from_agent,
                body=_console_dispatch_input_body(
                    req,
                    recipient_id=recipient_id,
                    message_id=recipient_message_id,
                    bracketed_paste=True,
                ),
            )
            submit_control_id = ""
            await _append_terminal_event(
                db,
                terminal_id,
                "terminal_input_requested",
                json.dumps({
                    "requestedBy": req.from_agent,
                    "controlId": control_id,
                    "submitControlId": submit_control_id,
                    "source": "dispatch",
                    "messageId": recipient_message_id,
                }),
            )
            contract_run_id = await _record_terminal_delivery_contract(
                db,
                source_message_id=recipient_message_id,
                from_agent=req.from_agent,
                recipient_id=recipient_id,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                require_reply=_dispatch_requires_reply(req.requireReply, default=_message_type_expects_reply(req.type)),
                terminal_id=terminal_id,
                control_id=control_id,
                runtime=terminal["runtime"] or "",
            )
            console_deliveries.append({
                "targetAgentId": recipient_id,
                "terminalId": terminal_id,
                "controlId": control_id,
                "contractRunId": contract_run_id,
                "status": "sent_to_console",
            })

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        # Wake any long-polling claim waiters now that work is committed and visible.
        # A new run was queued and/or a steer control / console delivery appended;
        # over-notifying is harmless (a woken waiter that finds nothing re-sleeps).
        longpoll.notify("dispatch")
        longpoll.notify("control")
        if console_deliveries:
            longpoll.notify("terminal-control")
        ws = await _get_ws(request)
        if ws:
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "dispatch_request", {"from": req.from_agent, "subject": req.subject})
            for run in runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
            for delivery in console_deliveries:
                await ws.broadcast("terminal_control_requested", {"terminalId": delivery["terminalId"], "action": "input"})
        for recipient_id in recipients:
            _wake_agent(recipient_id)

        return {
            "ok": True,
            "messageId": message_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "runs": runs,
            "notStarted": not_started,
            "consoleDeliveries": console_deliveries,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}")
async def get_dispatch_run(run_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")
        ec = await db.execute(
            "SELECT event_type, body, created_at FROM dispatch_events WHERE run_id = ? ORDER BY id ASC LIMIT 200",
            (run_id,)
        )
        events = [
            {"type": event["event_type"], "body": event["body"], "createdAt": event["created_at"]}
            for event in await ec.fetchall()
        ]
        cc = await db.execute(
            """
            SELECT id, from_agent, action, body, status, response_text, source_message_id, requested_at, claimed_at, handled_at
            FROM dispatch_controls WHERE run_id = ? ORDER BY requested_at ASC LIMIT 200
            """,
            (run_id,)
        )
        controls = [
            {
                "id": control["id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "status": control["status"],
                "response": control["response_text"],
                "sourceMessageId": control["source_message_id"],
                "requestedAt": control["requested_at"],
                "claimedAt": control["claimed_at"],
                "handledAt": control["handled_at"],
            }
            for control in await cc.fetchall()
        ]
        blocked_by = None
        if row["status"] == "queued":
            blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
        return {
            "run": _serialize_dispatch_run_row(
                row,
                blocked_by=blocked_by,
                include_body=True,
                include_events=events,
                include_controls=controls,
            )
        }
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}/events")
async def list_dispatch_run_events(
    run_id: str,
    limit: int = Query(50, ge=1),
    before: Optional[int] = Query(None, ge=1),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    db = await get_db()
    try:
        run_cursor = await db.execute("SELECT 1 FROM dispatch_runs WHERE id = ?", (run_id,))
        if not await run_cursor.fetchone():
            raise HTTPException(404, f"Run '{run_id}' not found")

        bounded_limit = min(limit, 50)
        params: list[Any] = [run_id]
        cursor_clause = ""
        direction = "DESC" if order == "desc" else "ASC"
        if before is not None:
            cursor_clause = "AND id < ?" if order == "desc" else "AND id > ?"
            params.append(before)
        params.append(bounded_limit + 1)
        events_cursor = await db.execute(
            f"""
            SELECT id, event_type, body, created_at
            FROM dispatch_events
            WHERE run_id = ? {cursor_clause}
            ORDER BY id {direction}
            LIMIT ?
            """,
            tuple(params),
        )
        rows = await events_cursor.fetchall()
        page = rows[:bounded_limit]
        return {
            "ok": True,
            "runId": run_id,
            "events": [
                {
                    "id": str(event["id"]),
                    "type": event["event_type"],
                    "eventType": event["event_type"],
                    "body": event["body"] or "",
                    "createdAt": event["created_at"],
                }
                for event in page
            ],
            "hasMore": len(rows) > bounded_limit,
            "nextBefore": str(page[-1]["id"]) if len(rows) > bounded_limit and page else "",
            "order": order,
            "limit": bounded_limit,
        }
    finally:
        await db.close()


@router.get("/dispatch/runs")
async def list_dispatch_runs(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        # Read-path-write fix (2026-06-29): _repair_unusable_active_runs scanned the runs table on
        # every poll of this endpoint and is already run by the 60s reconcile loop — removed here so
        # this stays a pure read (no write-txn contention on the single writer).
        # Plan 6 follow-up (2026-05-26): Section C's mode-switch audit
        # inserts synthetic `dispatch_runs` rows with dispatch_mode='audit'
        # to satisfy the dispatch_events.run_id FK constraint. Those rows
        # are never claimed/queued/started — they exist only as audit
        # anchors. Hide them from the listing endpoint so the dashboard's
        # dispatch history view doesn't fill with mode_switch_* entries.
        # Audit anchors are still queryable individually via the
        # per-id endpoint and via dispatch_events.
        query = "SELECT * FROM dispatch_runs WHERE (dispatch_mode IS NULL OR dispatch_mode != 'audit')"
        params = []
        if agentId:
            query += " AND target_agent = ?"
            params.append(agentId)
        if fromAgent:
            query += " AND from_agent = ?"
            params.append(fromAgent)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        # Perf (audit 2026-06-28): batch the per-run source-controls lookup into ONE query keyed
        # by all run_ids on the page, instead of a sub-query per row (was ~80 queries/poll on the
        # dashboard's 15s cycle → 1). Read-only; output is identical (same fields, ASC order, the
        # per-run 50 cap is applied in Python below).
        run_ids = [row["id"] for row in rows]
        controls_by_run: dict[str, list[dict[str, Any]]] = {}
        if run_ids:
            placeholders = ",".join("?" * len(run_ids))
            controls_cursor = await db.execute(
                f"""
                SELECT run_id, id, action, status, source_message_id, response_text
                FROM dispatch_controls
                WHERE run_id IN ({placeholders}) AND source_message_id != ''
                ORDER BY requested_at ASC
                """,
                run_ids,
            )
            for control in await controls_cursor.fetchall():
                controls_by_run.setdefault(control["run_id"], []).append({
                    "id": control["id"],
                    "action": control["action"],
                    "status": control["status"],
                    "sourceMessageId": control["source_message_id"],
                    "response": control["response_text"] or "",
                })
        runs = []
        for row in rows:
            blocked_by = None
            if row["status"] == "queued":
                blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
            payload = _serialize_dispatch_run_row(row, blocked_by=blocked_by)
            source_controls = controls_by_run.get(row["id"], [])[:50]
            if source_controls:
                payload["sourceControls"] = source_controls
            runs.append(payload)
        return {"runs": runs}
    finally:
        await db.close()


@router.post("/dispatch/handoffs/repair")
async def repair_dispatch_handoffs(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        closed_delivered = await _close_reconcilable_delivered_runs(db, limit=limit)
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT ?
            """,
            (max(1, limit - len(closed_delivered)),),
        )
        rows = await cursor.fetchall()
        mirrored = []
        dashboard_reports = []
        skipped_delivery_only = 0
        skipped = 0
        for row in rows:
            if _is_delivery_only_claude_run(row):
                skipped_delivery_only += 1
                continue
            message_id = await _mirror_missing_dispatch_handoff(db, row)
            if message_id:
                mirrored.append({"runId": row["id"], "messageId": message_id})
            else:
                skipped += 1

        report_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 0
              AND status = 'completed'
              AND COALESCE(summary, '') != ''
            ORDER BY requested_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        report_rows = await report_cursor.fetchall()
        for row in report_rows:
            message_id = await _maybe_report_async_manager_result_to_dashboard(db, row)
            if message_id:
                dashboard_reports.append({"runId": row["id"], "messageId": message_id})

        await db.commit()
        ws = await _get_ws(request)
        if ws and (mirrored or dashboard_reports or closed_delivered):
            await ws.broadcast(
                "dispatch_handoffs_repaired",
                {"mirrored": len(mirrored), "dashboardReports": len(dashboard_reports), "closedDelivered": len(closed_delivered)},
            )
        return {
            "ok": True,
            "mirrored": len(mirrored),
            "dashboardReports": len(dashboard_reports),
            "closedDelivered": len(closed_delivered),
            "skippedDeliveryOnly": skipped_delivery_only,
            "skipped": skipped,
            "runs": mirrored,
            "reports": dashboard_reports,
            "closed": closed_delivered,
        }
    finally:
        await db.close()


@router.post("/dispatch/runs/{run_id}/control")
async def request_dispatch_control(run_id: str, req: DispatchControlRequest, request: Request):
    action = (req.action or "").strip().lower()
    if action not in {"interrupt", "steer"}:
        raise HTTPException(400, "Unsupported control action")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(404, f"Run '{run_id}' not found")
        if run["status"] not in {"claimed", "running"}:
            raise HTTPException(409, f"Run '{run_id}' is not active")

        control_id = await _append_dispatch_control(
            db,
            run_id,
            from_agent=req.from_agent or "",
            action=action,
            body=req.body or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_requested", {"runId": run_id, "controlId": control_id, "action": action})
        return {"ok": True, "controlId": control_id, "runId": run_id, "action": action, "status": "pending"}
    finally:
        await db.close()


@router.patch("/dispatch/controls/{control_id}")
async def update_dispatch_control(control_id: str, req: DispatchControlUpdate, request: Request):
    if req.status not in {"completed", "failed"}:
        raise HTTPException(400, "Unsupported control status")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        control = await cursor.fetchone()
        if not control:
            raise HTTPException(404, f"Control '{control_id}' not found")

        handled_at = _now()
        await db.execute(
            "UPDATE dispatch_controls SET status = ?, response_text = ?, handled_at = ? WHERE id = ?",
            (req.status, req.response or "", handled_at, control_id)
        )
        if req.status == "completed" and (control["source_message_id"] or "").strip():
            run_cursor = await db.execute(
                "SELECT target_agent FROM dispatch_runs WHERE id = ?",
                (control["run_id"],),
            )
            run = await run_cursor.fetchone()
            if run and (run["target_agent"] or "").strip():
                msg_cursor = await db.execute(
                    "SELECT 1 FROM messages WHERE id = ?",
                    ((control["source_message_id"] or "").strip(),),
                )
                if await msg_cursor.fetchone():
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        ((control["source_message_id"] or "").strip(), run["target_agent"], handled_at),
                    )
        await _append_dispatch_event(
            db,
            control["run_id"],
            f"control:{control['action']}:{req.status}",
            req.response or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_updated", {"controlId": control_id, "status": req.status})
        return {"ok": True, "controlId": control_id, "status": req.status}
    finally:
        await db.close()


@router.patch("/dispatch/runs/{run_id}")
async def update_dispatch_run(run_id: str, req: DispatchRunUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")

        updates = []
        params = []
        now = _now()
        current_status = str(row["status"] or "").strip().lower()
        requested_status = str(req.status or "").strip().lower()
        effective_status = req.status
        if current_status in _borrowed_dispatch_terminal_statuses() and requested_status != current_status:
            effective_status = None

        if effective_status:
            updates.append("status = ?")
            params.append(effective_status)
            if effective_status == "running" and not row["started_at"]:
                updates.append("started_at = ?")
                params.append(now)
            if effective_status in _borrowed_dispatch_terminal_statuses():
                updates.append("finished_at = ?")
                params.append(now)
        if req.summary is not None:
            updates.append("summary = ?")
            params.append(req.summary)
        if req.error is not None:
            updates.append("error_text = ?")
            params.append(req.error)
        if req.resultMessageId is not None:
            normalized_result_message_id = str(req.resultMessageId or "").strip()
            if normalized_result_message_id or not str(row["result_message_id"] or "").strip():
                updates.append("result_message_id = ?")
                params.append(normalized_result_message_id)
        if req.externalThreadId is not None:
            updates.append("external_thread_id = ?")
            params.append(req.externalThreadId)
        if req.externalTurnId is not None:
            updates.append("external_turn_id = ?")
            params.append(req.externalTurnId)
        if req.runtime is not None:
            updates.append("runtime = ?")
            params.append(req.runtime)
        if req.requireReply is not None:
            updates.append("require_reply = ?")
            params.append(1 if req.requireReply else 0)

        if updates:
            params.append(run_id)
            await db.execute(f"UPDATE dispatch_runs SET {', '.join(updates)} WHERE id = ?", params)
            await _invalidate_agent_live_state(db, row["target_agent"])
            if effective_status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{effective_status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                mirrored_message_id = await _mirror_missing_dispatch_handoff(db, refreshed_row)
                dashboard_message_id = await _mirror_dashboard_run_summary_to_chat(db, refreshed_row)
                result_message_id = str((refreshed_row["result_message_id"] if refreshed_row else "") or mirrored_message_id or dashboard_message_id or "").strip()
                await _close_steered_contracts_for_parent_run(
                    db,
                    refreshed_row,
                    result_message_id=result_message_id,
                )
                await _maybe_report_async_manager_result_to_dashboard(db, refreshed_row)
                if refreshed_row:
                    # Send-deadlock fix (2026-06-02): an rr=0 channel/resident
                    # delivery that the bridge just marked completed is NOT
                    # sustained work — clear the recipient's turn_busy (which the
                    # delivery re-pulse left stamped) so a queued send isn't held
                    # behind a phantom turn for up to 120s. rr=1 runs keep their
                    # turn_busy and clear via _mark_dispatch_run_answered when the
                    # reply lands; the guard ensures we never clear while another
                    # rr=1 turn is still open (anti-feedback-loop invariant).
                    if (
                        effective_status == "completed"
                        and not _row_require_reply(refreshed_row)
                        and str((refreshed_row["execution_mode"] or "")).strip().lower() in {"channel", "resident"}
                    ):
                        await _clear_turn_busy_if_no_open_reply_owing_run(
                            db, refreshed_row["target_agent"], run_id
                        )
                    await _apply_pending_resident_takeover_if_ready(db, refreshed_row["target_agent"])
                    if effective_status == "completed":
                        await _run_contract_reminders_once(
                            db,
                            request=request,
                            target_agent_id=refreshed_row["target_agent"],
                            limit=25,
                            recent_only=True,
                        )

        # MC1 (2026-06-06): only persist a status that is in the 8-status vocabulary.
        # Delivery PATCHes historically sent a non-vocab agentStatus:"active", which got
        # written raw into agents.status and leaked to the dashboard as a 9th status. Now an
        # out-of-vocab value is ignored (status is DERIVED from turn/liveness signals anyway);
        # only an explicit valid operator/runtime status is written. last_seen still refreshes.
        if req.agentStatus and req.agentStatus in VALID_STATUSES:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
                (req.agentStatus, now, row["target_agent"])
            )
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            await _touch_current_agent_session(
                db,
                row["target_agent"],
                _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {},
                now,
            )

        if req.appendEvent:
            await _append_dispatch_event(db, run_id, req.eventType or "info", req.appendEvent)

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_updated", {"runId": run_id, "status": effective_status or row["status"]})
        return {"ok": True, "runId": run_id}
    finally:
        await db.close()
