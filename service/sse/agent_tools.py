"""Registering as the caller, and listing who else is here.

`comms_register` changes the caller's role and name, and nothing else. The caller is the request's header
(0.9.11). Registering is the launcher's job at start-up: a registration is a full refresh of runtime,
session, config and capabilities, and this caller has none of those facts, so a POST /agents from here
would clear what the host recorded. An agent with no record is told so rather than given a thin one.

`comms_agents` renders presence for every registered agent. Every field it prints has a default,
because a half-registered row is a normal state -- an agent that has never been seen has no
`lastSeen`, and rendering that as blank would read as "seen just now, with nothing to report".
"""

from __future__ import annotations

from urllib.parse import quote

from mcp.server.fastmcp import Context

from service.api_core.serialization import _quote_untrusted_subject
from service.sse.agent_summary import runtime_summary, wake_mode_summary
from service.sse.api_client import ServiceError, call
from service.sse.caller import caller_id, caller_or_none
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.tool_format import format_dispatch_state, format_outbound_activity


NOT_REGISTERED = ("You are not registered: your launcher (claude-aify, hermes-aify) registers you when it starts. "
                  "Start through it; this tool then changes your role and name.")


async def comms_register(ctx: Context, role: str, name: str = "") -> str:
    """Change your role, and your name if given. Your launcher registered you at start-up; this keeps the runtime and session it recorded."""
    me = caller_id(ctx)
    try:
        r = await call("PATCH", f"/agents/{quote(me, safe='')}/profile", {"role": role, "name": name})
    except ServiceError as error:
        if error.status == 404:
            return tool_error(NOT_REGISTERED)
        raise
    return f'Updated "{r.get("agentId", me)}": role {r.get("role", role)}, name {r.get("name", name or me)}.'


def _description_preview(info: dict) -> str:
    desc = str(info.get("description") or "").strip()
    if not desc:
        return ""
    return f"\n    {desc[:159]}…" if len(desc) > 160 else f"\n    {desc}"


async def comms_agents(ctx: Context):
    """List registered agents with role, status and unread count. Each line starts with the id to pass as comms_send `to`."""
    # AN OUTAGE IS NOT AN ANSWER: `call` raises on a failed request, and the agent is told so.
    entries = (await call("GET", "/agents")).get("agents") or {}
    if not entries:
        return "No agents registered."
    # The id is the address (`to=` in comms_send), and the caller's own row says so (v0.7, H-A7).
    me = caller_or_none(ctx)
    lines = []
    for aid, info in entries.items():
        status = f" [{info['status']}]" if info.get("status") else ""
        you = " (you)" if aid == me else ""
        lines.append(
            f"- {aid}{you} ({info.get('role')}){status} -- \"{info.get('name', aid)}\" | {runtime_summary(info)} "
            f"| wake: {wake_mode_summary(info)} | unread: {info.get('unread') or 0} | last seen: {info.get('lastSeen', '?')}"
            f"{_description_preview(info)}"
        )
    return "Address an agent by the id at the start of its line.\n" + "\n".join(lines)


async def comms_agent_info(agentId: str):
    """An agent's status, wake mode, unread count, last message read, last output, and active or queued runs."""
    info = ((await call("GET", "/agents")).get("agents") or {}).get(agentId)
    if not info:
        return tool_error(f'Agent "{agentId}" not found.')
    last_read = "unknown"
    try:  # best effort: the record is the answer, the last read a detail
        lr = (await call("GET", f"/agents/{agentId}/last-read")).get("lastRead")
        last_read = f'{_quote_untrusted_subject(lr["subject"])} from {lr["from"]} (read at {lr["readAt"]})' if lr else "no messages read yet"
    except Exception:  # noqa: BLE001
        pass
    dispatch = format_dispatch_state(info)
    # INBOUND facts, labelled as such, then OUTBOUND: the question people were actually asking.
    return (
        f"{agentId} ({info.get('role')}) [{info.get('status')}]\n"
        f"  Runtime: {runtime_summary(info)}\n"
        f"  Wake mode: {wake_mode_summary(info)}\n"
        f"  Unread (inbound): {info.get('unread')}\n"
        f"  Last seen (registration liveness, not output): {info.get('lastSeen')}\n"
        f"  Last read (inbound): {last_read}\n"
        f"  {format_outbound_activity(info)}" + (f"\n{dispatch}" if dispatch else "")
    )


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_register, comms_agents, comms_agent_info)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    register_tools(mcp_server, TOOLS)
