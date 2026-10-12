"""A handoff that keeps its agent's id restarts that agent into a fresh conversation, carrying the brief (0.9.1).

`comms_compact` in handoff mode and the dashboard's Compact post a spawn request for the successor. When the
successor is the agent itself and its host defines it, that spawn was refused (0.9 D8): the host owns the id. It
is the host's fresh-context `restart` instead, and the handoff brief is the restarted worker's first message,
sent once it attaches (`lifecycle_launch.attach`). The definition supplies runtime, workspace and model.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from service.api_core.definition_start import StartRefused, start_binding
from service.api_core.lifecycle_delegation import delegate


def same_id_handoff(agent_id: str, metadata: dict | None) -> bool:
    """True for a handoff whose successor is the agent it compacts. The source is named by `comms_compact`
    (`compactedFromAgentId`) or by the dashboard (`continuedFromAgentId`); `sameAgentId` is the caller's claim
    and is not read."""
    meta = metadata or {}
    source = meta.get("compactedFromAgentId") or meta.get("continuedFromAgentId")
    return meta.get("compactMode") == "handoff" and source == agent_id


async def restart_with_brief(db, agent_id: str, *, runtime: str, brief: dict | None, actor: str, request) -> dict | None:
    """Queue the fresh-context restart of a defined `agent_id` carrying `brief`, or None when no host defines it.

    A handoff asking for another runtime than the definition's is refused rather than restarted as the old one:
    the runtime is changed in the definition, on its host."""
    try:
        binding = await start_binding(db, agent_id)
    except StartRefused as refused:
        raise HTTPException(409, str(refused))
    if binding is None:
        return None
    if runtime and runtime != binding.runtime:
        raise HTTPException(409, f'Agent "{agent_id}" is defined to run {binding.runtime} on {binding.machine_id}; '
                                 f'a handoff restarts it from that definition, so change the runtime there first')
    return await delegate(db, agent_id, "restart", actor, request, fresh_context=True,
                          brief=json.dumps(brief) if brief else "")
