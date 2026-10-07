"""HTTP adapter for shadow ingest. Admission middleware is owned by main.py."""
from fastapi import HTTPException, Request, Response

from service.api_core.agent_state_shadow import AgentStateShadowStore, InvalidPublication
from service.api_core.routing import domain_router
from service import clock

router = domain_router()


@router.post('/agent-state')
async def publish_agent_state(request: Request):
    try:
        result = await AgentStateShadowStore().apply(await request.body(), applied_at=clock.now())
    except InvalidPublication as error:
        raise HTTPException(422, str(error)) from error
    if result.applied:
        return Response(status_code=204)
    return {'applied': False, 'reason': result.reason}
