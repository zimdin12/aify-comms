"""
MCP Server - SSE Transport (runs inside the Docker container)

This MCP server is mounted into the FastAPI app and accessible via SSE at:
  http://<host>:<port>/mcp/sse

AI agents building on this template should:
1. Register tools that expose the service's core functionality
2. Each tool should be self-documenting with clear descriptions
3. Tools should handle errors gracefully and return helpful messages

The tools registered here become available to any MCP-compatible client
(Claude Code, OpenClaw, Cursor, etc.)
"""

import logging
from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP

from service.config import get_config
# Layer-0 leaves that used to sit in this file. Imported under their ORIGINAL private names so every
# call site below is unchanged by the move — and so `test_sse_renderers.py`, which swaps `_api` for a
# canned payload, still patches the name the tool bodies resolve.
#
# They live under `service/` rather than beside this file because `mcp/` is NOT this repo's package:
# `import mcp` resolves to the PyPI distribution the line above imports FastMCP from, which is why
# `service/main.py` loads THIS file by path instead of importing it. See `service/sse/__init__.py`.
from service.sse.api_client import api as _api
from service.sse.channel_tools import register as _register_channel_tools
from service.sse.container_tools import (
    bind_app as _bind_container_app,
    get_manager as _get_manager,
    register as _register_container_tools,
)
from service.sse.inbox_tools import register as _register_inbox_tools
from service.sse.management_tools import register as _register_management_tools
from service.sse.shared_file_tools import register as _register_shared_file_tools

logger = logging.getLogger(__name__)

# Context variables for per-request user/client tracking
user_id_var: ContextVar[str] = ContextVar("user_id", default="default")
client_name_var: ContextVar[str] = ContextVar("client_name", default="unknown")

# Create MCP server instance
config = get_config()
mcp_server = FastMCP(config.name)

# ---------------------------------------------------------------------------
# Service Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def service_info() -> dict:
    """Get information about this service, its capabilities, managed containers, and available tools."""
    cfg = get_config()
    result = {
        "name": cfg.name,
        "version": cfg.version,
        "description": cfg.description,
        "status": "running",
    }
    manager = _get_manager()
    if manager:
        result["containers"] = manager.list_containers()
        result["groups"] = manager.get_groups()
    return result


@mcp_server.tool()
async def service_health() -> dict:
    """Check if the service and its dependencies are healthy."""
    checks = {}
    manager = _get_manager()
    if manager:
        checks["docker"] = "connected" if manager.docker else "unavailable"
        checks["containers"] = {
            name: state.status.value
            for name, state in manager.states.items()
        }
    return {"status": "healthy", "checks": checks}


# ---------------------------------------------------------------------------
# Container Management Tools — declared in service/sse/container_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_container_tools(mcp_server)


# ---------------------------------------------------------------------------
# Messaging Tools (comms_*)
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def comms_register(
    agentId: str,
    role: str,
    name: str = "",
    cwd: str = "",
    model: str = "",
    instructions: str = "",
) -> str:
    """Register this MCP client as an agent for messaging and presence. SSE clients can coordinate work, but cannot host local runtime launches."""
    r = await _api("POST", "/agents", {
        "agentId": agentId, "role": role, "name": name or agentId,
        "cwd": cwd, "model": model, "instructions": instructions,
    })
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f'Registered "{r.get("agentId", agentId)}" (role: {role}).'


@mcp_server.tool()
async def comms_agents() -> str:
    """List all registered agents, their roles, and unread message counts."""
    r = await _api("GET", "/agents")
    entries = r.get("agents", {})
    if not entries:
        return "No agents registered."
    lines = []
    for aid, info in entries.items():
        status = f" [{info['status']}]" if info.get("status") else ""
        lines.append(
            f"- {aid} ({info['role']}){status} -- \"{info.get('name', aid)}\" "
            f"| unread: {info.get('unread', 0)} | last seen: {info.get('lastSeen', '?')}"
        )
    return "\n".join(lines)


@mcp_server.tool()
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
    return f"Sent ({r['messageId']}) to {', '.join(r['recipients'])}. Subject: {subject}"


@mcp_server.tool()
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


