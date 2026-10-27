"""How every comms tool module puts its tools on the MCP server.

`structured_output=False`: a `-> str` tool otherwise carries an output schema in `tools/list` that every
agent pays for in context (3,148 bytes over 22 tools, measured 2026-10-27), and it lets a tool return
`rendering.tool_error(...)`.

`reports_errors`: an exception a tool does not catch becomes its result, the exception's message with
`isError` set. That is what the JS SDK does with a thrown error, so a ported tool keeps the JS tool's
error text without a try/except the JS did not have.

`compact_schema`: the published input schema without what pydantic adds and an agent cannot use: a `title`
on every argument, and an optional argument spelled `anyOf: [type, null]` with `default: null`. 5,560 of
the list's 24,500 bytes (measured 2026-10-27). Calls are still validated against FastMCP's own argument
model, so nothing an agent may send changes.
"""

from __future__ import annotations

import functools

from service.sse.rendering import tool_error

_NULL = {"type": "null"}


def compact_schema(node):
    """PURE. A JSON schema without titles, with each `anyOf: [T, null]` written as T and no `default: null`.

    Folding null away is a contract choice, not only an annotation trim: the published schema then refuses
    an explicit null that the service still accepts. It is the contract the JS tools publish (zod
    `.optional()`), and it loses nothing because every folded argument is optional with a null default, so
    leaving it out is the same call. `test_the_tool_list_is_held_at_its_size.py` holds that condition."""
    if isinstance(node, list):
        return [compact_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: compact_schema(value) for key, value in node.items() if key != "title"}
    if isinstance(node.get("properties"), dict):
        # Argument NAMES are data, not schema keywords: an argument called `title` stays.
        out["properties"] = {name: compact_schema(value) for name, value in node["properties"].items()}
    any_of = out.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2 and _NULL in any_of:
        (kept,) = [option for option in any_of if option != _NULL]
        out.pop("anyOf")
        out = {**kept, **out}
        if "default" in out and out["default"] is None:
            out.pop("default")
    return out


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
        # FastMCP keeps the registered Tool on its manager; its `parameters` is what tools/list publishes.
        registered = mcp_server._tool_manager.get_tool(tool.__name__)
        registered.parameters = compact_schema(registered.parameters)
