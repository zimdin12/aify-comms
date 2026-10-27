"""Channels: group chat across agents (port of `mcp/stdio/channel-tools.mjs`, service mode).

The caller is the request's header: it creates, joins, leaves and deletes as itself. `comms_channel_join`
may still add ANOTHER agent, named by `agentId`. Posting is `comms_channel_send` in send_tools.py, beside
`comms_send`, as in the JS.

Every message body read back is another agent's text: it is fenced so it cannot close the fence and
continue as the reader's own context, under the safety header. Times print in UTC, so every reader sees
the same text whatever its locale.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.clock import ISO_SECONDS
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import SAFETY_HEADER, tool_error
from service.sse.send_tools import SAFE_NAME


def _bad_name(name: str):
    if SAFE_NAME.match(name):
        return None
    return tool_error(f'Invalid channel name: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{name}"')


def _path(channel: str) -> str:
    return f"/channels/{quote(channel, safe='')}"


def _utc(timestamp_ms) -> str:
    if not timestamp_ms:
        return "?"
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime(ISO_SECONDS)


async def comms_channel_create(
    ctx: Context,
    name: str = Field(description="Channel name (e.g. 'backend-team', 'code-review')"),
    description: str | None = Field(default=None, description="Channel description"),
):
    """Create a channel (group chat). You join it automatically."""
    if refusal := _bad_name(name):
        return refusal
    body = {"name": name, "createdBy": caller_id(ctx)}
    if description is not None:
        body["description"] = description
    await call("POST", "/channels", body)
    return f"Channel #{name} created. You're a member."


async def comms_channel_join(
    ctx: Context,
    channel: str = Field(description="Channel name to join"),
    agentId: str | None = Field(default=None, description="Agent to add (omit to join yourself)"),
):
    """Join a channel yourself, or add another agent to a channel."""
    me = caller_id(ctx)
    target = agentId or me
    if refusal := _bad_name(channel):
        return refusal
    r = await call("POST", f"{_path(channel)}/join", {"agentId": target})
    action = "Joined" if target == me else f"Added {target} to"
    return f"{action} #{channel}. Members: {', '.join(r.get('members') or [])}"


async def comms_channel_leave(ctx: Context, channel: str = Field(description="Channel name to leave")):
    """Leave a channel: removes only your membership. The channel and its history stay for every other member."""
    me = caller_id(ctx)
    if refusal := _bad_name(channel):
        return refusal
    r = await call("POST", f"{_path(channel)}/leave", {"agentId": me})
    if not r.get("changed"):
        return f"{me} is not a member of #{channel}; nothing to leave."
    return f"Left #{channel}. Remaining members: {', '.join(r.get('members') or []) or 'none'}"


async def comms_channel_read(
    channel: str = Field(description="Channel name"),
    limit: float | None = Field(default=None, description="Number of messages (default: 20, newest first)"),
):
    """Read recent messages from a channel."""
    if refusal := _bad_name(channel):
        return refusal
    count = int(limit) if limit else 20
    ch = await call("GET", f"{_path(channel)}?limit={count}")
    members = ch.get("members") or []
    messages = ch.get("messages") or []
    if not messages:
        return f"#{channel} -- no messages yet. Members: {', '.join(members)}"
    header = f"#{channel} -- {ch.get('totalMessages')} messages, {len(members)} members ({', '.join(members)})"
    lines = [
        f"[{_utc(m.get('timestamp'))}] {m.get('from')}: ```\n{(m.get('body') or '').replace('```', chr(39) * 3)}\n```"
        for m in messages
    ]
    return f"{SAFETY_HEADER}\n\n{header}\n\n" + "\n\n".join(lines)


async def comms_channel_list():
    """List all channels."""
    channels = (await call("GET", "/channels")).get("channels") or []
    if not channels:
        return "No channels."
    return "\n".join(
        f"#{c.get('name')} -- {c.get('description') or '(no description)'} | {len(c.get('members') or [])} members, {c.get('messageCount')} messages"
        for c in channels
    )


async def comms_channel_delete(ctx: Context, channel: str = Field(description="Channel name to delete")):
    """DESTRUCTIVE. Deletes a channel YOU created, its membership and EVERY message posted to it, shared history for every member. There is no undo. To stop receiving a channel, LEAVE it: deleting ends it for everybody. Only the creator may delete it."""
    try:
        await call("DELETE", f"{_path(channel)}?requestedBy={quote(caller_id(ctx), safe='')}")
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(f"Failed to delete channel: {error}")
    return f"Deleted channel #{channel} and its messages."


TOOLS = (comms_channel_create, comms_channel_join, comms_channel_leave, comms_channel_read, comms_channel_list, comms_channel_delete)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
