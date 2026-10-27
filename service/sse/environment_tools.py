"""Hosts and spawning (port of `mcp/stdio/environment-tools.mjs`, service mode).

Whether a spawn can be CLAIMED on a host is the service's own verdict, `spawnClaim.state` on every row of
`/environments` (service/api_core/records.py), computed by the rule `/spawn` refuses on. The bridge kept a
local fallback for services too old to send it; here the service IS that service, so it is read, never
recomputed. Four answers, not a boolean: a stale bridge's age tells an operator what to restart, and an
absent stamp is unknown from a listing, so the spawn attempt is the authority.

A spawn is created by the CALLER, from the request's header.
"""

from __future__ import annotations

from datetime import datetime, timezone

from mcp.server.fastmcp import Context
from pydantic import Field

from service.api_core.runtime import _normalize_runtime
from service.env_status import bridge_stamp_time
from service.sse.api_client import call
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.send_tools import SAFE_NAME, Priority


def _stale_age(env: dict) -> str:
    stamp = str((env.get("metadata") or {}).get("bridgeLastSeen") or "").strip()
    try:
        seen = bridge_stamp_time(stamp)
    except ValueError:
        return ""
    seconds = max(0, round((datetime.now(timezone.utc) - seen).total_seconds()))
    if seconds < 120:
        return f"{seconds}s ago"
    return f"{round(seconds / 60)}m ago" if seconds < 7200 else f"{round(seconds / 3600)}h ago"


def _claimability(env: dict) -> dict:
    state = str((env.get("spawnClaim") or {}).get("state") or "").strip()
    if state == "fresh":
        return {"can": True, "unknown": False, "note": "can spawn"}
    if state == "stale":
        return {"can": False, "unknown": False, "note": f"CANNOT SPAWN: no bridge since {_stale_age(env)}"}
    if state == "invalid":
        return {"can": False, "unknown": False, "note": "CANNOT SPAWN: unreadable bridge timestamp"}
    return {"can": False, "unknown": True, "note": "spawn UNPROVEN: no bridge stamp on this row"}


def _summary(env: dict) -> str:
    runtimes = ", ".join(r.get("runtime") for r in env.get("runtimes") or [] if r.get("runtime")) or "no runtimes"
    roots = ", ".join(env.get("cwdRoots") or []) or "no roots"
    return (f"- {env.get('id')} [{_claimability(env)['note']}] {env.get('label') or ''}\n  advertised: {env.get('status') or 'unknown'}; "
            f"{env.get('os') or 'unknown'}/{env.get('kind') or 'unknown'}; runtimes: {runtimes}; roots: {roots}")


async def comms_envs():
    """List environments (aify-env hosts). Use before comms_spawn to pick host, runtime and workspace. The bracket says whether a spawn can be CLAIMED there; `advertised:` only says the host exists."""
    envs = (await call("GET", "/environments")).get("environments") or []
    if not envs:
        return ("No host is connected. aify-env hosts managed agents on each machine: ask the operator to check it with "
                "`aify-env doctor` and start it if none is serving that host.")
    return f"{len(envs)} environment(s):\n" + "\n".join(_summary(e) for e in envs)


async def comms_spawn(
    ctx: Context,
    agentId: str = Field(description="Stable agent ID to create"),
    role: str = Field(description="Agent role: manager, coder, reviewer, tester, researcher, architect, operator"),
    runtime: str = Field(description="Runtime: claude-code, codex or hermes (pi deprecated)"),
    environmentId: str | None = Field(default=None, description="From comms_envs. Omit to use the first that can claim a spawn for this runtime."),
    workspace: str | None = Field(default=None, description="Workspace path inside the selected environment's advertised roots"),
    name: str | None = Field(default=None, description="Friendly name"),
    model: str | None = Field(default=None, description="Model or profile"),
    instructions: str | None = Field(default=None, description="Standing instructions for the agent"),
    initialMessage: str | None = Field(default=None, description="First brief, delivered once it is up"),
    subject: str | None = Field(default=None, description="Initial task subject"),
    priority: Priority | None = Field(default=None, description="Priority for the initial task"),
    envVars: dict[str, str] | None = Field(default=None, description="Extra worker env vars (NAME: value); AIFY_* is reserved"),
):
    """Create a persistent managed agent on an aify-env environment. An existing available agent needs no spawn: a send cold-starts it."""
    if not SAFE_NAME.match(agentId):
        return tool_error(f'Invalid agent ID: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{agentId}"')
    wanted = _normalize_runtime(runtime)
    envs = (await call("GET", "/environments")).get("environments") or []

    def runs(env):
        return any(_normalize_runtime(r.get("runtime") or "") == wanted for r in env.get("runtimes") or [])

    def advertised(env):
        return str(env.get("status") or "").lower() == "online"

    if environmentId:
        env = next((e for e in envs if e.get("id") == environmentId), None)
    else:
        env = (next((e for e in envs if advertised(e) and runs(e) and _claimability(e)["can"]), None)
               or next((e for e in envs if advertised(e) and runs(e) and _claimability(e)["unknown"]), None))
    if not env:
        hint = ("Available environments:\n" + "\n".join(_summary(e) for e in envs)) if envs else "No environment bridges are connected."
        return tool_error(f'No matching environment found for runtime "{wanted}".\n{hint}')
    if not advertised(env):
        return tool_error(f'Environment "{env.get("id")}" is {env.get("status") or "unknown"}, not online. Start its bridge first.')
    claim = _claimability(env)
    if not claim["can"] and not claim["unknown"]:
        return tool_error(f'Environment "{env.get("id")}" {claim["note"]}. It is advertised as {env.get("status") or "unknown"}, which is aify-env '
                          "describing the host rather than offering to run anything -- a different fact, and the one that misled callers before "
                          "this check existed. Start a claimer on that host and retry.")
    if not runs(env):
        return tool_error(f'Environment "{env.get("id")}" does not advertise runtime "{wanted}".')
    chosen = workspace or (env.get("cwdRoots") or [""])[0] or ""
    body = {
        "createdBy": caller_id(ctx), "environmentId": env.get("id"), "agentId": agentId, "role": role, "runtime": wanted,
        "workspace": chosen, "model": model or "", "instructions": instructions or "", "initialMessage": initialMessage or "",
        "subject": subject or (f"Brief {agentId}" if initialMessage else ""), "priority": priority or "normal",
        "mode": "managed-warm", "resumePolicy": "native_first",
    }
    if name is not None:
        body["name"] = name
    if envVars:
        body["envVars"] = envVars
    req = (await call("POST", "/spawn-requests", body)).get("definitionRequest") or {}
    return (f'Queued: asked the host of {env.get("id")} to define "{agentId}" ({wanted}, {chosen or "default workspace"}); '
            f"it is spawned once that host has defined it. Definition request: {req.get('id') or 'unknown'} [{req.get('status') or 'pending'}]. "
            f"Not confirmation: comms_agent_info shows whether it started, and GET /api/v1/agent-definitions/{agentId}/requests "
            "what the host did with the request (a refusal says why, and its consequence names the spawn).")


TOOLS = (comms_envs, comms_spawn)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