@mcp_server.tool()
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
        f"Subject: {run.get('subject', '')}",
        f"Requested: {run.get('requestedAt', '')}",
    ]
    if run.get("summary"):
        lines.extend(["", "Summary:", run["summary"]])
    if run.get("error"):
        lines.extend(["", "Error:", run["error"]])
    events = run.get("events", [])[-10:]
    if events:
        lines.append("")
        lines.append("Recent events:")
        lines.extend([f"- {event['createdAt']} [{event['type']}] {event.get('body', '')}" for event in events])
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


@mcp_server.tool()
async def comms_console_tail(agentId: str, lines: int = 40) -> str:
    """Read the last N lines of another agent's live console (read-only; managed agents)."""
    n = max(1, min(int(lines or 40), 200))
    r = await _api("GET", f"/agents/{agentId}/console", params={"lines": n})
    if not r.get("ok"):
        return r.get("detail") or r.get("message") or f"Could not read {agentId}'s console."
    if not r.get("live"):
        return r.get("message") or f"{agentId} has no live console."
    output = r.get("output") or "(empty)"
    return (
        f"Console of {agentId} (terminal {r.get('terminalId')}, status {r.get('status')}), "
        f"last {r.get('lines')} lines:\n{output}"
    )


@mcp_server.tool()
async def comms_console_input(agentId: str, text: str = "", enter: bool = True, from_agent: str = "") -> str:
    """Recovery-only console input for managed agents; audited.

    Read the console first with comms_console_tail and use this only for a proven
    interactive prompt or operator recovery. Do not inject normal work messages,
    reminders, or duplicate comms_send delivery through the console.
    """
    r = await _api("POST", f"/agents/{agentId}/console/input", {
        "text": text or "",
        "enter": bool(enter),
        "from": from_agent or "",
    })
    if not r.get("ok"):
        return r.get("detail") or r.get("message") or f"Could not send input to {agentId}."
    return f"Input sent to {agentId}'s console (terminal {r.get('terminalId')}, control {r.get('controlId')})."


@mcp_server.tool()
async def comms_run_interrupt(runId: str, from_agent: str = "") -> str:
    """Request interruption of an active dispatched run."""
    r = await _api("POST", f"/dispatch/runs/{runId}/control", {
        "from_agent": from_agent,
        "action": "interrupt",
    })
    if not r.get("ok"):
        return r.get("detail", "Interrupt request failed.")
    return f"Interrupt requested for {runId}. Control ID: {r['controlId']}"


# NOTE (2026-05-31): comms_run_steer was REMOVED here to match the canonical
# stdio bridge (mcp/stdio/server.js), which retired it — ordinary comms_send to
# the target steers automatically when the target is busy and steer-capable.
# This SSE transport is intentionally a REDUCED tool surface vs stdio (it omits
# lifecycle/contract/inbox-management tools like comms_spawn / comms_compact /
# comms_contracts / comms_agent_info / comms_remove_agent / comms_delete_session);
# use the stdio bridge for the full tool set.


# ---------------------------------------------------------------------------
# Inbox + Search — declared in service/sse/inbox_tools.py, registered here so they land on
# this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_inbox_tools(mcp_server)


# ---------------------------------------------------------------------------
# Channel Tools — declared in service/sse/channel_tools.py, registered here so they land on this
# server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_channel_tools(mcp_server)


# ---------------------------------------------------------------------------
# File Sharing Tools — declared in service/sse/shared_file_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_shared_file_tools(mcp_server)


# ---------------------------------------------------------------------------
# Management Tools — declared in service/sse/management_tools.py, registered here so they
# land on this server in the position they always occupied.
# ---------------------------------------------------------------------------

_register_management_tools(mcp_server)


def setup_mcp_server(app):
    """Mount the MCP server onto the FastAPI app."""
    _bind_container_app(app)
    cfg = get_config()

    # Get the SSE app from FastMCP
    sse_app = mcp_server.sse_app()

    # Mount under the configured prefix
    app.mount(cfg.mcp_path_prefix, sse_app)

    logger.info(
        f"MCP SSE server mounted at {cfg.mcp_path_prefix}/ "
        f"- Connect at {cfg.mcp_path_prefix}/sse"
    )
