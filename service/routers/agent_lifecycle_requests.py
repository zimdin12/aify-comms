"""HTTP lifecycle queue. The service accepts intent; only the host executes."""
from fastapi import HTTPException, Request
from service.api_core import agent_lifecycle_requests as core

from service.api_core.operator_authz import require_lifecycle
from service.api_core.routing import domain_router
from service.clock import now
from service.db import get_db
from service.lifecycle_models import LifecycleSubmit, LifecycleClaim, LifecycleResult, LifecycleAttachment
from service.api_core import lifecycle_launch
from service.routers.definition_requests import _environment, _proven_host

router = domain_router()


@router.post('/agents/{agent_id}/lifecycle-requests')
async def submit_lifecycle(agent_id: str, submitted: LifecycleSubmit, request: Request):
    body = submitted.model_dump(exclude_unset=True)
    proof = require_lifecycle(body, request)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        queued = await core.admit_lifecycle(db, agent_id, body, proof, now())
        await db.commit()
        return {'ok': True, 'request': queued}
    finally:
        await db.close()


@router.get('/agents/{agent_id}/lifecycle-requests')
async def list_lifecycle(agent_id: str):
    db = await get_db()
    try:
        return {'ok': True, 'requests': await core.lifecycle_requests_for(db, agent_id)}
    finally:
        await db.close()


@router.get('/agent-lifecycle-requests/{request_id}')
async def read_lifecycle(request_id: str):
    db = await get_db()
    try:
        found = await core.lifecycle_request_by_id(db, request_id)
        if found is None:
            raise HTTPException(404, 'no lifecycle request')
        return {'ok': True, 'request': found}
    finally:
        await db.close()


@router.post('/environments/{environment_id:path}/lifecycle-requests/claim')
async def claim_lifecycle(environment_id: str, claimed_by: LifecycleClaim, request: Request):
    body = claimed_by.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        environment = await _environment(db, environment_id)
        await _proven_host(db, environment, body.get('machineId',''), request)
        found = await core.claim_lifecycle_requests(db, environment, body.get('bridgeId',''), body.get('machineId',''), now())
        await db.commit()
        return {'ok': True, 'requests': found}
    finally:
        await db.close()


async def _launch_operation(environment_id, request_id, submitted, request, operation, key):
    body = submitted.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        environment = await _environment(db, environment_id)
        await _proven_host(db, environment, body.get('machineId', ''), request)
        result = await operation(db, environment, request_id, body, now())
        await db.commit()
        return {'ok': True, key: result}
    except BaseException:
        await db.rollback()
        raise
    finally:
        await db.close()


@router.post('/environments/{environment_id:path}/lifecycle-requests/{request_id}/launch')
async def prepare_lifecycle_launch(environment_id: str, request_id: str, body: LifecycleClaim, request: Request):
    return await _launch_operation(environment_id, request_id, body, request, lifecycle_launch.prepare_launch, 'launch')


@router.post('/environments/{environment_id:path}/lifecycle-requests/{request_id}/attachment')
async def attach_lifecycle_terminal(environment_id: str, request_id: str, body: LifecycleAttachment, request: Request):
    return await _launch_operation(environment_id, request_id, body, request, lifecycle_launch.attach, 'attachment')


@router.post('/environments/{environment_id:path}/lifecycle-requests/{request_id}/result')
async def report_lifecycle(environment_id: str, request_id: str, result: LifecycleResult, request: Request):
    body = result.model_dump(exclude_unset=True)
    db = await get_db()
    try:
        await db.execute('BEGIN IMMEDIATE')
        environment = await _environment(db, environment_id)
        await _proven_host(db, environment, body.get('machineId',''), request)
        reported = await core.report_lifecycle_result(db, environment, request_id, body, now())
        await db.commit()
        if reported['action'] == 'delete' and reported['status'] == 'done':
            # Reported back so a host can see a removal the service declined, e.g. a redefined id.
            return {'ok': True, 'request': reported, 'removal': await core.finish_lifecycle_removal(db, reported)}
        return {'ok': True, 'request': reported}
    finally:
        await db.close()
