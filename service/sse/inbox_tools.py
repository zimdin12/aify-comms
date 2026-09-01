"""Reading what other agents sent you: the inbox, and search across it.

Extracted from `mcp/sse_server.py` in v0.5.4 — a whole subject, bodies untouched.

THESE TWO ARE THE REASON THE SSE RENDERERS WERE AUDITED AT ALL. `comms_search` shipped a bare
"No results" whether or not messages had actually been consulted; the same defect was fixed in the
stdio bridge first, and this copy survived because nothing called it. What it renders now is what
WAS searched and what was NOT, because an empty result is only evidence of absence if the record was
read — and the caller cannot tell the difference from the payload.

`comms_inbox` carries the same weight from the other side: it prepends the warning that marks the
content as data rather than instructions, and fences every body so a message cannot close its own
fence and continue as prose the reader treats as its own context. Its `headers` mode exists so an
agent can triage without marking anything read, and the truncation note exists so "20 messages" is
never mistaken for all of them.

`test_sse_renderers.py` drives both. It used to patch `sse_server._api` and now patches this
module's — these tools resolve `_api` from here, so a patch on the transport would silently
intercept nothing and the tests would reach for the network instead.
"""

from __future__ import annotations

from service.sse.api_client import api as _api
from service.api_core.serialization import _quote_untrusted_subject
from service.sse.rendering import SAFETY_HEADER, fence as _fence


async def comms_inbox(
    agentId: str,
    filter: str = "unread",
    fromAgent: str = "",
    fromRole: str = "",
    type: str = "",
    mode: str = "full",
    messageId: str = "",
    limit: int = 20,
    peek: bool = False,
) -> str:
    """Check your inbox. Returns only UNREAD messages by default. Viewing MARKS MESSAGES READ, mode='headers' included -- pass peek=true to leave them unread. messageId fetches one message by ID."""
    params = {"filter": filter, "limit": str(limit), "mode": mode}
    # SET ONLY WHEN TRUE. The route gates on `bool(peek)` over a STRING, so any non-empty value
    # counts and sending "false" would read as peek. Omitting the key is the only way to say no.
    if peek:
        params["peek"] = "1"
    if fromAgent:
        params["fromAgent"] = fromAgent
    if fromRole:
        params["fromRole"] = fromRole
    if type:
        params["type"] = type
    if messageId:
        params["messageId"] = messageId
    r = await _api("GET", f"/messages/inbox/{agentId}", params=params)
    if "detail" in r:
        return f"Error: {r['detail']}"
    msgs = r.get("messages", [])
    if not msgs:
        return f"Message {messageId} not found in inbox." if messageId else "Inbox empty."
    lines = []
    for m in msgs:
        if mode == "headers":
            preview = str(m.get("preview", "")).strip()
            parts = [
                f"--- {m['id']} ---",
                f"From: {m['from']} | Type: {m['type']} | "
                f"Subject: {_quote_untrusted_subject(m.get('subject', ''), 240)}",
            ]
            if m.get("inReplyTo"):
                parts.append(f"Reply to: {m['inReplyTo']}")
            if preview:
                parts.append(f"Preview: {preview}")
            lines.append("\n".join(parts))
        else:
            safe_body = _fence(m.get("body", ""))
            lines.append(
                f"--- {m['id']} ---\n"
                f"From: {m['from']} | Type: {m['type']} | "
                f"Subject: {_quote_untrusted_subject(m.get('subject', ''), 240)}\n"
                f"{safe_body}"
            )
    trunc = f"\n\n(Showing {r['showing']} of {r['total']})" if r.get("total", 0) > r.get("showing", 0) else ""
    return f"{SAFETY_HEADER}\n\n{r['total']} message(s):\n\n" + "\n\n".join(lines) + trunc


async def comms_search(
    query: str,
    agentId: str = "",
    scope: str = "all",
    limit: int = 10,
) -> str:
    """Search an agent's messages (sent AND received) and shared artifacts by keyword.

    PASS agentId, or messages are NOT searched at all and you only get shared files — an empty
    result would then say nothing about whether the message exists. The reply always states what
    was actually searched; read it before treating an empty result as absence.
    """
    params = {"query": query, "scope": scope, "limit": str(limit)}
    if agentId:
        params["agentId"] = agentId
    r = await _api("GET", "/messages/search", params=params)
    # AN OUTAGE IS NOT AN ANSWER. `_api` returns `detail` on any error precisely so every caller
    # can branch on it, and that fix's own note says "every caller in this package checks" -- this
    # one did not, so a 500 rendered as a confident fact about the fleet.
    if "detail" in r:
        return f"Error: {r['detail']}"
    results = r.get("results", [])
    # SAY WHAT WAS SEARCHED. This transport had the same defect as the stdio bridge: it printed a
    # bare 'No results' whether the record had been consulted or not. The server-side fix returns
    # `searched`/`skipped`, but a renderer that drops them leaves the caller with the identical
    # fail-open answer — an empty result read as "no such message exists" when messages were never
    # looked at. Fixing one transport and not the other would have left half the fleet misled.
    searched = r.get("searched") or []
    skipped = r.get("skipped") or []
    scope_note = f"searched: {' + '.join(searched)}" if searched else "searched: nothing"
    warn = (
        f"\n⚠ NOT searched: {'; '.join(skipped)}. "
        "An empty result here is NOT evidence that no such message exists."
        if skipped else ""
    )
    if not results:
        return f'No results for "{query}" ({scope_note}).{warn}'
    lines = []
    for x in results:
        if x.get("type") == "message":
            # No NEW/read marker. The search endpoint does not return read state, so this was
            # always "MSG NEW" — including for messages the agent sent itself. A marker that is
            # always on carries no information and quietly misleads.
            to = f" → {x['to']}" if x.get("to") else ""
            # QUOTED, like every other echo of somebody else's subject. A search result strips the
            # addressing off a message written BY one agent FOR another and renders it into a third
            # agent's context — the exact shape that made an agent act on a request aimed elsewhere.
            # This site was missed when the rule was written because the scan keys on a `Subject:`
            # LABEL and this line has none.
            lines.append(
                f"[MSG] {x['id']} | from: {x['from']}{to} | "
                f"{_quote_untrusted_subject(x.get('subject', ''), 120)}\n  {x.get('preview', '')}"
            )
        else:
            lines.append(f"[FILE] {x['name']} | from: {x.get('from', '?')} | {x.get('description', '')}")
    return "\n\n".join(lines) + f"\n\n({scope_note}){warn}"


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_inbox, comms_search)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
