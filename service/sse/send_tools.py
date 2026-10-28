"""Sending work to an agent or a channel (port of `mcp/stdio/send-tools.mjs`, service mode).

The sender is the request's header, never an argument. The send returns before anything claims the
work, so every reply says what was CREATED and names who was NOT started; an agent that reads a hopeful
ack reports a delivery that never happened (sc-manager, 2026-08-18).

`queueIfBusy=true` forces steer off: queueing and steering are opposite delivery choices. An unset
optional field is left out of the request, as the JS bridge's JSON leaves out `undefined`.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal
from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.serialization import _quote_untrusted_subject
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.tool_format import awaiting_reply_note, format_queued_run

ACK_NOTE = (" This ack reports what was CREATED, not what was delivered -- confirm with comms_run_status(...)"
            " before reporting delivery to anyone.")
INCOMPLETE = "The service's answer was incomplete (no usable message id): whether it was stored or woken is not known."
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")

MessageType = Literal["request", "response", "info", "error", "review", "approval"]
Priority = Literal["normal", "high", "urgent"]

COMMS_SEND_DESCRIPTION = (
    "Send a message to an agent by ID, or to every agent with a role. `dashboard` stores it for the operator without waking anyone: send there what they asked for, and problems that need a human. "
    "A new message to an offline, stopped or misconfigured target, or one with no live wake path, is saved unwoken; the answer says which. A reply is always stored. An `available` managed agent is cold-started by the send, and a `blocked` one gets it too. A busy target is steered into its active run when it can steer, and gets it next turn when it cannot. "
    'REPLY WITH A TOOL CALL: comms_send(type="response", inReplyTo=<the message id>). That call is the reply and closes the run; your final plain text is never delivered. Requests, reviews, errors and dashboard asks owe a reply. Leave a response, approval, info or acknowledgement with no new question or work unanswered. Answer input typed into your terminal there. '
    "Omit requireReply to keep the type default: request, review and error owe a reply, the rest do not. Set requireReply=true to track a reply on the rest. If you set requireReply=false on a request, review or error, the recipient is told no reply is tracked while the Work Loop still tracks one. requireReply never affects waking."
)


def _set(**fields) -> dict:
    return {key: value for key, value in fields.items() if value is not None}


def _unavailable(r: dict) -> str:
    skipped = [f"{x.get('targetAgentId')}: {x.get('reason')}" + (f" ({x['recipientStatus']})" if x.get("recipientStatus") else "")
               for x in r.get("notStarted") or []]
    return f"\nUnavailable: {'; '.join(skipped)}" if skipped else ""


def _stored_id(r: dict) -> str:
    message_id = r.get("messageId")
    return message_id if isinstance(message_id, str) and message_id.strip() else ""


def _triggered_result(r: dict, from_agent: str, to: str | None, type: str, require_reply: bool | None) -> str:
    if r.get("replayed"):
        if not _stored_id(r):
            return INCOMPLETE
        return (f"Already sent: this matched an earlier send of the same message ({_stored_id(r)}), which the service stored. "
                "Whether that send woke its recipients is not known from this answer, and nothing was woken again.")
    queued = [format_queued_run(x) for x in r.get("dispatchRuns") or []]
    consoles = [x.get("agentId") or x.get("targetAgentId") or x.get("terminalId") for x in r.get("consoleDeliveries") or []]
    consoles = [c for c in consoles if c]
    unwoken = [f"{x.get('targetAgentId')}: {x.get('reason')}" for x in r.get("notStarted") or []]
    lines = []
    if queued:
        lines.append(f"Sent. Dispatch: {', '.join(queued)}.{ACK_NOTE}")
    if consoles:
        lines.append(f"Sent. Typed into the console of {', '.join(consoles)} (queued input, not proof the agent read it).")
    if unwoken:
        lines.append(f"Saved in the inbox, not woken: {'; '.join(unwoken)}.")
        if r.get("notice"):
            lines.append(r["notice"])
    if not lines:
        lines.append(f"Stored ({_stored_id(r)}). Whether a wake happened is not known from this answer." if _stored_id(r) else INCOMPLETE)
    return "\n".join(lines) + awaiting_reply_note(from_agent, to, type, require_reply)


async def comms_send(
    ctx: Context,
    type: MessageType = Field(description="Message type"),
    subject: str = Field(description="Short subject"),
    body: str = Field(description="Message content"),
    to: str | None = Field(default=None, description="Target agent ID"),
    toRole: str | None = Field(default=None, description="Every agent with this role; wakes only running ones"),
    priority: Priority | None = Field(default=None, description="Message priority (default: normal)"),
    inReplyTo: str | None = Field(default=None, description="Message ID this replies to"),
    steer: bool | None = Field(default=None, description="Default true: a busy target that can steer gets it between tool calls; one that cannot gets it next turn."),
    queueIfBusy: bool | None = Field(default=None, description="true: deliver next turn, behind its current work, even if it could steer."),
    requireReply: bool | None = Field(default=None, description="Leave unset; see the description."),
):
    if not to and not toRole:
        return tool_error("Error: need 'to' or 'toRole'")
    sender = caller_id(ctx)
    force_queue = queueIfBusy is True
    r = await call("POST", "/messages/send", _set(
        from_agent=sender, to=to, toRole=toRole, type=type, subject=subject, body=body, priority=priority or "normal",
        inReplyTo=inReplyTo, trigger=True, steer=False if force_queue else (True if steer is None else steer),
        queueIfBusy=force_queue, requireReply=requireReply, clientNonce=str(uuid.uuid4()),
    ))
    if not r.get("ok"):
        return tool_error(f"{r.get('error') or 'Message was not sent.'}{_unavailable(r)}")
    recipients = r.get("recipients") or []
    dashboard_only = bool(recipients) and all(rid == "dashboard" for rid in recipients)
    if r.get("replayed") or (recipients and not dashboard_only):
        return _triggered_result(r, sender, to, type, requireReply)
    parts = []
    for rid in recipients:
        info = (r.get("recipientStatus") or {}).get(rid)
        parts.append(f"{rid} [{info.get('status')}, {info.get('unread')} unread]" if info else rid)
    return f"Sent ({r.get('messageId')}) to {', '.join(parts)}. Subject: {_quote_untrusted_subject(subject, 240)}"


comms_send.__doc__ = COMMS_SEND_DESCRIPTION


async def comms_channel_send(
    ctx: Context,
    channel: str = Field(description="Channel name"),
    body: str = Field(description="Message content"),
    type: MessageType | None = Field(default=None, description="Message type (default: info)"),
    priority: Priority | None = Field(default=None, description="Message priority (default: normal)"),
    steer: bool | None = Field(default=None, description="Default true: busy members that can steer get it between tool calls; the rest get it next turn."),
    queueIfBusy: bool | None = Field(default=None, description="true: deliver next turn for everyone. Overrides steer."),
):
    """Post to a channel. The post and every member's inbox copy are always stored. Members that cannot be woken (offline, stopped, misconfigured, or no live wake path) are listed under Not started. Busy members are steered or queued as with comms_send."""
    if not SAFE_NAME.match(channel):
        return tool_error(f'Invalid channel name: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{channel}"')
    force_queue = queueIfBusy is True
    r = await call("POST", f"/channels/{quote(channel, safe='')}/send", {
        "from_agent": caller_id(ctx), "channel": channel, "body": body, "type": type or "info", "priority": priority or "normal",
        "trigger": True, "steer": False if force_queue else (True if steer is None else steer), "queueIfBusy": force_queue,
    })
    if not r.get("ok"):
        return tool_error(f"{r.get('error') or f'Channel message to #{channel} was not sent.'}{_unavailable(r)}")
    if r.get("dispatchRuns") or r.get("notStarted"):
        queued = [format_queued_run(x) for x in r.get("dispatchRuns") or []]
        skipped = [f"{x.get('targetAgentId')}: {x.get('reason')}" for x in r.get("notStarted") or []]
        # No run created is not a start: the post is stored and every member's copy waits in its inbox.
        outcome = (f"Dispatch: {', '.join(queued)}. This ack reports what was CREATED, not what was delivered -- confirm with "
                   "comms_run_status(...) before reporting delivery." if queued
                   else "No member was woken: the post and every member's inbox copy are stored.")
        return f"Sent to #{channel}. {outcome}" + (f"\nNot started: {'; '.join(skipped)}" if skipped else "")
    return f"Sent to #{channel} ({len(r.get('members') or [])} members)."


TOOLS = (comms_send, comms_channel_send)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
