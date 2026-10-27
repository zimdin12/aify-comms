"""What an agent says about itself: its focus note and its team-facing description (port of
`mcp/stdio/self-record-tools.mjs`).

Both write the CALLER's own record, so the caller is the request's header and never an argument: an
agent cannot set another agent's note. The status word here is a coarse tag for the note; the badge
beside an agent's name is derived by the service and this does not change it.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error

#: `selfReportedStatuses` in service/contracts/vocabulary.json, as the JS tool's enum.
SelfReportedStatus = Literal["idle", "working", "reviewing", "testing", "researching", "blocked", "focused"]


async def comms_status(
    ctx: Context,
    status: SelfReportedStatus = Field(description="Coarse tag for the note; not your badge"),
    note: str | None = Field(default=None, description="What you're working on, e.g. 'NRD createPipelines'"),
):
    """Set your focus note, shown beside your name on the dashboard until you change it. It does not change your status badge, which the service derives. Report finished work with a reply."""
    body = {"status": status} if note is None else {"status": status, "note": note}
    r = await call("PATCH", f"/agents/{caller_id(ctx)}", body)
    return f"Status updated: {r.get('agentId')} → {r.get('status')}"


async def comms_describe(
    ctx: Context,
    description: str = Field(max_length=2000, description='Who you are, your project and your focus. "" clears it.'),
):
    """Set your team-facing description, shown to other agents in comms_agents until you change it."""
    try:
        r = await call("PATCH", f"/agents/{quote(caller_id(ctx), safe='')}/description", {"description": description})
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(f"Describe error: {error}")
    text = r.get("description") or ""
    preview = f": {text[:120]}{'…' if len(text) > 120 else ''}" if text else " (cleared)"
    return f"Description updated for {r.get('agentId')}{preview}"


TOOLS = (comms_status, comms_describe)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
