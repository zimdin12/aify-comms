"""HTTP adapter for shadow ingest. Admission middleware is owned by main.py."""
from fastapi import HTTPException, Query, Request, Response

from service.api_core.agent_state_shadow import AgentStateShadowStore, InvalidPublication
from service.api_core.routing import domain_router
from service import clock
from service.api_core import partial_status_shadow as shadow
from service.api_core.shadow_windows import recent_windows

router = domain_router()


@router.get('/agent-state/shadow-report')
async def shadow_report():
    return shadow.recorder.report()


@router.get('/agent-state/shadow-windows')
async def shadow_windows(limit: int = Query(20, ge=1, le=200)):
    return {'windows': await recent_windows(limit)}


@router.post('/agent-state')
async def publish_agent_state(request: Request):
    try:
        result = await AgentStateShadowStore().apply(await request.body(), applied_at=clock.now())
    except InvalidPublication as error:
        raise HTTPException(422, str(error)) from error
    if result.applied:
        return Response(status_code=204)
    return {'applied': False, 'reason': result.reason}
