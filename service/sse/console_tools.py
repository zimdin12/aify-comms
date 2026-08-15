"""Reading and writing a managed agent's live console.

Extracted from `mcp/sse_server.py` in v0.5.4 — a subject that was split across the run tools in the
original file order.

THESE TWO DISTINGUISH THREE OUTCOMES WHERE A CALLER MIGHT SEE ONLY TWO. A console read can fail
(`ok` false), succeed against an agent that has no live terminal (`live` false), or return output —
and the middle one is the one that matters: "no live console" is not an error and must not be read
as one, or a caller retries forever against an agent that simply is not managed.

`comms_console_input` is RECOVERY-ONLY and audited. Its docstring is the only thing standing between
it and being used as a second delivery channel, which is why the docstring is part of what moved and
is asserted rather than assumed — an agent picks a tool by reading it.

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

from service.sse.api_client import api as _api


async def comms_console_tail(agentId: str, lines: int = 40) -> str:
    """Read the last N lines of another agent's live console (read-only; managed agents)."""
    n = max(1, min(int(lines or 40), 200))
    r = await _api("GET", f"/agents/{agentId}/console", params={"lines": n})
    if not r.get("ok"):
        return r.get("detail") or r.get("message") or f"Could not read {agentId}'s console."
    if not r.get("live"):
        return r.get("message") or f"{agentId} has no live console."
    output = r.get("output") or "(empty)"
    return (
        f"Console of {agentId} (terminal {r.get('terminalId')}, status {r.get('status')}), "
        f"last {r.get('lines')} lines:\n{output}"
    )


async def comms_console_input(agentId: str, text: str = "", enter: bool = True, from_agent: str = "") -> str:
    """Recovery-only console input for managed agents; audited.

    Read the console first with comms_console_tail and use this only for a proven
    interactive prompt or operator recovery. Do not inject normal work messages,
    reminders, or duplicate comms_send delivery through the console.
    """
    r = await _api("POST", f"/agents/{agentId}/console/input", {
        "text": text or "",
        "enter": bool(enter),
        "from": from_agent or "",
    })
    if not r.get("ok"):
        return r.get("detail") or r.get("message") or f"Could not send input to {agentId}."
    return f"Input sent to {agentId}'s console (terminal {r.get('terminalId')}, control {r.get('controlId')})."


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_console_tail, comms_console_input)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
