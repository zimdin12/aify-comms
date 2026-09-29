"""Compacting an agent: typing its runtime's own command, or briefing a fresh session.

HTTP only. What is allowed, which command, and what a handoff brief says are decided in
`service/api_core/compaction.py`; this module gathers the facts those functions take.

  POST /agents/{id}/compact/native        type the runtime's compaction command into the live console
  GET  /agents/{id}/compact/handoff-brief the first message for a handoff's fresh session

The handoff itself is still a spawn request its caller posts (`comms_compact`, the dashboard's
Compact form), so the spawn path stays the one path; what moved here is the TEXT, which both callers
built separately and differently until 0.7.5.
"""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from service.api_core.agent_terminal_ops import _resolve_live_console_terminal
from service.api_core.compaction import (
    MAX_RECENT_MESSAGES,
    NativeCompactTarget,
    decide_native_compact,
    handoff_brief,
    recent_messages_count,
    transcript_location,
)
from service.api_core.console_write import queue_console_input, require_console_caller
from service.api_core.operator_authz import refuse_an_unproven_operator_claim
from service.api_core.routing import domain_router
from service.api_core.status_refresh import _compute_agent_status
from service.api_core.ws import _get_ws
from service.db import get_db

# Imported for the ANNOTATION: under postponed evaluation a missing one demotes the body to a query
# parameter and the endpoint 422s at request time.
from service.models import AgentNativeCompactRequest

router = domain_router()


@router.post("/agents/{agent_id}/compact/native")
async def post_agent_native_compact(agent_id: str, req: AgentNativeCompactRequest, request: Request):
    """Type the runtime's compaction command into a managed agent's live console, then Enter.

    Refused, and nothing queued, unless the agent is managed, its runtime has a verified command, it
    has a live TUI console, and it is idle at its prompt. `ok` means QUEUED: whether the runtime
    compacted is read on its console, as with any console input.
    """
    # `dashboard` is let through unregistered below, so it must be the operator.
    refuse_an_unproven_operator_claim(req.from_, request, action="compacting an agent as the operator")
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        caller = await require_console_caller(db, req.from_, allow_dashboard=True)
        terminal = await _resolve_live_console_terminal(db, agent_id)
        status = await _compute_agent_status(agent_row, db)
        decision = decide_native_compact(NativeCompactTarget(
            agent_id=agent_id,
            session_mode=str(agent_row["session_mode"] or ""),
            runtime=str(agent_row["runtime"] or ""),
            status=str(status or ""),
            terminal_id=str(terminal["id"] or "") if terminal else "",
            terminal_command=str(terminal["command"] or "") if terminal else "",
            requested_session_id=str(req.sessionId or ""),
            terminal_session_id=str(terminal["session_id"] or "") if terminal else "",
        ))
        if not decision.allowed:
            return {"ok": False, "refused": decision.refused, "status": status, "message": decision.message}
        control_id = await queue_console_input(
            db, terminal, caller=caller, body=decision.command + "\r", purpose="native-compact",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal["id"], "action": "input"})
        return {
            "ok": True,
            "queued": True,
            "command": decision.command,
            "terminalId": terminal["id"],
            "controlId": control_id,
            "note": (
                f"QUEUED {decision.command}, not confirmed: the bytes reach the PTY, and whether the "
                "runtime compacted shows on its console (comms_console_tail)."
            ),
        }
    finally:
        await db.close()


@router.get("/agents/{agent_id}/compact/handoff-brief")
async def get_agent_handoff_brief(
    agent_id: str,
    recentMessages: int | None = Query(None, ge=0, le=MAX_RECENT_MESSAGES),
    sessionId: str = "",
):
    """The first message for a handoff's fresh session, and the facts it was built from.

    `sessionId` names the session being replaced and must be this agent's; without it the agent's
    most recently seen session is used.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        wanted = str(sessionId or "").strip()
        if wanted:
            session = await (await db.execute(
                "SELECT * FROM agent_sessions WHERE id = ? AND agent_id = ?", (wanted, agent_id),
            )).fetchone()
            if not session:
                raise HTTPException(404, f"Session '{wanted}' is not a session of agent '{agent_id}'")
        else:
            session = await (await db.execute(
                "SELECT * FROM agent_sessions WHERE agent_id = ? ORDER BY last_seen DESC LIMIT 1", (agent_id,),
            )).fetchone()
        handle = str((session["session_handle"] if session else "") or agent_row["session_handle"] or "")
        runtime = str((session["runtime"] if session else "") or agent_row["runtime"] or "")
        workspace = str((session["workspace"] if session else "") or agent_row["cwd"] or "")
        transcript = transcript_location(runtime, handle, workspace)
        count = recent_messages_count(recentMessages)
        source_session_id = str(session["id"]) if session else ""
        return {
            "ok": True,
            "agentId": agent_id,
            "sourceSessionId": source_session_id,
            "sessionHandle": handle,
            "runtime": runtime,
            "transcript": transcript,
            "recentMessages": count,
            "text": handoff_brief(
                agent_id=agent_id,
                source_session_id=source_session_id,
                session_handle=handle,
                runtime=runtime,
                transcript=transcript,
                recent_messages=count,
            ),
        }
    finally:
        await db.close()
