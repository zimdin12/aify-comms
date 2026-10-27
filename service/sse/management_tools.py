"""`comms_clear`, the bulk delete (port of the tool in `mcp/stdio/lifecycle-tools.mjs`, service mode).

The one destructive tool that reaches the whole hub. Its description is the only safety it has, so it says
so; "Nothing to clear." and a list of counts are different claims, and an agent that reads a silent
success as "cleared" when nothing matched will not run it again.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from service.sse.api_client import call
from service.sse.registration import register_tools


async def comms_clear(
    target: Literal["inbox", "shared", "agents"] = Field(description="What to clear"),
    agentId: str | None = Field(default=None, description="Limit to one agent for target=inbox or target=agents"),
    olderThanHours: float | None = Field(default=None, description="Only clear items older than N hours"),
):
    """DESTRUCTIVE AND IRREVERSIBLE. Permanently deletes data for the WHOLE hub, not just for you. Without agentId, inbox and agents reach every agent and shared every artifact — other teams included. There is no undo and no confirmation prompt; the only safety is this sentence. Do NOT use it to tidy your own inbox (reading them marks them read; just leave them) or to remove one agent (use comms_remove_agent). Scope it as narrowly as the task allows: pass agentId, and prefer olderThanHours over a bare wipe. If you did not explicitly decide to destroy shared history, you want a different tool."""
    body = {"target": target}
    if agentId is not None:
        body["agentId"] = agentId
    if olderThanHours is not None:
        body["olderThanHours"] = olderThanHours
    cleared = (await call("POST", "/clear", body)).get("cleared") or {}
    parts = [f"{cleared[key]} {word}" for key, word in (("messages", "messages"), ("files", "files"), ("agents", "agents")) if cleared.get(key)]
    return f"Cleared: {', '.join(parts)}." if parts else "Nothing to clear."


TOOLS = (comms_clear,)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
