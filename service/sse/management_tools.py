"""The housekeeping tool: bulk clear.

Extracted from `mcp/sse_server.py` in v0.5.4 — a whole subject, bodies untouched.

`comms_clear` IS THE ONLY DESTRUCTIVE TOOL ON THIS TRANSPORT. It takes a target and an optional age
filter and reports what it removed, and its renderer carries a real distinction: "Nothing to clear."
and a list of counts are different claims, and an agent that reads a silent success as "cleared"
when nothing matched will not run it again. That branch is what the tests pin.

Patch `_api` HERE, not on the transport: the tool resolves it from this module.
"""

from __future__ import annotations

from service.sse.api_client import api as _api


async def comms_clear(target: str, agentId: str = "", olderThanHours: float = 0) -> str:
    """DESTRUCTIVE AND IRREVERSIBLE. Permanently deletes data for the WHOLE hub, not just for you.

    Without agentId, inbox and agents reach every agent and shared every artifact -- other teams
    included. There is no undo and no confirmation prompt; the only safety is this sentence.
    Do NOT use it to tidy your own inbox (reading them marks them read; just leave them) or to
    remove one agent (use comms_remove_agent). Scope it as narrowly as the task allows: pass
    agentId, and prefer olderThanHours over a bare wipe. If you did not explicitly decide to
    destroy shared history, you want a different tool.
    """
    data = {"target": target}
    if agentId:
        data["agentId"] = agentId
    if olderThanHours > 0:
        data["olderThanHours"] = olderThanHours
    r = await _api("POST", "/clear", data)
    if not r.get("ok"):
        return f"Error: {r.get('detail', 'unknown error')}"
    c = r.get("cleared", {})
    parts = [f"{k}: {v}" for k, v in c.items() if v]
    return f"Cleared: {', '.join(parts)}" if parts else "Nothing to clear."


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_clear,)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
