"""Session lifecycle OPERATIONS: keep, confirm, resident-lost, stop-worker, control.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import HTTPException, Request

from service.api_core.operator_authz import recorded_operator_actor
from service.api_core.lifecycle_delegation import delegate
from service.api_core.request_body import json_object_body
from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.agent_stop_resume import _apply_agent_stop_or_resume
from service.api_core.agent_terminal_ops import _request_stop_agent_terminals
from service.api_core.status_broadcast import _broadcast_agent_status
from service.api_core.routing import domain_router
from service.api_core.validation import validate_sender

logger = logging.getLogger("aify_comms.routers.agents.session_ops")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentControlRequest

from service.api_core.definition_start import undefined_refusal
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.records import _agent_record_to_dict, _terminal_session_to_dict
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.turn_state import _clear_status_state_in_turn
from service.api_core.ws import _get_ws
from service.db import get_db
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
import sqlite3
from service.routers.agents.shared import logger

router = domain_router()


@router.post("/agents/{agent_id}/control")
async def control_agent(agent_id: str, req: AgentControlRequest, request: Request):
    validate_sender(req.from_agent)
    # `dashboard` starts REPLACE a live instance, and an interrupt or stop records an omitted name as it.
    actor = recorded_operator_actor(req.from_agent, request, action="controlling an agent as the operator")
    action = str(req.action or "").strip().lower()
    if action not in {"interrupt", "stop", "resume", "start"}:
        raise HTTPException(400, f'Unsupported agent control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        # A DEFINED agent's start and stop go to its host through the D9 lifecycle queue (D9c).
        if action in {"start", "stop"}:
            delegated = await delegate(db, agent_id, action, actor, request)
            if delegated:
                return delegated

        now = _now()

        # START: a defined agent's was handed to its host above (D9c). What reaches here no host defines, and
        # since D8 that is not started; a resident is told where its terminal is instead.
        if action == "start":
            if _normalize_session_mode(agent["session_mode"] or "resident") == "resident":
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" is resident — its terminal is the CLI you launched, '
                    "not a dashboard-owned worker. Switch it to managed to start one from here.",
                )
            raise HTTPException(409, undefined_refusal(agent_id))
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if action in {"interrupt", "stop"}:
            if active_run:
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=actor,
                    action="interrupt",
                    body=req.body or f"Agent {action} requested from dashboard.",
                )
            elif action == "interrupt":
                raise HTTPException(409, f'Agent "{agent_id}" has no active run to interrupt')

        cancelled_queued = 0
        cancelled_queued = await _apply_agent_stop_or_resume(
            db, agent_id, agent, req, action, now, cancelled_queued
        )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
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
        body = await json_object_body(request, lenient=True)
        requested_by = recorded_operator_actor(body.get("requestedBy"), request, action="stopping a worker as the operator")
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        # Ending the worker and leaving the agent startable is the D9 `kill` (D9c).
        delegated = await delegate(db, agent_id, "kill", requested_by, request)
        if delegated:
            return delegated
        now = _now()
        # THE RUN IN FLIGHT IS INTERRUPTED, as the session stop does: this is the drawer's only
        # stop since 2026-09-29, and without it the run is left with nobody behind it.
        active_run = await _get_blocking_active_run(db, agent_id)
        if active_run:
            await _append_dispatch_control(
                db, active_run["runId"], from_agent=requested_by, action="interrupt",
                body="Worker stopped from the dashboard.",
            )
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
        # Through `_request_stop_agent_terminals`, the one stop-every-terminal path operator Stop,
        # removal and the resident switch also use: a `stop` control per terminal, the row marked
        # 'stopping' (the TRANSITIONAL state -- the host has not acknowledged anything yet, and a
        # wedged row is caught by the STUCK_STOPPING_GRACE_SECONDS reaper). It skips `vterm_%` rows,
        # handled above, so a virtual terminal is never double-stopped. This carried its own copy
        # of that loop until 2026-09-23.
        for real_terminal_id in await _request_stop_agent_terminals(
            db, agent_id, requested_by=requested_by, now=now,
        ):
            await _append_terminal_event(
                db,
                real_terminal_id,
                "agent_worker_stopped",
                json.dumps({"agentId": agent_id, "requestedAt": now, "terminal": "real"}),
            )
            if terminal_payload is None:
                row = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?", (real_terminal_id,),
                )).fetchone()
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
