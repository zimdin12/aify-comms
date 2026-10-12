"""HTTP for on-demand model reads (0.9.1 3B): a looker asks what an agent's session runs, and its host answers."""
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel

from service import longpoll
from service.api_core import model_reads
from service.api_core.routing import domain_router
from service.clock import now
from service.db import get_db
from service.routers.definition_requests import _environment, _proven_host

router = domain_router()


class ModelReadClaim(BaseModel):
    bridgeId: Any = None
    machineId: Any = None
    waitMs: int = 0


class ModelReadAnswer(BaseModel):
    bridgeId: Any = None
    machineId: Any = None
    results: Any = None


@router.get('/agents/{agent_id}/model')
async def agent_model(agent_id: str):
    """`actual`: `{model, at, readAt}` from the agent's host, `stale` added when that host was silent, or `{unknown}`.
    The configured model is the agent record's `runsWith`."""
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT id, runtime, cwd, session_handle, machine_id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if row is None:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        handle = str(row["session_handle"] or "")
        sharing = [r["id"] for r in await (await db.execute(
            "SELECT id FROM agents WHERE session_handle = ? AND id != ? ORDER BY id", (handle, agent_id))).fetchall()] if handle else []
    finally:
        await db.close()
    if not handle:
        actual = {"unknown": "no session handle"}
    elif sharing:
        actual = {"unknown": f"its session handle is also claimed by {', '.join(sharing)}"}
    elif not row["machine_id"]:
        actual = {"unknown": "no host"}
    else:
        actual = await model_reads.MODEL_READS.read(row["machine_id"], model_reads.read_input(row))
    return {"ok": True, "agentId": agent_id, "actual": actual}


async def _host_machine(environment_id: str, machine_id: Any, request: Request, then=None):
    """The proved host's machine id, or `then(machine)` run straight after the proof. Nothing awaits between the two,
    so no enrollment or new work can land in between (review of 0.9.1, F3: the dequeue once followed `db.close()`)."""
    db = await get_db()
    try:
        environment = await _environment(db, environment_id)
        if environment is None:
            raise HTTPException(404, f"Environment '{environment_id}' not found")
        await _proven_host(db, environment, str(machine_id or ''), request)
        machine = str(environment.get("machine_id") or "")
        return then(machine) if then else machine
    finally:
        await db.close()


@router.post('/environments/{environment_id:path}/model-reads/claim')
async def claim_model_reads(environment_id: str, body: ModelReadClaim, request: Request):
    machine = await _host_machine(environment_id, body.machineId, request)

    async def claimed() -> dict:
        # PROVED AGAIN IN THE SAME STEP AS THE DEQUEUE: a claim held while its host enrolled would otherwise take work on
        # the proof it had when it started, which an enrolled host no longer accepts.
        return {"ok": True, "reads": await _host_machine(environment_id, body.machineId, request,
                                                         then=model_reads.MODEL_READS.claim)}

    # NO FALLBACK RE-CHECK: a read only ever arrives through `ask`, which notifies this scope, so the held claim wakes
    # for it at once and otherwise sleeps out its wait.
    return await longpoll.longpoll(body.waitMs, claimed, lambda result: not result["reads"],
                                   scope=model_reads.scope(machine), fallback_s=longpoll.MAX_WAIT_S, request=request)


@router.post('/environments/{environment_id:path}/model-reads/{read_id}/answer')
async def answer_model_read(environment_id: str, read_id: str, body: ModelReadAnswer, request: Request):
    machine = await _host_machine(environment_id, body.machineId, request)
    # A read nobody waits for any more is not an error to the host: it answered, and the asker moved on.
    return {"ok": True, "accepted": model_reads.MODEL_READS.answer(machine, read_id, body.results, now())}
