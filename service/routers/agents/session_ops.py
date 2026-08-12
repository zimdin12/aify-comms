"""Session lifecycle OPERATIONS: keep, confirm, resident-lost, stop-worker, control.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.session_ops")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentControlRequest, AgentResidentLostRequest, AgentSessionResolveRequest

from service.routers.agents.shared import (  # noqa: F401
    DEFAULT_SETTINGS,
    LIVE_SESSION_STATUSES,
    _SESSION_MODES,
    _active_terminal_for_agent,
    _adopt_live_resident_driver,
    _agent_liveness,
    _agent_record_to_dict,
    _agent_session_to_dict,
    _agent_tombstone,
    _append_dispatch_control,
    _append_dispatch_event,
    _append_terminal_control,
    _append_terminal_event,
    _apply_status_event,
    _auto_return_resident_to_managed_if_possible,
    _borrowed_channel_claim_runtimes,
    _borrowed_console_tail_max_bytes,
    _borrowed_console_tail_max_lines,
    _borrowed_list_agents_refresh_limit,
    _borrowed_listen_events,
    _borrowed_live_session_statuses,
    _borrowed_manual_statuses,
    _borrowed_reap_triad_body_sentinel,
    _borrowed_runtime_config_live_keys,
    _borrowed_shell_placeholder_handle_re,
    _borrowed_terminal_end_statuses,
    _borrowed_terminal_output_writes,
    _borrowed_virtual_pi_rpc_command,
    _borrowed_virtual_rpc_commands_by_runtime,
    _borrowed_windows_drive_cwd_re,
    _borrowed_wsl_drive_cwd_re,
    _broadcast_agent_status,
    _broadcast_engine_status,
    _clear_status_state_in_turn,
    _coldstart_refusal_message,
    _coldstart_spawn_request_for_dispatch,
    _compute_agent_status,
    _compute_live_status_cache,
    _default_capabilities_for,
    _enforce_env_reachable_gate,
    _enforce_live_worker_gate,
    _ensure_managed_pty_for_dispatch,
    _environment_effective_status,
    _environment_record_to_dict,
    _fail_active_runs_for_superseded_bridges,
    _fresh_same_mode_bridge_conflict,
    _get_blocking_active_run,
    _get_dispatch_state_for_agent,
    _get_dispatch_state_map,
    _get_outbound_activity_map,
    _get_unread_count_map,
    _get_ws,
    _has_codex_live_app_server,
    _has_live_terminal_session,
    _has_pending_or_booting_spawn_request,
    _invalidate_agent_live_state,
    _is_lock_error,
    _iso_to_epoch,
    _json_loads_or,
    _live_state_get,
    _load_settings,
    _machine_family,
    _managed_owning_environment_row,
    _managed_via_wrapper_for_runtime,
    _merge_runtime_policy_for_wrapper_reregister,
    _normalize_machine_id,
    _normalize_runtime,
    _normalize_session_mode,
    _now,
    _record_bridge_registration,
    _record_channel_sidecar_heartbeat,
    _record_claimer_lease,
    _refresh_expired_agent_live_states,
    _remove_agent_record,
    _render_live_terminal_screen,
    _render_terminal_snapshot,
    _repair_unusable_active_runs,
    _request_stop_agent_terminals,
    _resolve_live_console_terminal,
    _resume_command_for,
    _row_status_note,
    _runtime_capability_for_environment,
    _runtime_handle_from_state,
    _runtime_state_replacing_handle,
    _runtime_state_with_handle,
    _sanitize_session_handle,
    _session_capabilities_replacing_handle,
    _session_handle_live_owner,
    _stop_virtual_terminals_for_superseded_bridges,
    _synth_terminal_should_be_created,
    _terminal_failure_line,
    _terminal_failure_tail,
    _terminal_session_to_dict,
    _timestamp_sort_key,
    _touch_current_agent_session,
    _upsert_resident_agent_session,
    _validate_registration_cwd,
    _workspace_for_environment,
    apply_event,
    derive,
    engine_status,
    get_db,
    logger,
    re,
    sqlite3,
    validate_name,
)

router = domain_router()


@router.post("/agents/{agent_id}/control")
async def control_agent(agent_id: str, req: AgentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"interrupt", "stop", "resume", "start"}:
        raise HTTPException(400, f'Unsupported agent control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        now = _now()

        # START (2026-07-14). A managed agent with NO session row could not be started from the
        # dashboard at all: the Console tab returns early on "no session" (above the start
        # buttons, which all need a session id), so the only way to bring one up was to send it a
        # message and hope. Operator: "why can't I start hermes models?" — the cold-start itself
        # was never broken; there was simply no button.
        #
        # This is the SAME mechanism the send path uses (_coldstart_spawn_request_for_dispatch):
        # create a spawn request, a bridge claims it, registers a session and brings the worker
        # up — resuming the agent's saved session handle when it has one, which for the hermes
        # coders means their existing conversation (lc-coder alone is 12,780 messages).
        if action == "start":
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" is resident — its terminal is the CLI you launched, '
                    "not a dashboard-owned worker. Switch it to managed to start one from here.",
                )
            # ALLOWLIST, never a blocklist (fixed 2026-07-26). This gate used to be
            # `status NOT IN ('stopped','failed','ended','cancelled')`, which silently treats
            # every status NOT on that list as LIVE. `lost` is not on it — so an agent whose
            # worker was lost months ago read as "already running" forever: Start returned
            # alreadyRunning, no spawn request was ever created, the agent stayed `available`,
            # and clicking again just repeated the toast. Live-reproduced on the whole ef- team
            # (ef-manager / ef-coder-lead / ef-tech-lead / ef-tester — four sessions stuck
            # `lost` with ended_at 2026-04-30), which were permanently unstartable from the
            # dashboard. Note the asymmetry that made it invisible: derive() correctly reported
            # `available` off real liveness, so status and this gate disagreed.
            #
            # Use the canonical live sets instead, so a new session status can never silently
            # mean "live" here again. The union of both is deliberate: LIVE_SESSION_STATUSES is
            # the session-row set the reconcilers use, _borrowed_live_session_statuses() the narrower
            # status-engine set that also covers restarting/cli-takeover. A row must ALSO not be
            # marked ended — a live status with ended_at set is a stale row the reconcilers heal,
            # and trusting it would re-create exactly this permanent block.
            _start_live_statuses = sorted(
                {s.lower() for s in LIVE_SESSION_STATUSES}
                | {s.lower() for s in _borrowed_live_session_statuses()}
            )
            _live_ph = ",".join("?" for _ in _start_live_statuses)
            live = await (await db.execute(
                f"""
                SELECT id FROM agent_sessions
                WHERE agent_id = ?
                  AND LOWER(COALESCE(status,'')) IN ({_live_ph})
                  AND COALESCE(ended_at,'') = ''
                LIMIT 1
                """,
                (agent_id, *_start_live_statuses),
            )).fetchone()
            if live:
                # Already running — starting again would spawn a duplicate worker.
                return {"ok": True, "agentId": agent_id, "action": "start", "alreadyRunning": True}
            settings = await _load_settings(db)
            started = await _coldstart_spawn_request_for_dispatch(
                db,
                agent_id,
                runtime=_normalize_runtime(agent["runtime"] or ""),
                settings=settings,
                requested_by=req.from_agent or "dashboard",
            )
            await db.commit()
            if not started:
                # _coldstart returns False for an already-pending/booting spawn too (idempotent
                # success, not a failure). Clicking Start twice during a slow boot — before the
                # session row exists — must not surface a false "no environment bridge" error.
                if await _has_pending_or_booting_spawn_request(db, agent_id):
                    return {"ok": True, "agentId": agent_id, "action": "start", "spawnPending": True}
                raise HTTPException(
                    409,
                    f'Could not start "{agent_id}" — no environment bridge is available to run it. '
                    "Start one on its host with `aify-comms`.",
                )
            await _invalidate_agent_live_state(db, agent_id)
            return {"ok": True, "agentId": agent_id, "action": "start", "spawnRequested": True}
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if action in {"interrupt", "stop"}:
            if active_run:
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=req.from_agent or "dashboard",
                    action="interrupt",
                    body=req.body or f"Agent {action} requested from dashboard.",
                )
            elif action == "interrupt":
                raise HTTPException(409, f'Agent "{agent_id}" has no active run to interrupt')

        cancelled_queued = 0
        if action == "stop":
            queued_cursor = await db.execute(
                "SELECT id FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                (agent_id,),
            )
            queued_rows = await queued_cursor.fetchall()
            for row in queued_rows:
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                    (f'Agent "{agent_id}" was stopped from the dashboard before the run could start.', now, row["id"]),
                )
                await _append_dispatch_event(db, row["id"], "agent_stopped", "Agent stopped from dashboard")
                cancelled_queued += 1
            stop_note = "Stopped from dashboard. Resume to allow wake/dispatch again."
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                stop_note = "Resident session stop requested from dashboard; live bridge should terminate the CLI host."
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ?
                WHERE id = ?
                """,
                (stop_note, now, agent_id),
            )
            # Kill the managed console/TUI too — aify-comms is the lifecycle driver
            # for managed sessions, so Stop must tear down the running terminal
            # instead of leaving an abandoned TUI (operator-reported 2026-05-31).
            # Resident windows are the operator's OWN process; the bridge teardown
            # handles those (see stop_note), so this is managed-only.
            if _normalize_session_mode(agent["session_mode"] or "resident") == "managed":
                await _request_stop_agent_terminals(
                    db, agent_id, requested_by=req.from_agent or "dashboard", now=now,
                )
        elif action == "resume":
            await db.execute(
                """
                UPDATE agents
                SET status = 'idle', status_note = '', launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_control_requested",
                {"agentId": agent_id, "action": action, "controlId": control_id, "cancelledQueued": cancelled_queued},
            )
        await _broadcast_agent_status(ws, db, agent_id)
        return {
            "ok": True,
            "agentId": agent_id,
            "action": action,
            "controlId": control_id,
            "cancelledQueued": cancelled_queued,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/session/confirm")
async def confirm_agent_session(agent_id: str, req: AgentSessionResolveRequest, request: Request):
    """Sticky session identity (governance, 2026-05-30): operator confirms the
    NEW (pending) session id. Re-pins `session_handle := pending_session_id`,
    clears the pending id, and exits the `session-changed` state. Delivery now
    follows the new id. Idempotent: a 409 is returned if there is no pending id
    to confirm (nothing to resolve).
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        pending = str(row["pending_session_id"] or "").strip()
        if not pending:
            raise HTTPException(409, f"Agent '{agent_id}' has no pending session id to confirm")

        runtime = _normalize_runtime(row["runtime"] or "generic")
        session_mode = _normalize_session_mode(row["session_mode"] or "resident")
        runtime_config = _json_loads_or(row["runtime_config"], {})
        # Re-pin: the new id becomes the live handle. Mirror the normal handle
        # write so runtime_state / capabilities stay consistent with the handle.
        runtime_state = _runtime_state_replacing_handle(runtime, row["runtime_state"], pending)
        capabilities = _default_capabilities_for(runtime, session_mode, pending, runtime_config)
        await db.execute(
            """
            UPDATE agents
            SET session_handle = ?,
                pending_session_id = '',
                runtime_state = ?,
                capabilities = ?,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                pending,
                json.dumps(runtime_state),
                json.dumps(capabilities),
                f"session-changed resolved: re-pinned to '{pending}' by {req.requestedBy or 'operator'}.",
                now,
                agent_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": pending})
        return {
            "ok": True,
            "agentId": agent_id,
            "resolution": "confirm",
            "sessionHandle": pending,
            "pendingSessionId": "",
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/session/keep")
async def keep_agent_session(agent_id: str, req: AgentSessionResolveRequest, request: Request):
    """Sticky session identity (governance, 2026-05-30): operator keeps the
    CURRENT (persisted) session id. Clears `pending_session_id`, leaves
    `session_handle` untouched, and surfaces the runtime's resume command so the
    operator can re-attach the agent to the persisted id (e.g. the agent drifted
    onto a fresh id and must be resumed back onto the pinned one). Idempotent:
    409 if there is no pending id to keep.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        pending = str(row["pending_session_id"] or "").strip()
        if not pending:
            raise HTTPException(409, f"Agent '{agent_id}' has no pending session id to keep")

        persisted_handle = str(row["session_handle"] or "").strip()
        runtime = _normalize_runtime(row["runtime"] or "generic")
        # Resume command for the operator to re-attach to the persisted id,
        # sourced from the runtime adapter (Python mirror of the JS contract).
        resume_command = ""
        try:
            from service.runtimes import adapter_for
            resume_command = adapter_for(runtime).resume_command(persisted_handle, agent_id=agent_id)
        except Exception:
            resume_command = ""
        await db.execute(
            """
            UPDATE agents
            SET pending_session_id = '',
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                (
                    f"session-changed resolved: keeping pinned id '{persisted_handle}' "
                    f"(resume: {resume_command}) by {req.requestedBy or 'operator'}."
                    if resume_command
                    else f"session-changed resolved: keeping pinned id '{persisted_handle}' by {req.requestedBy or 'operator'}."
                ),
                now,
                agent_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, db)
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_session_handle_updated", {"agentId": agent_id, "sessionHandle": persisted_handle})
        return {
            "ok": True,
            "agentId": agent_id,
            "resolution": "keep",
            "sessionHandle": persisted_handle,
            "pendingSessionId": "",
            "resumeCommand": resume_command,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/resident-lost")
async def resident_lost(agent_id: str, req: AgentResidentLostRequest, request: Request):
    db = await get_db()
    try:
        now = _now()
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        runtime_state = _json_loads_or(row["runtime_state"], {})
        current_bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
        bridge_id = str(req.bridgeId or "").strip()
        if bridge_id and current_bridge_id and bridge_id != current_bridge_id:
            return {
                "ok": True,
                "ignored": True,
                "reason": "bridge_not_current",
                "agentId": agent_id,
                "currentBridgeId": current_bridge_id,
                "bridgeId": bridge_id,
            }

        if bridge_id:
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = CASE WHEN COALESCE(superseded_by, '') = '' THEN 'resident-lost' ELSE superseded_by END,
                    superseded_at = COALESCE(superseded_at, ?)
                WHERE id = ? AND agent_id = ?
                """,
                (now, bridge_id, agent_id),
            )

        settings = await _load_settings(db)
        returned, transition = await _auto_return_resident_to_managed_if_possible(
            db,
            row,
            settings=settings,
            force=True,
            reason="resident_runtime_lost",
        )

        if not transition:
            # A session_mode='managed' agent reaching here is NOT a resident that lost
            # its runtime — it's a MANAGED worker whose backing died (the hermes
            # managed-host reuses this signal via reportGatewayDead when its gateway
            # port goes dead). The server can re-spawn a managed worker on the next
            # message, so it must rest at a COLD-STARTABLE state, not 'stopped'.
            #
            # The old code stopped it (status='stopped', launch_mode='none'), which the
            # send-gate rejects outright ("agent status is stopped") — so a dead-gateway
            # hermes could NEVER wake; every send bounced and the only recovery was a
            # manual hermes-aify restart (operator-reported: whole hermes team stuck
            # 'stopped', 2026-07-06/07). Wake test proved status='stopped' hard-blocks
            # delivery (dispatchRuns:[], reason "agent status is stopped").
            #
            # Fix: for a managed agent, mirror an idle-available managed worker
            # (stored status='active' → _compute_agent_status derives 'available' with
            # no live worker; launch_mode='detached') so the next send cold-starts a
            # fresh session (new gateway). The bound env still gates via the send
            # preflight, so an offline env yields a clean "env unavailable" wait rather
            # than a permanent stop. Resident agents keep the stop fallback (a resident
            # that lost its runtime with no managed backing is correctly stopped).
            agent_is_managed = str(row["session_mode"] or "").strip().lower() == "managed"
            if agent_is_managed:
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'active',
                        status_note = ?,
                        launch_mode = 'detached',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        (
                            "Managed worker backing ended ("
                            + str(req.reason or "runtime/gateway lost").strip()[:200]
                            + "); will cold-start a fresh session on the next message."
                        )[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "managed_worker_lost_available"
            else:
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        str(req.reason or "Resident runtime bridge was lost and no managed backing was available.")[:500],
                        now,
                        agent_id,
                    ),
                )
                returned = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
                transition = "resident_to_stopped"

        await db.commit()
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        status = await _compute_agent_status(returned, db)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_resident_lost", {"agentId": agent_id, "transition": transition})
        return {
            "ok": True,
            "agentId": agent_id,
            "transition": transition,
            "agent": _agent_record_to_dict(returned, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/stop-worker")
async def stop_agent_worker(agent_id: str, request: Request):
    """Phase 4: dashboard Stop → agent.status = 'available'.

    Single endpoint that tears down whatever persistent worker the agent
    has (virtual rpc terminal_session, live agent_sessions, terminal
    bindings, runtime_state.virtualTerminalId pointer, turn_busy pulse).
    Bridge-side resources (PiSession pool entry, codex/opencode session
    pools, claude-aify wrapper PTY) get cleaned up by the bridge on its
    next reconcile cycle — the service-side teardown here is
    authoritative for the agent's reported status.

    The agent's persistent identity (registration, capabilities,
    conversation history, session_handle for resume) is preserved.
    Only the live worker lifecycle ends.
    """
    db = await get_db()
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        requested_by = str(body.get("requestedBy") or "dashboard").strip() or "dashboard"
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        runtime_state = _json_loads_or(agent_row["runtime_state"], {}) or {}
        virtual_terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
        terminal_payload = None
        terminal_control_id = ""
        if virtual_terminal_id:
            row = await (await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ?",
                (virtual_terminal_id,),
            )).fetchone()
            if row:
                # Database state cannot stop a process on the owning host. Queue
                # the bridge-side stop before marking the row stopped. For managed
                # Hermes, server.js also uses this control to reap the detached
                # gateway/loop/daemon triad for this agent only.
                terminal_control_id = await _append_terminal_control(
                    db,
                    terminal_id=virtual_terminal_id,
                    environment_id=str(row["environment_id"] or ""),
                    bridge_id=str(row["bridge_id"] or ""),
                    action="stop",
                    requested_by=requested_by,
                )
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = COALESCE(stopped_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, virtual_terminal_id),
                )
                await _append_terminal_event(
                    db,
                    virtual_terminal_id,
                    "agent_worker_stopped",
                    json.dumps({"agentId": agent_id, "requestedAt": now}),
                )
                terminal_payload = _terminal_session_to_dict(row)
            runtime_state.pop("virtualTerminal", None)
            runtime_state.pop("virtualTerminalId", None)
        # REAL terminals too (R2, 2026-07-26). The block above only ever stopped
        # runtime_state.virtualTerminalId — Pi's synthesized RPC terminal — so for every
        # wrapper-backed runtime (claude-aify / hermes-aify / codex-aify PTYs) this endpoint
        # tore down DB state and reported success while the actual process kept running. The
        # operator's "Stop worker" button therefore lied on a destructive action.
        #
        # No new machinery: the same `_append_terminal_control(action="stop")` the virtual path
        # uses is what session-control already relies on, and host-side TERMINAL_MANAGER.stop
        # escalates SIGTERM→SIGKILL. Only the target was wrong.
        #
        # `id NOT LIKE 'vterm_%'` skips the synthesized rows (handled above, and already marked
        # stopped) so a virtual terminal is never double-stopped.
        live_terminals = await (await db.execute(
            """
            SELECT * FROM terminal_sessions
            WHERE agent_id = ?
              AND id NOT LIKE 'vterm_%'
              AND LOWER(COALESCE(status,'')) IN
                  ('starting','attached','running','active','idle','recovering')
            """,
            (agent_id,),
        )).fetchall()
        for row in live_terminals:
            real_terminal_id = str(row["id"] or "")
            if not real_terminal_id:
                continue
            await _append_terminal_control(
                db,
                terminal_id=real_terminal_id,
                environment_id=str(row["environment_id"] or ""),
                bridge_id=str(row["bridge_id"] or ""),
                action="stop",
                requested_by=requested_by,
            )
            # 'stopping', NOT 'stopped' — the TRANSITIONAL state, matching the shared
            # session-control path (api_v2.py:12407). The stop is only QUEUED here; the host has
            # not acknowledged it yet, so claiming 'stopped' asserts a process death that has not
            # happened — the same "state that lies" defect this release exists to remove. A wedged
            # 'stopping' row is caught by the STUCK_STOPPING_GRACE_SECONDS reaper.
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = 'stopping',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, real_terminal_id),
            )
            await _append_terminal_event(
                db,
                real_terminal_id,
                "agent_worker_stopped",
                json.dumps({"agentId": agent_id, "requestedAt": now, "terminal": "real"}),
            )
            if terminal_payload is None:
                terminal_payload = _terminal_session_to_dict(row)
        await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(runtime_state), now, agent_id),
        )
        # End any live agent_sessions for the agent — they tracked the
        # worker process which is being torn down.
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE agent_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting', 'cli-takeover', 'managed-warm')
            """,
            (now, now, agent_id),
        )
        # Clear turn_busy.
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
            (agent_id, now),
        )
        # Keep the v2 engine in sync (dual-table drift guard, review M3 2026-06-10).
        await _clear_status_state_in_turn(db, agent_id)
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_worker_stopped", {"agentId": agent_id, "virtualTerminalId": virtual_terminal_id})
        await _broadcast_agent_status(ws, db, agent_id)
        return {
            "ok": True,
            "agentId": agent_id,
            "virtualTerminalId": virtual_terminal_id,
            "terminalControlId": terminal_control_id,
            "terminal": terminal_payload,
        }
    finally:
        await db.close()
