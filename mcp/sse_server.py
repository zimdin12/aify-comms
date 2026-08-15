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

import httpx  # still used directly by comms_share, which posts form-encoded and cannot go through _api
from mcp.server.fastmcp import FastMCP

from service.config import get_config
# Layer-0 leaves that used to sit in this file. Imported under their ORIGINAL private names so every
# call site below is unchanged by the move — and so `test_sse_renderers.py`, which swaps `_api` for a
# canned payload, still patches the name the tool bodies resolve.
#
# They live under `service/` rather than beside this file because `mcp/` is NOT this repo's package:
# `import mcp` resolves to the PyPI distribution the line above imports FastMCP from, which is why
# `service/main.py` loads THIS file by path instead of importing it. See `service/sse/__init__.py`.
from service.sse.api_client import api as _api, api_url as _api_url  # noqa: F401
from service.sse.container_tools import (
    bind_app as _bind_container_app,
    get_manager as _get_manager,
    register as _register_container_tools,
)
from service.sse.rendering import SAFETY_HEADER, fence as _fence

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


@mcp_server.tool()
async def comms_inbox(
    agentId: str,
    filter: str = "unread",
    fromAgent: str = "",
    fromRole: str = "",
    type: str = "",
    mode: str = "full",
    messageId: str = "",
    limit: int = 20,
) -> str:
    """Check your inbox. Returns only UNREAD messages by default. Use mode='headers' for preview-only triage or messageId to fetch one message by ID. Messages are marked as read after viewing."""
    params = {"filter": filter, "limit": str(limit), "mode": mode}
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
                f"From: {m['from']} | Type: {m['type']} | Subject: {m.get('subject', '')}",
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
                f"From: {m['from']} | Type: {m['type']} | Subject: {m.get('subject', '')}\n"
                f"{safe_body}"
            )
    trunc = f"\n\n(Showing {r['showing']} of {r['total']})" if r.get("total", 0) > r.get("showing", 0) else ""
    return f"{SAFETY_HEADER}\n\n{r['total']} message(s):\n\n" + "\n\n".join(lines) + trunc


@mcp_server.tool()
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
            lines.append(
                f"[MSG] {x['id']} | from: {x['from']}{to} | {x.get('subject', '')}\n  {x.get('preview', '')}"
            )
        else:
            lines.append(f"[FILE] {x['name']} | from: {x.get('from', '?')} | {x.get('description', '')}")
    return "\n\n".join(lines) + f"\n\n({scope_note}){warn}"


# ---------------------------------------------------------------------------
# Channel Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def comms_channel_create(name: str, from_agent: str, description: str = "") -> str:
    """Create a new channel (group chat) for multiple agents to communicate."""
    r = await _api("POST", "/channels", {"name": name, "createdBy": from_agent, "description": description})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f"Channel #{name} created. You're a member."


@mcp_server.tool()
async def comms_channel_join(channel: str, from_agent: str) -> str:
    """Join an existing channel."""
    r = await _api("POST", f"/channels/{channel}/join", {"agentId": from_agent})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f"Joined #{channel}. Members: {', '.join(r.get('members', []))}"


@mcp_server.tool()
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


@mcp_server.tool()
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


@mcp_server.tool()
async def comms_channel_list() -> str:
    """List all channels."""
    r = await _api("GET", "/channels")
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


# ---------------------------------------------------------------------------
# File Sharing Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def comms_share(from_agent: str, name: str, content: str, description: str = "") -> str:
    """Share an artifact (code, results, text) with other agents."""
    # Use form-encoded data to match the API
    url = f"{_api_url()}/shared"
    headers = {}
    cfg = get_config()
    if cfg.api_key:
        headers["X-API-Key"] = cfg.api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, data={
            "from_agent": from_agent, "name": name, "content": content, "description": description,
        })
        r = resp.json()
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f'Shared "{r.get("name", name)}" ({r.get("size", 0)} bytes).'


@mcp_server.tool()
async def comms_read(name: str) -> str:
    """Read a shared artifact by name."""
    r = await _api("GET", f"/shared/{name}")
    if "detail" in r:
        return f"Error: {r['detail']}"
    if r.get("content"):
        meta = r.get("meta", {})
        header = f"From: {meta.get('from', '?')} | {meta.get('sharedAt', '')}" if meta.get("from") else ""
        if meta.get("description"):
            header += f" | {meta['description']}"
        return (header + "\n\n" + r["content"]) if header else r["content"]
    return f'"{name}" -- binary file on server.'


@mcp_server.tool()
async def comms_files() -> str:
    """List all shared artifacts."""
    r = await _api("GET", "/shared")
    files = r.get("files", [])
    if not files:
        return "No shared artifacts."
    lines = [
        f"- {f['name']} ({f.get('size', 0)}B, from: {f.get('from', '?')}, {f.get('sharedAt', '')})"
        + (f" -- {f['description']}" if f.get("description") else "")
        for f in files
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Management Tools
# ---------------------------------------------------------------------------

@mcp_server.tool()
async def comms_clear(target: str, agentId: str = "", olderThanHours: float = 0) -> str:
    """Clear messages, shared files, agents, or everything. Optional age filter."""
    data = {"target": target}
    if agentId:
        data["agentId"] = agentId
    if olderThanHours > 0:
        data["olderThanHours"] = olderThanHours
    r = await _api("POST", "/clear", data)
    if not r.get("ok"):
        return f"Error: {r.get('detail', 'unknown error')}"
    c = r.get("cleared", {})
    parts = [f"{k}: {v}" for k, v in c.items() if v]
    return f"Cleared: {', '.join(parts)}" if parts else "Nothing to clear."


@mcp_server.tool()
async def comms_dashboard() -> str:
    """Get the dashboard URL."""
    cfg = get_config()
    return f"Dashboard: http://localhost:{cfg.port}/api/v1/dashboard"


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
