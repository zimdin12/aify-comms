"""Controlling a live session: interrupt it, stop it, restart it.

Extracted from `service/routers/sessions.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local to that router.

A SESSION CONTROL IS NOT A TERMINAL CONTROL, even though it often becomes one. The request names a
SESSION; this handler decides what that means for the thing actually running — an active dispatch
run gets a dispatch control, an attached terminal gets a terminal control, and a restart has to
prepare a spawn before anything is torn down. Three different rows, from one verb, chosen by what is
live at the time.

A RESTART IS NOT PREPARED HERE ANY MORE (D8): a defined agent's restart goes to its host through the
lifecycle queue, and an agent no host defines is not started, so only stop and CLI takeover act here.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.agent_sessions import _settle_agent_for_session_control
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.events import _append_terminal_control
from service.api_core.operator_authz import recorded_operator_actor
from service.api_core.definition_start import undefined_refusal
from service.api_core.lifecycle_delegation import delegate
from service.api_core.records import _agent_session_to_dict
from service.api_core.routing import domain_router
from service.api_core.validation import validate_sender
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import SessionControlRequest

router = domain_router()



@router.post("/sessions/{session_id}/control")
async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    validate_sender(req.from_agent)
    # An omitted name is recorded as `dashboard`; the bridge and aify-env always name themselves.
    actor = recorded_operator_actor(req.from_agent, request, action="controlling a session as the operator")
    action = str(req.action or "").strip().lower()
    # Lifecycle cleanup (2026-06-03): `recover` + `resume` were byte-identical
    # aliases of `restart` with NO dashboard caller — dropped. (Resident
    # wake-resume lives on POST /agents/{id}/control, a different endpoint.)
    if action not in {"stop", "restart", "recreate", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]
        # A DEFINED agent's stop and restart go to its host through the D9 lifecycle queue (D9c);
        # `recreate` is a restart into a fresh conversation.
        if action in {"stop", "restart", "recreate"}:
            delegated = await delegate(db, agent_id, "stop" if action == "stop" else "restart", actor, request,
                                       fresh_context=action == "recreate")
            if delegated:
                return delegated
        if action in {"restart", "recreate"}:
            # D8: a defined agent was handed to its host above, and one no host defines is not started.
            raise HTTPException(409, undefined_refusal(agent_id))

        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=actor,
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        cancelled_spawns = 0

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recreate": "ended",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering','ended') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        cancelled_spawns = await _settle_agent_for_session_control(
            db, session_id, agent_id, action, now, cancelled_spawns,
        )

        # Halt the running backing (2026-06-07): Stop/Restart/Reset/CLI-takeover must PROMPTLY
        # kill the live managed PTY, not just flip DB status. Previously only the agent-control
        # stop enqueued a terminal stop, so the UI's session-control Stop left the worker running
        # as a headless orphan until a reaper / the next Restart's reap-prior. Enqueue a terminal
        # 'stop' for the session's live terminal(s). For restart/recreate the new spawn_request
        # was already queued above (and an env-offline target 409'd before reaching here), so we
        # never kill the old backing without a replacement queued. Resume is unaffected — it
        # carries via the durable session_handle, not the live PTY.
        live_terminals = await (await db.execute(
            "SELECT id, environment_id, bridge_id, status FROM terminal_sessions WHERE session_id = ?",
            (session_id,),
        )).fetchall()
        for term_row in live_terminals:
            if str(term_row["status"] or "").strip().lower() in _TERMINAL_ACTIVE_STATUSES:
                await _append_terminal_control(
                    db,
                    terminal_id=term_row["id"],
                    environment_id=term_row["environment_id"] or "",
                    bridge_id=term_row["bridge_id"] or "",
                    action="stop",
                    requested_by=actor,
                    body=f"Session {action} from dashboard.",
                )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
        }
    finally:
        await db.close()
