"""Which session currently owns an agent: confirm one, keep one alive, report one lost.

Extracted from `service/routers/agents/session_ops.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local, and notably NOTHING borrowed from
`agents/shared.py`. That last part is why these three could leave and `control_agent` /
`stop_agent_worker` could not: those still reach `_borrowed_live_session_statuses`, so moving them
would create a shim rather than a route surface.

AN AGENT IS ONE IDENTITY WITH MANY PROCESSES, and that is the whole subject here. `agent_sessions`
rows are PROCESSES, not conversations — one long-lived conversation resumed by seventy-five boots is
seventy-five rows — so "which of these is the live one" is a real question with a wrong answer
available at all times. These three endpoints are how a bridge answers it: confirm resolves a
contested claim, keep renews a lease, and resident-lost reports that the process behind a resident
session is gone.

RESIDENT-LOST IS NOT A STOP. A resident session vanishing may mean the agent should return to
managed operation rather than end — `_auto_return_resident_to_managed_if_possible` — and the
distinction matters because treating loss as termination strands an agent that could still be
worked. `_settle_lost_resident_when_no_transition` is the other half: when no transition applies,
something still has to make the records agree.

Bodies and route decorators are byte-identical to what stood in `session_ops.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.capabilities import _default_capabilities_for
from service.api_core.dispatch_state import _get_dispatch_state_for_agent
from service.api_core.execution_mode import _auto_return_resident_to_managed_if_possible
from service.api_core.records import _agent_record_to_dict
from service.api_core.resident_loss import _settle_lost_resident_when_no_transition
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.runtime_state import _runtime_state_replacing_handle
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import AgentResidentLostRequest, AgentSessionResolveRequest

router = domain_router()



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

        returned, transition = await _settle_lost_resident_when_no_transition(
            db, agent_id, row, req, now, returned, transition
        )

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
