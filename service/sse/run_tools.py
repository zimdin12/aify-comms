"""Watching and interrupting a dispatched run.

Extracted from `mcp/sse_server.py` in v0.5.4. These two were not adjacent there — `comms_run_status`
sat above the console pair and `comms_run_interrupt` below it — which is why they read as unrelated
in the file and as one subject here: both answer questions about a RUN, not about the agent doing it.

`comms_run_status` IS ALL RENDERER. Four of the run's fields decide one sentence about the reply —
not required, sent, pending, or expected — and a caller waiting on a response acts on which of those
it reads. "reply expected" and "reply pending" are different states of the world: one means nothing
has been sent, the other that the run is still owed. It also truncates events and controls to the
last ten each, which is fine as long as nothing reads the absence of an old event as its absence
from the run.

"Run not found" is deliberately distinct from an empty status: an unknown id and a run with nothing
to report are not the same answer.

THE SUBJECT LINE IS QUOTED, and was not until this move. It echoes text another agent wrote, on a
bare line, in a reply that carries no safety header — the exact rendering an operator reported in
2026-08-11 when an agent read `Restart lc-coder` out of a summary and restarted itself. The fix has
existed since v0.5.1 and this call site never got it, because the gate enforcing it scanned
`service/**` and this code lived under `mcp/`. Moving it into the package is what surfaced it: the
defect did not arrive with the move, it became VISIBLE with it. Everything else here is byte-
identical to what stood in the transport.

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

from service.api_core.serialization import _quote_untrusted_subject
from service.sse.rendering import fence as _fence
from service.sse.api_client import api as _api


async def comms_run_status(runId: str) -> str:
    """Inspect a dispatched run, including recent events and control requests."""
    r = await _api("GET", f"/dispatch/runs/{runId}")
    run = r.get("run")
    if not run:
        return f"Run not found: {runId}"
    if not run.get("requireReply"):
        reply_summary = "reply not required"
    elif run.get("resultMessageId"):
        reply_summary = f"reply sent ({run['resultMessageId']})"
    elif run.get("replyPending"):
        reply_summary = "reply pending"
    else:
        reply_summary = "reply expected"
    lines = [
        f"{run['id']} -> {run['targetAgentId']}",
        f"Status: {run['status']}",
        f"Reply: {reply_summary}",
        f"Runtime: {run.get('runtime') or 'unknown'}",
        f"Subject: {_quote_untrusted_subject(run.get('subject', ''))}",
        f"Requested: {run.get('requestedAt', '')}",
    ]
    # FENCED, like the inbox fences a message body. A run's summary and error are written by the
    # TARGET's runtime — free text from another agent — and this renders them into the reader's
    # context. Bare, a summary that happens to contain an instruction reads as one, which is the
    # operator-reported incident this transport already quotes subjects for. Reported by an external
    # reviewer 2026-08-18 as "run_tools renders summary/body unfenced".
    if run.get("summary"):
        lines.extend(["", "Summary:", _fence(run["summary"])])
    if run.get("error"):
        lines.extend(["", "Error:", _fence(run["error"])])
    events = run.get("events", [])[-10:]
    if events:
        lines.append("")
        lines.append("Recent events:")
        # Event bodies stay INLINE — they belong to a bulleted list and a fence would break it — but
        # they are clipped to one line each. A multi-line body silently destroyed the list structure,
        # and an unbounded one turned a status check into a wall of somebody else's output.
        lines.extend([
            f"- {event['createdAt']} [{event['type']}] {_one_line(event.get('body', ''), 200)}"
            for event in events
        ])
    controls = run.get("controls", [])[-10:]
    if controls:
        lines.append("")
        lines.append("Recent controls:")
        lines.extend([
            f"- {control['requestedAt']} [{control['action']}/{control['status']}] {control.get('from') or 'unknown'}"
            + (f" -> {control['response']}" if control.get("response") else "")
            for control in controls
        ])
    return "\n".join(lines)


async def comms_run_interrupt(runId: str, from_agent: str = "") -> str:
    """Request interruption of an active dispatched run."""
    r = await _api("POST", f"/dispatch/runs/{runId}/control", {
        "from_agent": from_agent,
        "action": "interrupt",
    })
    if not r.get("ok"):
        return r.get("detail", "Interrupt request failed.")
    return f"Interrupt requested for {runId}. Control ID: {r['controlId']}"


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_run_status, comms_run_interrupt)


def _one_line(text: str, limit: int) -> str:
    """Foreign text folded onto a single clipped line, for the bulleted lists below."""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: max(limit - 1, 0)].rstrip() + "…"


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
