"""The two housekeeping tools: bulk clear, and where the dashboard is.

Extracted from `mcp/sse_server.py` in v0.5.4 — a whole subject, bodies untouched.

`comms_clear` IS THE ONLY DESTRUCTIVE TOOL ON THIS TRANSPORT. It takes a target and an optional age
filter and reports what it removed, and its renderer carries a real distinction: "Nothing to clear."
and a list of counts are different claims, and an agent that reads a silent success as "cleared"
when nothing matched will not run it again. That branch is what the tests pin.

`comms_dashboard` reads `get_config()` at call time rather than closing over the module-level
`config`, which is what lets a port change take effect without a restart.

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

from service.config import get_config
from service.sse.api_client import api as _api


async def comms_clear(target: str, agentId: str = "", olderThanHours: float = 0) -> str:
    """Clear messages, shared files, agents, or everything. Optional age filter."""
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


async def comms_dashboard() -> str:
    """Get the dashboard URL."""
    cfg = get_config()
    return f"Dashboard: http://localhost:{cfg.port}/api/v1/dashboard"


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_clear, comms_dashboard)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
