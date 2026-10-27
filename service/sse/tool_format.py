"""Renderers shared by several comms tools (port of `mcp/stdio/tool-response-format.mjs`).

Each says only what was observed: a run is "queued, not yet claimed" until something claims it, and an
agent's inbound facts (unread, last read) are labelled inbound, because during the 2026-08-10 outage every
one of them was true while a reply sat undelivered, and a manager read them three times as a dead lane.
Subjects are another agent's text, so they are quoted onto one line by the service's own quoting.
"""

from __future__ import annotations

from service.api_core.reply_expectation import _message_type_expects_reply
from service.api_core.serialization import _quote_untrusted_subject


def format_dispatch_state(info: dict) -> str:
    state = info.get("dispatchState") or {}
    active = state.get("activeRun") or {}
    lines = []
    if active.get("runId"):
        lines.append(f"  Active run: {active['runId']} [{active.get('status') or 'running'}]")
        if active.get("subject"):
            lines.append(f"    Subject: {_quote_untrusted_subject(active['subject'], 240)}")
    if int(state.get("queuedRuns") or 0) > 0:
        lines.append(f"  Queued runs: {state['queuedRuns']}")
    return "\n".join(lines)


def format_queued_run(run: dict) -> str:
    """One dispatch run as the sender should read it. Only a steer is an observed delivery; a fresh run is
    "queued, not yet claimed" (sc-manager reported "Delivered" on an ack that could not tell, 2026-08-18)."""
    text = f"{run.get('targetAgentId')} ({run.get('runId')})"
    if run.get("steered") or run.get("status") == "steered":
        target = run.get("steeredIntoActiveRun") or {}
        text += f" steered into active run {target.get('runId') or run.get('runId')}"
        if target.get("subject"):
            text += f" ({_quote_untrusted_subject(target['subject'], 120)})"
        return text
    if run.get("merged") and int(run.get("mergedCount") or 0) > 1:
        text += f" buffered {run['mergedCount']} updates"
    behind = run.get("queuedBehindActiveRun") or {}
    if behind.get("runId"):
        text += f" queued behind active run {behind['runId']}"
        if behind.get("subject"):
            text += f" ({_quote_untrusted_subject(behind['subject'], 120)})"
        return text
    return f"{text} queued, not yet claimed"


def awaiting_reply_note(from_agent: str, to: str | None, type: str, require_reply: bool | None) -> str:
    """Said when a reply is tracked (an explicit requireReply wins over the type), never to yourself."""
    owed = require_reply is True or (require_reply is not False and _message_type_expects_reply(type))
    if not owed or (to and to == from_agent):
        return ""
    return (" The reply arrives as a new message that wakes you; until it does you know nothing about its result,"
            " so do not report, predict or redo that work.")


def format_outbound_activity(info: dict) -> str:
    """What the agent last PRODUCED. An absent `outbound` is an older service, not "nothing produced"."""
    outbound = info.get("outbound") or {}
    sent = str(outbound.get("lastSentAt") or "").strip()
    ran = str(outbound.get("lastCompletedRunAt") or "").strip()
    if not sent and not ran:
        if "outbound" in info:
            return "Last produced: none recorded (the service answered — no message sent, no run completed)"
        return "Last produced: unknown (service did not report outbound activity — pre-v0.3.1 service)"
    bits = ([f"sent {sent}"] if sent else []) + ([f"completed a run {ran}"] if ran else [])
    return f"Last produced (OUTBOUND): {'; '.join(bits)}"
