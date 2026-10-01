"""HTTP for change requests on defined agents (P0 C4). Behaviour lives in api_core/definition_requests.py.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.definition_requests import (
    admit, claim, finish_removal, report, request_by_id, requests_for,
)
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


@router.post("/agent-definitions/{agent_id}/requests")
async def request_definition_change(agent_id: str, request: Request):
    """Operator: ask the owning host to change (`patch`) or remove (`{"remove": true}`) a definition."""
    body = await json_object_body(request)
    require_operator(body, request, "change an agent definition")
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        queued = await admit(db, agent_id, body.get("patch"), str(body.get("requestedBy") or "").strip(), _now())
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
async def claim_definition_changes(environment_id: str, claimed_by: DefinitionClaim):
    """A host's claim of the requests its current store should apply, fenced as a push is."""
    body = claimed_by.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        claimed = await claim(db, await _environment(db, environment_id), body.get("bridgeId", ""),
                              body.get("machineId", ""), _now())
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "requests": claimed}


@router.post("/environments/{environment_id:path}/definition-requests/{request_id}/result")
async def report_definition_change(environment_id: str, request_id: str, result: DefinitionResult):
    """A host's report of what it did with a request: `done` (with the lifetime and revision it left)
    or `refused`. A removal's `done` removes the agent here only behind C4's fence."""
    body = result.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        reported = await report(db, await _environment(db, environment_id), request_id, body, _now())
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
