"""`comms_compact`: continue a degraded managed agent from a summary (port of `mcp/stdio/compact-tool.mjs`).

Two modes. `handoff` (default) spawns a fresh session told its old session id and to read its recent
messages; `native` types the runtime's own compact command into its live console. Either loses what the
agent knew and never wrote down, so the description says so. The requester is the CALLER, from the
request's header.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import quote, urlencode

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.runtime import _normalize_runtime
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.send_tools import SAFE_NAME, Priority

SESSION_RANK = {"running": 100, "starting": 90, "recovering": 85, "restarting": 80, "cli-takeover": 60,
                "stopped": 40, "lost": 25, "failed": 20, "ended": 5}


def _millis(stamp) -> float:
    try:
        return datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return 0


def pick_compact_session(sessions: list) -> dict | None:
    """The session to compact from: the liveliest status, then the most recently seen."""
    ranked = sorted(sessions, key=lambda s: (-SESSION_RANK.get(str(s.get("status") or "").lower(), 0),
                                             -_millis(s.get("lastSeen") or s.get("startedAt"))))
    return ranked[0] if ranked else None


def _bad(name: str, label: str):
    if SAFE_NAME.match(name or ""):
        return None
    return tool_error(f'Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{name}"')


async def _native(me: str, target: str, handoff: dict):
    misplaced = [name for name, value in handoff.items() if value is not None]
    if misplaced:
        verb = "ies" if len(misplaced) == 1 else "y"
        return tool_error(f'{", ".join(misplaced)} appl{verb} to mode "handoff" only; native compaction types the runtime\'s own command and takes none of them.')
    r = await call("POST", f"/agents/{quote(target, safe='')}/compact/native", {"from": me})
    if not r.get("ok"):
        return tool_error(r.get("message") or f'Native compaction of "{target}" was refused.')
    return (f"QUEUED {r.get('command')} to {target}'s console (terminal {r.get('terminalId')}, control {r.get('controlId')}). "
            "Not confirmation: the bytes reach the PTY, and whether it compacted shows on its console (comms_console_tail). It keeps its session.")


async def _handoff(me: str, target: str, h: dict):
    info = ((await call("GET", "/agents")).get("agents") or {}).get(target) or {}
    successor = h["newAgentId"] or target
    if refusal := _bad(successor, "handoff agent ID"):
        return refusal
    sessions = (await call("GET", f"/sessions?{urlencode({'agentId': target, 'limit': '100'})}")).get("sessions") or []
    source = pick_compact_session(sessions)
    if not source:
        return tool_error(f'No managed session record found for "{target}". Compact needs a dashboard-managed backing session. '
                          "Use comms_spawn first or adopt the identity into an environment from the dashboard.")
    query = {"sessionId": source.get("id") or ""}
    if h["recentMessages"] is not None:
        query["recentMessages"] = str(h["recentMessages"])
    brief = await call("GET", f"/agents/{quote(target, safe='')}/compact/handoff-brief?{urlencode(query)}")
    packet = f"{brief.get('text')}\n\nInstructions:\n{h['instructions']}" if h["instructions"] else brief.get("text")
    r = await call("POST", "/spawn-requests", {
        "createdBy": me, "environmentId": h["environmentId"] or source.get("environmentId"), "agentId": successor,
        "role": h["role"] or info.get("role") or "coder", "name": successor,
        "runtime": _normalize_runtime(h["runtime"] or source.get("runtime") or info.get("runtime") or "generic"),
        "workspace": h["workspace"] or source.get("workspace") or info.get("cwd") or "",
        "initialMessage": packet, "subject": f"Handoff compact from {target}", "priority": h["priority"] or "normal",
        "mode": "managed-warm", "resumePolicy": "fresh_context",
        "metadata": {"compactMode": "handoff", "compactedFromAgentId": target, "compactedFromSessionId": source.get("id") or "",
                     "compactedBy": me, "recentMessagesToRead": brief.get("recentMessages"), "sameAgentId": successor == target},
    })
    req = r.get("spawnRequest") or r.get("definitionRequest") or r.get("lifecycleRequest") or {}
    who = f'same agent ID "{successor}"' if successor == target else f'successor "{successor}"'
    queued_as = "Its host restarts it into a fresh conversation. Restart request" if r.get("lifecycleRequest") else "Spawn request"
    return (f'Queued handoff compaction for {who} from "{target}". {queued_as}: {req.get("id") or "unknown"} [{req.get("status") or "queued"}]. '
            f"The fresh session is told its previous session and to read its last {brief.get('recentMessages')} message(s); the old native session is not reused.")


async def comms_compact(
    ctx: Context,
    targetAgentId: str = Field(description="Existing managed agent to compact"),
    mode: Literal["handoff", "native"] | None = Field(default=None, description="handoff (default): fresh session, told its old session id and to read its recent messages. native: types its runtime's /compact (hermes /compress) into its live console; managed, idle agents only."),
    newAgentId: str | None = Field(default=None, description="handoff: a separate continuation identity. Default: the target's own."),
    role: str | None = Field(default=None, description="handoff: default the target's role, else coder."),
    environmentId: str | None = Field(default=None, description="handoff: default the source session's."),
    runtime: str | None = Field(default=None, description="handoff: default the source session's."),
    workspace: str | None = Field(default=None, description="handoff: default the source session's."),
    instructions: str | None = Field(default=None, description="handoff: a phase brief for the fresh session."),
    recentMessages: int | None = Field(default=None, ge=0, description="handoff: messages it is told to read. Default 10."),
    priority: Priority | None = Field(default=None, description="handoff: priority of its brief."),
):
    """DESTRUCTIVE TO CONTEXT — the target continues from a summary: whatever it knew but never wrote down is gone. Use when a managed agent is degraded by a long noisy session, not as routine hygiene. Have it record open decisions somewhere durable FIRST."""
    me = caller_id(ctx)
    if refusal := _bad(me, "from agent ID") or _bad(targetAgentId, "target agent ID"):
        return refusal
    handoff = {"newAgentId": newAgentId, "role": role, "environmentId": environmentId, "runtime": runtime, "workspace": workspace,
               "instructions": instructions, "recentMessages": recentMessages, "priority": priority}
    try:
        return await (_native(me, targetAgentId, handoff) if mode == "native" else _handoff(me, targetAgentId, handoff))
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(str(error))


TOOLS = (comms_compact,)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
