"""Retiring an agent, deleting a dead session, restarting a managed agent (port of
`mcp/stdio/lifecycle-tools.mjs`, service mode).

A removal or restart can be QUEUED for the agent's host to carry out (aify-env owns its processes); the
reply then names the lifecycle request rather than claiming the effect. A restart acts as the CALLER, from
the request's header.
"""

from __future__ import annotations

from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.liveness import _LIVE_SESSION_STATUSES
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.send_tools import SAFE_NAME

def _queued(answer: dict) -> bool:
    return answer.get("queued") is True and isinstance((answer.get("request") or {}).get("id"), str)


def _queued_text(answer: dict, what: str) -> str:
    request = answer["request"]
    return (f"{what} is queued as lifecycle request {request['id']} [{request.get('status')}]; aify-env on "
            f"its host carries it out. Read GET /agent-lifecycle-requests/{request['id']} before acting on it.")


async def comms_remove_agent(agentId: str = Field(description="Agent ID to remove")):
    """DESTRUCTIVE. Retires an agent identity for good: the ID stops being addressable and a live session under it is orphaned. Its message history survives; re-creating the ID later makes a fresh identity, not a restore. To restart a stuck agent use comms_restart; to pause one, stop it in dashboard Sessions."""
    if not SAFE_NAME.match(agentId):
        return tool_error(f'Invalid agent ID: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{agentId}"')
    r = await call("DELETE", f"/agents/{quote(agentId, safe='')}")
    if _queued(r):
        return _queued_text(r, f'Removal of "{agentId}"')
    if r.get("ok"):
        return (f'Removed agent "{agentId}". If it had a managed worker, its host stops that within about half a minute '
                "-- confirm with comms_agents or the environment's own process list if you need it gone before acting on that.")
    return f'Agent "{agentId}" was already absent; future auto re-registration is blocked until explicit register.'


async def comms_delete_session(sessionId: str = Field(description="Runtime session ID to delete")):
    """Delete one inactive runtime session record. Active/running sessions must be stopped first. This does not delete the agent identity or chat messages."""
    session = (sessionId or "").strip()
    if not session:
        return tool_error("sessionId is required.")
    try:
        r = await call("DELETE", f"/sessions/{quote(session, safe='')}")
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(str(error) or "Failed to delete session.")
    if r.get("ok"):
        return f'Deleted inactive session "{session}" for agent "{r.get("agentId") or "unknown"}".'
    return f'Session "{session}" was not deleted.'


async def comms_restart(
    ctx: Context,
    agentId: str = Field(description="Agent whose managed session to restart"),
    freshContext: bool | None = Field(default=None, description="true = Reset: a fresh native session, its memory lost"),
):
    """Restart a MANAGED agent, as dashboard Sessions → Restart does: aify-env replaces the worker and resumes its native session. Not while it is `starting`: that kills the boot. A RESIDENT cannot be restarted: interrupt its run with comms_run_interrupt, or ask the operator to relaunch it."""
    target_id = (agentId or "").strip()
    if not target_id:
        return tool_error("agentId is required.")
    try:
        info = await call("GET", f"/agents/{quote(target_id, safe='')}")
        agent = info.get("agent") or info
        mode = str(agent.get("sessionMode") or "").lower()
        if not mode:
            return tool_error(f'Agent "{target_id}" not found or has no session.')
        if mode != "managed":
            return tool_error(f'Agent "{target_id}" is {mode}, not managed. Resident sessions are operator-owned and can\'t be restarted '
                              "remotely — ask the operator to relaunch it, or use comms_run_interrupt to stop its current run.")
        sessions = (await call("GET", f"/sessions?agentId={quote(target_id, safe='')}")).get("sessions") or []
        target = next((s for s in sessions if str(s.get("status") or "").lower() in _LIVE_SESSION_STATUSES), sessions[0] if sessions else None)
        if not target or not target.get("id"):
            return tool_error(f'Agent "{target_id}" has no session to restart — send it a message instead; a managed agent cold-starts a fresh worker on send.')
        action = "recreate" if freshContext else "restart"
        r = await call("POST", f"/sessions/{quote(target['id'], safe='')}/control", {"action": action, "from_agent": caller_id(ctx)})
    except Exception as error:  # noqa: BLE001
        return tool_error(str(error) or "Failed to restart agent.")
    if _queued(r):
        return _queued_text(r, f'Restart of "{target_id}"')
    if r.get("ok") is False:
        return f'Restart of "{target_id}" was not accepted: {r.get("error") or "unknown reason"}.'
    verb = "reset with a fresh context" if freshContext else "restarted"
    return (f'Managed session for "{target_id}" {verb} (session {target["id"]}, action={action}); '
            "the live worker is stopped and aify-env starts the replacement.")


TOOLS = (comms_remove_agent, comms_delete_session, comms_restart)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
