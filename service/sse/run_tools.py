"""Dispatched runs and who owes whom (port of `mcp/stdio/dispatch-tools.mjs`, service mode).

`comms_run_status` is all renderer: "reply expected" and "reply pending" are different states (nothing
sent yet, against still owed), and events and controls show the last ten each, so an old event missing
here is not missing from the run. Its subject is another agent's text and goes through the quoter (an
agent once restarted itself on reading `Restart lc-coder` out of a summary, 2026-08-11).

The interrupts act as the CALLER, from the request's header. `comms_contracts` filters by sender with
`fromAgent`, as `comms_inbox` and `comms_files` do.
"""

from __future__ import annotations

import math
from typing import Literal
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.serialization import _quote_untrusted_subject
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import fence, tool_error
from service.sse.tool_format import js_number

ContractState = Literal["open", "overdue", "working", "queued", "seen", "sent", "missing_reply", "failed", "answered", "closed"]


def _reply_summary(run: dict) -> str:
    if not run.get("requireReply"):
        return "reply not required"
    if run.get("resultMessageId"):
        return f"reply sent ({run['resultMessageId']})"
    return "reply pending" if run.get("replyPending") else "reply expected"


def _one_line(text, limit: int) -> str:
    """Foreign text folded onto a single clipped line, for a bulleted list."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: max(limit - 1, 0)].rstrip() + "…"


def _js_round(value: float) -> float:
    """JS Math.round: halves go up, not to even."""
    return math.floor(value + 0.5)


def _age(minutes) -> str:
    try:
        age = float(minutes or 0)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(age):
        return "?"
    return f"{js_number(_js_round(age / 6) / 10)}h" if age >= 60 else f"{js_number(_js_round(age))}m"


def _contract_line(c: dict) -> str:
    state = str(c.get("state") or "sent").replace("_", " ").upper()
    reminders = f", reminders={c['reminderCount']}" if c.get("reminderCount") else ""
    answer = f"\n  answer: {str(c['resultPreview'])[:180]}" if c.get("resultPreview") else ""
    return (f"- {state} {c.get('from') or '?'} -> {c.get('targetAgentId') or '?'} ({_age(c.get('ageMinutes'))}{reminders}) "
            f"{c.get('subject') or c.get('id') or '(no subject)'}{answer}")


async def comms_run_status(runId: str = Field(description="Dispatch run ID")):
    """Inspect a dispatched run: its status, recent events, and any control requests against it."""
    run = (await call("GET", f"/dispatch/runs/{quote(runId, safe='')}"))["run"]
    # The summary, error and event bodies are the TARGET runtime's text: fenced, and folded onto one line.
    events = [f"- {e.get('createdAt')} [{e.get('type')}] {_one_line(e.get('body'), 200)}" for e in (run.get("events") or [])[-10:]]
    controls = [f"- {c.get('requestedAt')} [{c.get('action')}/{c.get('status')}] {c.get('from') or 'unknown'}"
                + (f" -> {c['response']}" if c.get("response") else "") for c in (run.get("controls") or [])[-10:]]
    blocked = run.get("blockedByActiveRun") or {}
    lines = [
        f"{run.get('id')} -> {run.get('targetAgentId')}",
        f"Status: {run.get('status')}",
        f"Reply: {_reply_summary(run)}",
        f"Runtime: {run.get('runtime') or 'unknown'}",
        f"Subject: {_quote_untrusted_subject(run.get('subject'), 240)}",
        f"Requested: {run.get('requestedAt')}",
    ]
    lines += [f"Started: {run['startedAt']}"] if run.get("startedAt") else []
    lines += [f"Finished: {run['finishedAt']}"] if run.get("finishedAt") else []
    if blocked.get("runId"):
        subject = f" ({_quote_untrusted_subject(blocked['subject'], 120)})" if blocked.get("subject") else ""
        lines.append(f"Blocked by active run: {blocked['runId']}{subject}")
    lines += [f"Thread: {run['externalThreadId']}"] if run.get("externalThreadId") else []
    lines += [f"Turn: {run['externalTurnId']}"] if run.get("externalTurnId") else []
    text = "\n".join(lines) + "\n"
    text += f"\nSummary:\n{fence(run['summary'])}\n" if run.get("summary") else ""
    text += f"\nError:\n{fence(run['error'])}\n" if run.get("error") else ""
    text += f"\nRecent events:\n" + "\n".join(events) if events else ""
    text += f"\nRecent controls:\n" + "\n".join(controls) if controls else ""
    return text


async def comms_contracts(
    agentId: str | None = Field(default=None, description="Contracts this agent owes"),
    fromAgent: str | None = Field(default=None, description="Contracts owed to this sender"),
    state: ContractState | None = Field(default=None, description="Filter by computed contract state. Defaults to open."),
    category: Literal["direct", "channel", "self_wake"] | None = Field(default=None, description="Defaults to direct."),
    includeClosed: bool | None = Field(default=None, description="Include answered/closed recent contracts. Default false."),
    limit: int | None = Field(default=None, ge=1, le=200, description="Max contracts to return. Default 25."),
):
    """Who owes whom a reply or work, derived from messages and runs: what is open, what is overdue, and whether unread counts are real work."""
    params = {k: v for k, v in (("agentId", agentId), ("fromAgent", fromAgent)) if v}
    params.update(state=state or "open", category=category or "direct")
    if includeClosed:
        params["includeClosed"] = "true"
    params["limit"] = str(limit or 25)
    r = await call("GET", f"/contracts?{urlencode(params)}")
    contracts, s = r.get("contracts") or [], r.get("summary") or {}
    header = (f"Contracts: {s.get('total') or len(contracts)}; open={s.get('open') or 0}; overdue={s.get('overdue') or 0}; "
              f"working={s.get('working') or 0}; queued={s.get('queued') or 0}; missingReply={s.get('missingReply') or 0}; "
              f"answered={s.get('answered') or 0}")
    return f"{header}\n" + ("\n".join(_contract_line(c) for c in contracts) if contracts else "No matching contracts.")


async def comms_run_interrupt(ctx: Context, runId: str = Field(description="Dispatch run ID")):
    """Interrupt an active dispatched RUN, or cancel one still queued. No dispatched run? Use comms_interrupt."""
    try:
        r = await call("POST", f"/dispatch/runs/{quote(runId, safe='')}/control", {"from_agent": caller_id(ctx), "action": "interrupt"})
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(str(error))
    if r.get("status") == "cancelled":
        return f"{runId} was still queued, so it was cancelled before delivery."
    return f"Interrupt requested for {runId}. Control ID: {r.get('controlId')}"


async def comms_interrupt(ctx: Context, agentId: str = Field(description="Target agent ID")):
    """Send Ctrl+C to a managed agent's console. It also stops turns typed directly into the TUI. For a dispatched run use comms_run_interrupt, which is tracked."""
    try:
        r = await call("POST", f"/agents/{quote(agentId, safe='')}/console/input", {"text": "\u0003", "enter": False, "from": caller_id(ctx)})
    except Exception as error:  # noqa: BLE001
        return tool_error(str(error))
    if not r.get("ok"):
        return tool_error(r.get("message") or f"Could not interrupt {agentId}.")
    return f"Interrupted {agentId} through its live console (terminal {r.get('terminalId')}, control {r.get('controlId')})."


TOOLS = (comms_run_status, comms_contracts, comms_run_interrupt, comms_interrupt)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
