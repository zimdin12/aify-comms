"""Sending work to another agent: the ordinary path, and the lower-level one.

Extracted from `mcp/sse_server.py` in v0.5.4 — bodies untouched.

TWO TOOLS THAT LOOK ALIKE AND ARE NOT, which is exactly why their descriptions carry the difference.
`comms_send` is the ordinary teamwork verb and already fails visibly when live delivery is
impossible. `comms_dispatch` is run-control and debug: it takes `requireStart`, it does not steer,
and its reply tells the caller to prefer `comms_send` for normal messages. An agent choosing between
them reads those sentences, so they are part of what moved.

BOTH RENDER WHO WILL *NOT* RECEIVE THE MESSAGE. A reply that names only the launched recipients lets
a caller believe a team was reached when half of it was skipped — the same wrong-belief shape the
channel and search renderers were audited for.

`steer` and `queueIfBusy` are not independent: queueing and steering are opposite delivery choices,
so `queueIfBusy=true` forces steer off in the request regardless of what was asked for.

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

from service.api_core.serialization import _quote_untrusted_subject
from service.sse.api_client import api as _api


async def comms_send(
    from_agent: str,
    type: str,
    subject: str,
    body: str,
    to: str = "",
    toRole: str = "",
    inReplyTo: str = "",
    priority: str = "normal",
    silent: bool = False,
    steer: bool | None = None,
    queueIfBusy: bool = False,
    requireReply: bool | None = None,
) -> str:
    """Send a live-gated message to an agent by ID or role. Offline/stopped/no-wake targets fail without storing. Busy steer-capable targets receive ordinary sends as current-run steer; busy live non-steer targets queue/merge as next-turn work. Set queueIfBusy=true only when you intentionally want next-turn delivery even if steering is available. Reply tracking: omit requireReply for type defaults (request/review/error=true; info/response/approval=false); set true only when a normally optional message needs a tracked response, and false only for intentional fire-and-forget. requireReply does not control delivery or waking. Use silent=true only for legacy inbox-only delivery."""
    if not to and not toRole:
        return "Error: need 'to' or 'toRole'"
    should_trigger = not silent
    force_queue = bool(queueIfBusy)
    data = {
        "from_agent": from_agent,
        "type": type,
        "subject": subject,
        "body": body,
        "priority": priority,
        "trigger": should_trigger,
        "steer": False if force_queue else (steer if steer is not None else True),
        "queueIfBusy": force_queue,
        "requireReply": requireReply,
    }
    if to:
        data["to"] = to
    if toRole:
        data["toRole"] = toRole
    if inReplyTo:
        data["inReplyTo"] = inReplyTo
    r = await _api("POST", "/messages/send", data)
    if not r.get("ok"):
        return r.get("error", "No recipients found.")
    if should_trigger and r.get("recipients"):
        queued = [
            f"{run.get('targetAgentId', '?')} [{run.get('status', 'queued')}]"
            + (f" -> {run.get('runId')}" if run.get("runId") else "")
            for run in r.get("dispatchRuns", [])
        ]
        skipped = [f"{item.get('targetAgentId', '?')}: {item.get('reason', 'not started')}" for item in r.get("notStarted", [])]
        note = f"Sent + live delivery for {', '.join(queued) if queued else 'no launchable recipients'}."
        if skipped:
            note += f" Not started: {'; '.join(skipped)}."
        note += " Use comms_run_status(...) to inspect progress. Request-type sends expect an explicit reply by default, and the bridge mirrors the result if none is sent."
        return note
    # The sender's OWN subject, read back to the sender — not the foreign-text case the quoter was
    # built for. Quoted anyway so the rule has no exceptions: an echo site that is safe "because of
    # who wrote the text" is one refactor away from being reached by text somebody else wrote, and a
    # gate with a carve-out is a gate somebody has to re-justify.
    return (
        f"Sent ({r['messageId']}) to {', '.join(r['recipients'])}. "
        f"Subject: {_quote_untrusted_subject(subject, 240)}"
    )


async def comms_dispatch(
    from_agent: str,
    type: str,
    subject: str,
    body: str,
    to: str = "",
    toRole: str = "",
    inReplyTo: str = "",
    requireStart: bool = False,
    requireReply: bool | None = None,
) -> str:
    """Lower-level tracked run-control/debug API. Normal teamwork should use comms_send, which already fails visibly when live delivery is unavailable. Direct dispatch expects a reply by default unless requireReply=false."""
    if not to and not toRole:
        return "Error: need 'to' or 'toRole'"
    data = {
        "from_agent": from_agent,
        "type": type,
        "subject": subject,
        "body": body,
        "mode": "require_start" if requireStart else "start_if_possible",
        "createMessage": True,
        "requireReply": requireReply,
    }
    if to:
        data["to"] = to
    if toRole:
        data["toRole"] = toRole
    if inReplyTo:
        data["inReplyTo"] = inReplyTo
    r = await _api("POST", "/dispatch", data)
    if not r.get("ok"):
        return r.get("error", "Dispatch failed.")
    runs = r.get("runs", [])
    not_started = r.get("notStarted", [])
    lines = [f"- {run['targetAgentId']}: {run['runId']} [{run['status']}]" for run in runs]
    if not_started:
        lines.append("Not started:")
        lines.extend([f"- {item['targetAgentId']}: {item['reason']}" for item in not_started])
    if not lines:
        return "No dispatch runs were created."
    if requireStart:
        lines.extend(["", "Use comms_run_status(...) to inspect progress. For normal teamwork messages, prefer comms_send(...); it already fails visibly when live delivery is not possible."])
    else:
        lines.extend(["", "Use comms_run_status(...) to inspect progress. Direct dispatch expects an explicit reply by default, and the bridge mirrors the result if none is sent."])
    return "\n".join(lines)


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_send, comms_dispatch)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
