"""Small PATCHes on an agent row: status, description, favourite, herdr space, effort.

Extracted from `service/routers/agents/identity.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local, nothing borrowed from `agents/shared.py`.

THEY ARE TOGETHER BECAUSE THEY ARE THE SAME OPERATION with a different column, and apart from
`register_agent` because that one is 209 lines of deciding what an agent IS. These three assume the
agent already exists and change one thing about it.

`description` IS THE EXCEPTION TO RE-REGISTRATION, which is the reason it has an endpoint at all.
Re-registering an agent is a full state refresh — everything else is wiped and rebuilt — so the one
field an operator wants to keep across a re-register has to be settable on its own. See DECISIONS.md.

Bodies and route decorators are byte-identical to what stood in `identity.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from service.api_core.agent_sessions import _mark_agent_present
from service.api_core.definition_guard import DEFINED_SQL
from service.api_core.serialization import _json_loads_or
from service.api_core.definition_requests import queued_for_its_host
from service.api_core.model_effort import with_effort
from service.api_core.operator_authz import recorded_operator_actor
from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import (
    AgentDescribeRequest, AgentEffortUpdate, AgentFavoriteUpdate, AgentHerdrSpaceUpdate, AgentStatusUpdate,
)

router = domain_router()



@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    db = await get_db()
    try:
        note = getattr(req, 'note', None) or ''
        # FOLDED, like every sibling identity field on this row. `agents.status` is compared against
        # lowercase literals in 25 places -- 14 of them fold case first and 11 do not -- so a stored
        # `"Stopped"` is the operator's manual stop to half the service and an unrecognised value to
        # the other half. `_MANUAL_STATUSES` is the one the operator would notice: it is the status
        # derivation is forbidden to argue with, and it matches on `"stopped"` exactly.
        status = str(req.status or "").strip().lower()
        status_val = f"{status}: {note}" if note else status
        now = _now()
        cursor = await db.execute(
            "UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?",
            (status, note, now, agent_id)
        )
        # comms_status: the agent reporting on itself, so it is presence. Its siblings below are not.
        await _mark_agent_present(db, agent_id, now)
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        ws = await _get_ws(request)
        if ws:
            # Keep req.status authoritative (operator-set), enrich with the note
            # so dashboards can render it on the agent's row without a refetch.
            await ws.broadcast("agent_status", {"agentId": agent_id, "status": status, "statusNote": note})
        return {"ok": True, "agentId": agent_id, "status": status_val, "statusRaw": status, "statusNote": note}
    finally:
        await db.close()



@router.patch("/agents/{agent_id}/description")
async def update_agent_description(agent_id: str, req: AgentDescribeRequest, request: Request):
    """Update an agent's team-facing description without re-registering."""
    validate_name(agent_id, "agent ID")
    description = str(req.description or "")
    if len(description) > 2000:
        raise HTTPException(400, "description must be 2000 chars or fewer")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await db.execute(
            "UPDATE agents SET description = ?, last_seen = ? WHERE id = ?",
            (description, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_description_updated", {"agentId": agent_id, "description": description})
        return {"ok": True, "agentId": agent_id, "description": description}
    finally:
        await db.close()



@router.patch("/agents/{agent_id}/favorite")
async def update_agent_favorite(agent_id: str, req: AgentFavoriteUpdate, request: Request):
    """Dashboard favorites — pin/unpin an agent in the chat list.

    Operator-set per-deployment flag (not synced across remote
    dashboards). Dashboard renders favorited agents at the top of the
    list and shows a visual marker. Pure metadata — no behavior change.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        flag = 1 if bool(req.favorited) else 0
        await db.execute(
            "UPDATE agents SET favorited = ?, last_seen = ? WHERE id = ?",
            (flag, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_favorite_updated", {"agentId": agent_id, "favorited": bool(flag)})
        return {"ok": True, "agentId": agent_id, "favorited": bool(flag)}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/herdr-space")
async def update_agent_herdr_space(agent_id: str, req: AgentHerdrSpaceUpdate, request: Request):
    """Whether this managed agent is started with a herdr space of its own. Applies from its next start.

    One value per agent, stored here because the host tier, aify-comms' dashboard and aify-dashboard
    all already read this service's agents; the launch route hands it to the host that starts the
    worker. `last_seen` is not stamped: an operator's setting is not the agent being here.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        show = bool(req.show)
        # A DEFINED AGENT'S HERDR SPACE IS ITS DEFINITION'S (P0 C5): the edit becomes a request for its
        # host. The guard is the UPDATE's own WHERE, so no push can land between a check and the write:
        # the row exists (above), so an UPDATE that changed nothing found it defined.
        cursor = await db.execute(f"UPDATE agents SET herdr_space = ? WHERE id = ? AND NOT {DEFINED_SQL}",
                                  (1 if show else 0, agent_id))
        if not cursor.rowcount:
            await db.rollback()
            actor = recorded_operator_actor(None, request, action="changing a defined agent as the operator")
            return await queued_for_its_host(db, agent_id, {"herdrSpace": show}, actor, _now())
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_herdr_space_updated", {"agentId": agent_id, "herdrSpace": show})
        return {"ok": True, "agentId": agent_id, "herdrSpace": show}
    finally:
        await db.close()


async def _respecify_effort(db, agent_id: str, effort: str) -> None:
    """Every spawn spec of this agent carries `effort`, so its next start does.

    A restart starts from the agent's stored spec, and a start going running copies that spec's
    `runtimeConfig` over the agent's (running_spawn.py). Changing only the agent's record was undone by
    the very start it was for. The apply-defaults route writes both for the same reason (settings.py).
    """
    specs = await (await db.execute("SELECT id, metadata FROM spawn_specs WHERE agent_id = ?", (agent_id,))).fetchall()
    for spec in specs:
        metadata = _json_loads_or(spec["metadata"], {})
        metadata = metadata if isinstance(metadata, dict) else {}
        metadata = {**metadata, "runtimeConfig": with_effort(metadata.get("runtimeConfig"), effort)}
        await db.execute("UPDATE spawn_specs SET metadata = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(metadata), _now(), spec["id"]))


@router.patch("/agents/{agent_id}/effort")
async def update_agent_effort(agent_id: str, req: AgentEffortUpdate, request: Request):
    """The reasoning effort this agent starts with from its NEXT start (P0 C12); "" is the runtime's own.

    Whoever owns what the agent's start reads is who changes it. A DEFINED agent's effort is its
    definition's, so the change is a request its host applies (C5). An undefined MANAGED agent's is its
    record's and its spawn specs', which its managed starts read. An undefined RESIDENT's launcher reads
    neither, so the change is refused with the way to define it rather than accepted and ignored.
    """
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        # THE WRITE LOCK BEFORE THE READ, as `usage-source` takes it: this rewrites the whole runtime
        # config, so a sibling edit committed between this read and the write was lost behind two 200s.
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT id, session_mode, runtime_config FROM agents WHERE id = ?",
                                      (agent_id,))).fetchone()
        if not row:
            await db.rollback()
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        # The guard is the UPDATE's own WHERE, as for the herdr space: a push cannot land between a check
        # and the write. Resident rows are left out of it and refused below.
        cursor = await db.execute(
            f"UPDATE agents SET runtime_config = ? WHERE id = ? AND NOT {DEFINED_SQL} AND session_mode = 'managed'",
            (json.dumps(with_effort(_json_loads_or(row["runtime_config"], {}), req.effort)), agent_id))
        if not cursor.rowcount:
            await db.rollback()
            defined = await (await db.execute(f"SELECT 1 FROM agents WHERE id = ? AND {DEFINED_SQL}", (agent_id,))).fetchone()
            if not defined:
                raise HTTPException(409, f'"{agent_id}" is a resident agent that no host defines: its launcher reads '
                                         f"only a definition. Define it (aify-env agents import), then change its effort.")
            actor = recorded_operator_actor(None, request, action="changing a defined agent as the operator")
            queued = await queued_for_its_host(db, agent_id, {"effort": req.effort}, actor, _now())
            return {**queued, "appliesAt": "next start"}
        await _respecify_effort(db, agent_id, req.effort)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_effort_updated", {"agentId": agent_id, "effort": req.effort})
        return {"ok": True, "agentId": agent_id, "effort": req.effort, "appliesAt": "next start"}
    finally:
        await db.close()
