"""The five channel tools: group chat across agents, over SSE.

Extracted from `mcp/sse_server.py` in v0.5.4 — a whole subject, moved with its bodies untouched.

TWO OF THESE FORM CONCLUSIONS rather than relaying a payload, which is the class of bug the SSE
renderers were audited for. `comms_channel_read` decides whether "no messages yet" is the honest
answer and wraps every body in a fence so a message cannot escape into the reader's own context;
`comms_channel_send` turns a dispatch result into a sentence about who WILL receive it, including
the recipients the server declined to start. A caller acts on those sentences, and until now nothing
called a line of either.

`_api`, `_fence` and `SAFETY_HEADER` are imported under the private names the bodies already used,
so not a character of them moved. NOTE FOR ANYONE WRITING A TEST: the transport's own tests swap
`sse_server._api`; these tools resolve `_api` from THIS module, so patch it here — a patch on the
transport now silently affects nothing.
"""

from __future__ import annotations

from service.sse.api_client import api as _api
from service.sse.rendering import SAFETY_HEADER, fence as _fence


async def comms_channel_create(name: str, from_agent: str, description: str = "") -> str:
    """Create a new channel (group chat) for multiple agents to communicate."""
    r = await _api("POST", "/channels", {"name": name, "createdBy": from_agent, "description": description})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f"Channel #{name} created. You're a member."


async def comms_channel_join(channel: str, from_agent: str) -> str:
    """Join an existing channel."""
    r = await _api("POST", f"/channels/{channel}/join", {"agentId": from_agent})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f"Joined #{channel}. Members: {', '.join(r.get('members', []))}"


async def comms_channel_send(
    channel: str,
    from_agent: str,
    body: str,
    type: str = "info",
    priority: str = "normal",
    silent: bool = False,
    steer: bool | None = None,
    queueIfBusy: bool = False,
) -> str:
    """Send a live-gated message to a channel. Offline/stale/stopped/no-wake members fail the send without storing. Busy steer-capable members receive ordinary sends as current-run steer; busy live non-steer members queue/merge as next-turn work. Set queueIfBusy=true only when you intentionally want next-turn delivery even if steering is available."""
    should_trigger = not silent
    force_queue = bool(queueIfBusy)
    r = await _api("POST", f"/channels/{channel}/send", {
        "from_agent": from_agent, "channel": channel, "body": body, "type": type, "priority": priority,
        "trigger": should_trigger, "silent": silent, "steer": False if force_queue else (steer if steer is not None else True),
        "queueIfBusy": force_queue,
    })
    if "detail" in r:
        return f"Error: {r['detail']}"
    if should_trigger and (r.get("dispatchRuns") or r.get("notStarted")):
        queued = [
            (
                f"{run.get('targetAgentId', '?')} ({run.get('runId', '?')})"
                + f" [{run.get('status', 'queued')}]"
                + (
                    f" queued behind active run {run['queuedBehindActiveRun']['runId']}"
                    if run.get("queuedBehindActiveRun", {}).get("runId")
                    else ""
                )
            )
            for run in r.get("dispatchRuns", [])
        ]
        skipped = [f"{item.get('targetAgentId', '?')}: {item.get('reason', 'not started')}" for item in r.get("notStarted", [])]
        note = f"Sent to #{channel} with live delivery for {', '.join(queued) if queued else 'no launchable recipients'}."
        if skipped:
            note += f" Not started: {'; '.join(skipped)}."
        note += " Use comms_run_status(...) to inspect progress."
        return note
    return f"Sent to #{channel} ({r.get('members', {})  if isinstance(r.get('members'), int) else len(r.get('members', []))} members)."


async def comms_channel_read(channel: str, limit: int = 20) -> str:
    """Read recent messages from a channel."""
    r = await _api("GET", f"/channels/{channel}", params={"limit": str(limit)})
    if "detail" in r:
        return f"Error: {r['detail']}"
    msgs = r.get("messages", [])
    if not msgs:
        return f"#{channel} -- no messages yet. Members: {', '.join(r.get('members', []))}"
    header = f"#{channel} -- {r.get('totalMessages', len(msgs))} messages, {len(r.get('members', []))} members ({', '.join(r.get('members', []))})"
    lines = []
    for m in msgs:
        t = m.get("timestamp", "")
        safe_body = _fence(m.get("body", ""))
        lines.append(f"[{t}] {m.get('from', '?')}: {safe_body}")
    return f"{SAFETY_HEADER}\n\n{header}\n\n" + "\n\n".join(lines)


async def comms_channel_list() -> str:
    """List all channels."""
    r = await _api("GET", "/channels")
    # AN OUTAGE IS NOT AN ANSWER. `_api` returns `detail` on any error precisely so every caller
    # can branch on it, and that fix's own note says "every caller in this package checks" -- this
    # one did not, so a 500 rendered as a confident fact about the fleet.
    if "detail" in r:
        return f"Error: {r['detail']}"
    channels = r.get("channels", [])
    if not channels:
        return "No channels."
    lines = [
        f"#{c['name']} -- {c.get('description', '(no description)')} | "
        f"{c.get('members', 0) if isinstance(c.get('members'), int) else len(c.get('members', []))} members, "
        f"{c.get('messageCount', 0)} messages"
        for c in channels
    ]
    return "\n".join(lines)


async def comms_channel_delete(channel: str, requestedBy: str) -> str:
    """Delete a channel you created, along with its messages.

    THE MOST DESTRUCTIVE DELETE AN AGENT CAN REACH: it removes the channel, its membership and EVERY
    MESSAGE ever posted to it — shared history for every member, not just your own. There was no tool
    for it until 2026-08-18, and the endpoint had no ownership check either; both were fixed together
    rather than exposing the hole.

    To stop receiving a channel's messages, LEAVE it. Deleting ends it for everybody, so only the
    creator or an operator surface may do so and the service enforces that.
    """
    r = await _api("DELETE", f"/channels/{channel}", params={"requestedBy": requestedBy})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f"Deleted channel #{channel} and its messages."


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (
    comms_channel_create,
    comms_channel_join,
    comms_channel_send,
    comms_channel_read,
    comms_channel_list,
    comms_channel_delete,
)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
