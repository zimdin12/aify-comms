"""A managed agent's console: read it, and type into it to recover (port of `mcp/stdio/console-tools.mjs`).

A read has three outcomes where a caller might see two: live output, the LAST RECORDED output of a worker
that is gone (marked NOT LIVE, led by its cause), and no console at all, which is not an error. Input is
recovery-only and attributed to the CALLER from the request's header; success means the bytes reached the
PTY, not that the runtime acted, and the reply says so.
"""

from __future__ import annotations

from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error

CONSOLE_INPUT_DESCRIPTION = (
    "Recovery-only: types into another managed agent's live console. Read the console first with comms_console_tail, "
    "and use this only to answer a prompt you saw there. Do not inject normal work messages, reminders or duplicate sends. "
    "NOT RELIABLE AS A SUBMIT: success means the input was queued: not written, not acted on (five writes reported "
    "success on a draft that never submitted). If one attempt does not visibly change the console, escalate to the operator."
)


async def comms_console_tail(
    agentId: str = Field(description="Agent whose console to read"),
    lines: int | None = Field(default=None, ge=1, le=200, description="How many trailing lines to return. Default 40."),
):
    """Read the last N lines of a managed agent's console. Works on a DEAD worker too: it returns the LAST RECORDED output, marked NOT LIVE and led by the one-line cause. Read it to learn WHY a spawn or dispatch failed, and before restarting or re-sending to an agent that looks stalled."""
    try:
        count = max(1, min(int(lines or 40), 200))
        r = await call("GET", f"/agents/{quote(agentId, safe='')}/console?lines={count}")
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(str(error))
    if not r.get("live") and r.get("historical"):
        cause = f"Cause: {r['failureLine']}\n\n" if r.get("failureLine") else ""
        store = ("Recovered from the terminal's recorded events; the output column held only its exit marker.\n\n"
                 if r.get("recordedFrom") == "events" else "")
        exit_line = (f"Killed by {r['exitSignal']}.\n" if r.get("exitSignal")
                     else "" if r.get("exitCode") is None else f"Exited with code {r['exitCode']}.\n")
        stopped = f" at {r['stoppedAt']}" if r.get("stoppedAt") else ""
        return (f"NOT LIVE — last recorded console of {agentId} (terminal {r.get('terminalId')}, {r.get('status')}{stopped}). "
                f"This worker is gone; the output below is history.\n\n{exit_line}{store}{cause}{r.get('output') or '(nothing was recorded)'}")
    if not r.get("live"):
        return r.get("message") or f"{agentId} has no live console."
    rebuilt = ("NOTE: this screen was rebuilt from the stored log after a service restart and the program has not redrawn it since; "
               "text may overlap or be missing.\n" if r.get("reconstructed") is True else "")
    return (f"Console of {agentId} (terminal {r.get('terminalId')}, status {r.get('status')}), last {r.get('lines')} lines:\n"
            f"{rebuilt}{r.get('output') or '(empty)'}")


async def comms_console_input(
    ctx: Context,
    agentId: str = Field(description="Agent whose console to send input to"),
    text: str | None = Field(default=None, description="Text/command to type. Empty string + enter=true sends just Enter."),
    enter: bool | None = Field(default=None, description="Append a carriage return. Default true. ATTEMPTS a submit; see the description."),
):
    try:
        r = await call("POST", f"/agents/{quote(agentId, safe='')}/console/input",
                       {"text": text or "", "enter": True if enter is None else bool(enter), "from": caller_id(ctx)})
    except Exception as error:  # noqa: BLE001
        return tool_error(str(error))
    if not r.get("ok"):
        return tool_error(r.get("message") or f"Could not send input to {agentId}.")
    return (f"Input QUEUED to {agentId}'s console (terminal {r.get('terminalId')}, control {r.get('controlId')}). This is NOT confirmation: "
            "queued, not yet written. The control completes when the bytes reach the PTY, and even then that does not show the runtime acted on them. Read the console with "
            "comms_console_tail and check whether the draft is still at the prompt before assuming it worked — and do not retry blind, "
            "a repeated Enter has been observed to change nothing.")


comms_console_input.__doc__ = CONSOLE_INPUT_DESCRIPTION

TOOLS = (comms_console_tail, comms_console_input)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
