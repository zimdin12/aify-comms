"""Three small PATCHes on an agent row: status, description, favourite.

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

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import AgentDescribeRequest, AgentFavoriteUpdate, AgentStatusUpdate

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
        cursor = await db.execute(
            "UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?",
            (status, note, _now(), agent_id)
        )
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
