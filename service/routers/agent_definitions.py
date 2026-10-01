"""HTTP for aify-env's agent definitions (P0 C3). Behaviour lives in api_core/definition_push.py.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.definition_push import apply_definition_push
from service.api_core.operator_authz import authorize_operator, operator_key_from
from service.api_core.request_body import json_object_body
from service.api_core.routing import domain_router
from service.clock import now as _now
from service.db import get_db

router = domain_router()


@router.put("/environments/{environment_id:path}/agent-definitions")
async def push_agent_definitions(environment_id: str, request: Request):
    """A host's complete snapshot of its agent definitions: applied, a replay, or refused by name."""
    body = await json_object_body(request)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        try:
            result = await apply_definition_push(db, dict(env_row) if env_row else None, body, _now())
        except HTTPException:
            # A refused retired store's count is written before the refusal: kept, so the doctor and
            # the dashboard can say two stores claim the machine.
            await db.commit()
            raise
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, **result}


def _require_operator(body: dict, request: Request, action: str) -> None:
    actor = str(body.get("requestedBy") or "").strip()
    if not authorize_operator(actor, request, operator_key_from(request), action=action):
        raise HTTPException(403, f"only the operator may {action}")


@router.post("/environments/{environment_id:path}/definition-store/reset")
async def reset_definition_store(environment_id: str, request: Request):
    """Operator: let this machine return to a store it retired (two definition directories used in turn)."""
    body = await json_object_body(request, lenient=True)
    _require_operator(body, request, "reset a machine's retired definition stores")
    db = await get_db()
    try:
        env_row = await (await db.execute("SELECT machine_id FROM environments WHERE id = ?", (environment_id,))).fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{environment_id}" not found')
        cursor = await db.execute("DELETE FROM definition_stores_retired WHERE machine_id = ?", (env_row["machine_id"],))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "machineId": env_row["machine_id"], "cleared": cursor.rowcount}


@router.post("/agent-definitions/{agent_id}/release")
async def release_agent_definition(agent_id: str, request: Request):
    """Operator: release an id whose owner is gone, so another machine may define it (C3, C6)."""
    body = await json_object_body(request, lenient=True)
    _require_operator(body, request, "release an agent definition")
    db = await get_db()
    try:
        row = await (await db.execute("SELECT machine_id FROM agent_definitions WHERE agent_id = ?", (agent_id,))).fetchone()
        if not row:
            raise HTTPException(404, f'"{agent_id}" has no definition to release')
        await db.execute("DELETE FROM agent_definitions WHERE agent_id = ?", (agent_id,))
        await db.execute("UPDATE agents SET definition_state = 'withdrawn' WHERE id = ?", (agent_id,))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "agentId": agent_id, "releasedFrom": row["machine_id"]}
