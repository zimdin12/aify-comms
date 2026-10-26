"""Who is calling a comms tool: the `X-Aify-Agent-Id` header of the request that carried the call.

0.9.11: the service serves the comms tools to every agent, so the caller cannot come from this process's
environment the way the per-agent JS bridge read `AIFY_AGENT_ID`. The launcher writes the header for its
agent's session, beside the API key; the model never types it.

A tool that acts as its caller refuses a call without the header rather than guessing one: a wrong
identity would read another agent's inbox or send in its name.
"""

from __future__ import annotations

HEADER = "x-aify-agent-id"


class NoCaller(Exception):
    """The call arrived without a usable agent id header."""


def caller_id(ctx) -> str:
    """The agent id the request names. Raises `NoCaller` when there is no request or no header."""
    try:
        # FastMCP raises ValueError for a context outside a request, and a stdio request has no HTTP request.
        headers = ctx.request_context.request.headers
    except (AttributeError, ValueError):
        headers = None
    agent_id = ((headers.get(HEADER) if headers is not None else None) or "").strip()
    if not agent_id:
        raise NoCaller(
            f"this connection names no agent: the launcher sets the {HEADER} header for its agent; "
            "relaunch through it"
        )
    return agent_id
