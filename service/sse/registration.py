"""How every comms tool module puts its tools on the MCP server.

`structured_output=False`: a `-> str` tool otherwise carries an output schema in `tools/list` that every
agent pays for in context (3,148 bytes over 22 tools, measured 2026-10-27), and it lets a tool return
`rendering.tool_error(...)`.

`reports_errors`: an exception a tool does not catch becomes its result, the exception's message with
`isError` set. That is what the JS SDK does with a thrown error, so a ported tool keeps the JS tool's
error text without a try/except the JS did not have.
"""

from __future__ import annotations

import functools

from service.sse.rendering import tool_error


def reports_errors(tool):
    @functools.wraps(tool)
    async def run(*args, **kwargs):
        try:
            return await tool(*args, **kwargs)
        except Exception as error:  # noqa: BLE001 - the agent is told what failed, as the JS SDK tells it
            return tool_error(str(error))

    return run


def register_tools(mcp_server, tools) -> None:
    for tool in tools:
        mcp_server.tool(structured_output=False)(reports_errors(tool))
