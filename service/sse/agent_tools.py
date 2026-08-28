"""Registering an SSE client as an agent, and listing who else is here.

Extracted from `mcp/sse_server.py` in v0.5.4 — bodies untouched.

`comms_register` SAYS WHAT AN SSE CLIENT CANNOT DO, in its own description: it can coordinate work
but cannot host a local runtime launch. That sentence is why this transport is a deliberately
REDUCED tool surface rather than an incomplete one, and it is the first thing a connecting agent
reads, so it moved with the code and is asserted rather than assumed.

`comms_agents` renders presence for every registered agent. Every field it prints has a default,
because a half-registered row is a normal state — an agent that has never been seen has no
`lastSeen`, and rendering that as blank would read as "seen just now, with nothing to report".

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

from service.sse.api_client import api as _api


async def comms_register(
    agentId: str,
    role: str,
    name: str = "",
    cwd: str = "",
    model: str = "",
    instructions: str = "",
) -> str:
    """Register this MCP client as an agent for messaging and presence. SSE clients can coordinate work, but cannot host local runtime launches."""
    r = await _api("POST", "/agents", {
        "agentId": agentId, "role": role, "name": name or agentId,
        "cwd": cwd, "model": model, "instructions": instructions,
    })
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f'Registered "{r.get("agentId", agentId)}" (role: {role}).'


async def comms_agents() -> str:
    """List all registered agents, their roles, and unread message counts."""
    r = await _api("GET", "/agents")
    # AN OUTAGE IS NOT AN ANSWER. `_api` returns `detail` on any error precisely so every caller
    # can branch on it, and that fix's own note says "every caller in this package checks" -- this
    # one did not, so a 500 rendered as a confident fact about the fleet.
    if "detail" in r:
        return f"Error: {r['detail']}"
    entries = r.get("agents", {})
    if not entries:
        return "No agents registered."
    lines = []
    for aid, info in entries.items():
        status = f" [{info['status']}]" if info.get("status") else ""
        lines.append(
            f"- {aid} ({info['role']}){status} -- \"{info.get('name', aid)}\" "
            f"| unread: {info.get('unread', 0)} | last seen: {info.get('lastSeen', '?')}"
        )
    return "\n".join(lines)


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_register, comms_agents)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
