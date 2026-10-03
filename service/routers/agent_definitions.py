"""HTTP for aify-env's agent definitions (P0 C3). Behaviour lives in api_core/definition_push.py.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.definition_push import apply_definition_push
from service.api_core.host_proof import judge_host_proof, presented_proof
from service.api_core.serialization import _normalize_machine_id
from service.api_core.operator_authz import require_operator
from service.api_core.request_body import json_object_body
from service.api_core.routing import domain_router
from service.clock import now as _now
from service.db import get_db
from service.definition_models import DefinitionPush

router = domain_router()


@router.put("/environments/{environment_id:path}/agent-definitions")
async def push_agent_definitions(environment_id: str, push: DefinitionPush, request: Request):
    """A host's complete snapshot of its agent definitions: applied, a replay, or refused by name."""
    body = push.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        # ONLY THE MACHINE'S OWN HOST (external review of 0.8.1, HIGH 2): the bridge id below is no secret.
        proof = await judge_host_proof(db, [env_row["machine_id"] if env_row else "", body.get("machineId")],
                                       presented_proof(request))
        if proof.refusal:
            await db.rollback()
            raise HTTPException(403, f"definition push refused: {proof.refusal}")
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


@router.post("/environments/{environment_id:path}/definition-store/reset")
async def reset_definition_store(environment_id: str, request: Request):
    """Operator: let this machine return to a store it retired (two definition directories used in turn)."""
    body = await json_object_body(request, lenient=True)
    require_operator(body, request, "reset a machine's retired definition stores")
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


@router.post("/host-proofs/{machine_id}/reset")
async def reset_host_proof(machine_id: str, request: Request):
    """Operator: forget the host proof a machine presented, so its next aify-env records a new one (HIGH 2)."""
    body = await json_object_body(request, lenient=True)
    require_operator(body, request, "reset a machine's host proof")
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM host_proofs WHERE machine_id = ?", (_normalize_machine_id(machine_id),))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "machineId": machine_id, "cleared": cursor.rowcount}


@router.post("/agent-definitions/{agent_id}/release")
async def release_agent_definition(agent_id: str, request: Request):
    """Operator: release an id from the machine named in `machineId`, whose owner is gone, so another
    machine may define it (C3, C6). Only that machine's definition is released.

    THE OWNER IS READ INSIDE THE WRITE TRANSACTION. Read outside it, a push could move the id to
    another machine between the read and the delete, and the release deleted the new owner's
    definition while reporting the old one (review of P3a, R1).
    """
    body = await json_object_body(request, lenient=True)
    require_operator(body, request, "release an agent definition")
    expected = str(body.get("machineId") or "").strip()
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute("SELECT machine_id FROM agent_definitions WHERE agent_id = ?", (agent_id,))).fetchone()
        if not row:
            await db.rollback()
            raise HTTPException(404, f'"{agent_id}" has no definition to release')
        if not expected:
            await db.rollback()
            raise HTTPException(422, "machineId: name the machine whose definition is being released")
        if row["machine_id"] != expected:
            await db.rollback()
            raise HTTPException(409, f'"{agent_id}" is defined on {row["machine_id"]}, not {expected}; nothing released')
        await db.execute("DELETE FROM agent_definitions WHERE agent_id = ?", (agent_id,))
        await db.execute("UPDATE agents SET definition_state = 'withdrawn' WHERE id = ?", (agent_id,))
        await db.commit()
    finally:
        await db.close()
    # A RELEASE DOES NOT ADVANCE ANY HOST'S REVISION (P0 C3): a machine whose push was refused this id
    # takes it with its next fresh revision; replaying the revision it already sent does not.
    return {"ok": True, "agentId": agent_id, "releasedFrom": expected}
