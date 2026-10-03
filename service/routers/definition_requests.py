"""HTTP for change requests on defined agents (P0 C4). Behaviour lives in api_core/definition_requests.py.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.definition_requests import (
    admit, claim, finish_removal, report, request_by_id, requests_for,
)
from service.api_core.host_proof import host_proof_refusal, presented_proof
from service.api_core.operator_authz import require_operator
from service.api_core.request_body import json_object_body
from service.api_core.routing import domain_router
from service.clock import now as _now
from service.db import get_db
from service.definition_models import DefinitionClaim, DefinitionResult

router = domain_router()


async def _environment(db, environment_id: str):
    row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
    return dict(row) if row else None


async def _proven_host(db, environment: dict | None, machine_id: str, request: Request) -> None:
    """Refuse (403) a claim or report not from the machine's own host (external review of 0.8.1, HIGH 2)."""
    refusal = await host_proof_refusal(db, [(environment or {}).get("machine_id", ""), machine_id],
                                       presented_proof(request), _now())
    if refusal:
        await db.rollback()
        raise HTTPException(403, refusal)


@router.post("/agent-definitions/{agent_id}/requests")
async def request_definition_change(agent_id: str, request: Request):
    """Operator: ask the owning host to change (`patch`) or remove (`{"remove": true}`) a definition."""
    body = await json_object_body(request)
    operator = require_operator(body, request, "change an agent definition")
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        queued = await admit(db, agent_id, body.get("patch"), operator, _now())
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "request": queued}


@router.get("/agent-definitions/{agent_id}/requests")
async def list_definition_changes(agent_id: str):
    db = await get_db()
    try:
        found = await requests_for(db, agent_id, _now())
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "requests": found}


@router.post("/environments/{environment_id:path}/definition-requests/claim")
async def claim_definition_changes(environment_id: str, claimed_by: DefinitionClaim, request: Request):
    """A host's claim of the requests its current store should apply, fenced as a push is."""
    body = claimed_by.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        environment = await _environment(db, environment_id)
        await _proven_host(db, environment, body.get("machineId", ""), request)
        claimed = await claim(db, environment, body.get("bridgeId", ""),
                              body.get("machineId", ""), _now())
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "requests": claimed}


@router.post("/environments/{environment_id:path}/definition-requests/{request_id}/result")
async def report_definition_change(environment_id: str, request_id: str, result: DefinitionResult, request: Request):
    """A host's report of what it did with a request: `done` (with the lifetime and revision it left)
    or `refused`. A removal's `done` removes the agent here only behind C4's fence."""
    body = result.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        environment = await _environment(db, environment_id)
        await _proven_host(db, environment, body.get("machineId", ""), request)
        reported = await report(db, environment, request_id, body, _now())
        await db.commit()
        # OWED UNTIL SETTLED: a removal whose `done` was recorded and whose service consequence did not
        # finish (a crash, a lost connection) is finished by whichever report finds it still pending,
        # and one already settled is never run again.
        if reported["consequence"] == "pending":
            await finish_removal(db, reported)
            reported = await request_by_id(db, request_id)
    finally:
        await db.close()
    return {"ok": True, "request": reported}
