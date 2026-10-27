"""Reading what other agents sent you, taking back what you sent, and search (port of
`mcp/stdio/inbox-tools.mjs` and `search-tool.mjs`, service mode).

The inbox is the CALLER's, from the request's header. Every message is another agent's text: the reply
starts with the warning that marks it as data, every body is fenced so it cannot close its fence and go on
as the reader's own context, and every subject goes through the quoter. The truncation note keeps "20
messages" from reading as all of them.

`comms_search` says what it searched and what it did NOT: an empty result is evidence of absence only for
what was read, and the caller cannot tell from the results alone. Whose messages it searches is an
argument, as in the JS, not the caller.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.message_view import _sender_label
from service.api_core.serialization import _quote_untrusted_subject
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import SAFETY_HEADER, tool_error
from service.sse.send_tools import SAFE_NAME
from service.sse.tool_format import iso_millis, js_number


def _sender_of(m: dict) -> str:
    # ABSENT IS NOT FALSE: only a sender the service judged external is labelled as one.
    return _sender_label(str(m.get("from")), registered=m.get("fromRegistered") is not False,
                         origin=str(m.get("origin") or ""), machine=str(m.get("externalMachine") or ""))


def _head(m: dict) -> str:
    read_tag = " [read]" if m.get("_read") or m.get("read") else " [NEW]"
    time = iso_millis(m["timestamp"]) if m.get("timestamp") else "?"
    return (f"--- {m.get('id')}{read_tag} ---\nFrom: {_sender_of(m)}\n"
            f"Type: {m.get('type')} | Subject: {_quote_untrusted_subject(m.get('subject'), 240)}\nTime: {time}")


def _headers_view(m: dict) -> str:
    preview = str(m.get("preview") or m.get("body") or "").strip()
    return _head(m) + (f"\nReply to: {m['inReplyTo']}" if m.get("inReplyTo") else "") + (f"\nPreview: {preview}" if preview else "")


def _full_view(m: dict) -> str:
    body = (m.get("body") or "").replace("```", "'''")
    return _head(m) + "\n" + (f"Reply to: {m['inReplyTo']}\n" if m.get("inReplyTo") else "") + f"\n```\n{body}\n```"


async def comms_inbox(
    ctx: Context,
    filter: Literal["unread", "read", "all"] | None = Field(default=None, description="Which messages (default: unread)"),
    fromAgent: str | None = Field(default=None, description="Filter by sender agent ID"),
    fromRole: str | None = Field(default=None, description="Filter by sender role"),
    type: str | None = Field(default=None, description="Filter by message type"),
    mode: Literal["full", "headers"] | None = Field(default=None, description="full (default) or headers; both mark read"),
    messageId: str | None = Field(default=None, description="Fetch one message by ID, read or unread."),
    limit: float | None = Field(default=None, description="Max messages (default: 20)"),
    peek: bool | None = Field(default=None, description="Leave the returned messages UNREAD."),
):
    """Read your inbox: UNREAD messages by default, 20 at a time. Viewing MARKS MESSAGES READ, mode=headers included; pass peek=true to leave them unread. messageId fetches one message."""
    me = caller_id(ctx)
    if not SAFE_NAME.match(me):
        return tool_error(f'Invalid agent ID: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{me}"')
    view = mode or "full"
    params = {"filter": filter or "unread", "limit": js_number(limit or 20), "mode": view}
    params.update({k: v for k, v in (("fromAgent", fromAgent), ("fromRole", fromRole), ("type", type), ("messageId", messageId)) if v})
    if peek:
        params["peek"] = "1"
    r = await call("GET", f"/messages/inbox/{me}?{urlencode(params)}")
    messages = r.get("messages") or []
    if not messages:
        return f"Message {messageId} not found in inbox." if messageId else "Inbox empty."
    render = _headers_view if view == "headers" else _full_view
    trunc = (f"\n\n(Showing {r.get('showing')} of {r.get('total')}, newest first. Use limit param for more.)"
             if (r.get("total") or 0) > (r.get("showing") or 0) else "")
    return f"{SAFETY_HEADER}\n\n{r.get('total')} message(s):\n\n" + "\n\n".join(render(m) for m in messages) + trunc


async def comms_unsend(ctx: Context, messageId: str = Field(description="The message ID to delete")):
    """Take back a message YOU sent, by its ID."""
    try:
        await call("DELETE", f"/messages/{quote(messageId, safe='')}?requestedBy={quote(caller_id(ctx), safe='')}")
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(f"Failed to delete: {error}")
    return f"Deleted message {messageId}."


async def comms_search(
    query: str = Field(description="Search term (case-insensitive, matches subject + body)"),
    agentId: str | None = Field(default=None, description="Whose messages to search. OMIT AND MESSAGES ARE NOT SEARCHED."),
    scope: Literal["inbox", "shared", "all"] | None = Field(default=None, description="Where to search (default: all)"),
    limit: float | None = Field(default=None, description="Max results (default: 10)"),
):
    """Keyword search over an agent's messages (sent AND received) and shared artifacts. PASS agentId, or messages are NOT searched and an empty result says nothing about them. The response reports what it searched; read it before treating empty as absence."""
    params = {"query": query, "scope": scope or "all", "limit": js_number(limit or 10)}
    if agentId:
        params["agentId"] = agentId
    r = await call("GET", f"/messages/search?{urlencode(params)}")
    searched, skipped = r.get("searched"), r.get("skipped")
    note = f"searched: {' + '.join(searched)}" if isinstance(searched, list) and searched else "searched: nothing"
    warn = (f"\n⚠ NOT searched: {'; '.join(skipped)}. An empty result here is NOT evidence that no such message exists."
            if isinstance(skipped, list) and skipped else "")
    results = r.get("results") or []
    if not results:
        return f'No results for "{query}" ({note}).{warn}'
    lines = [
        f"[MSG] {x.get('id')} | from: {x.get('from')}{' → ' + x['to'] if x.get('to') else ''} | "
        f"{_quote_untrusted_subject(x.get('subject'), 120)}\n  {x.get('preview')}"
        if x.get("type") == "message" else f"[FILE] {x.get('name')} | from: {x.get('from')} | {x.get('description')}"
        for x in results
    ]
    return "\n\n".join(lines) + f"\n\n({note}){warn}"


TOOLS = (comms_inbox, comms_unsend, comms_search)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
