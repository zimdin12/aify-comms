"""HTTP adapter for the agent-state ingest and its publisher readout. Admission middleware is owned by main.py."""
from fastapi import HTTPException, Request, Response

from service.api_core.agent_state_shadow import AgentStateShadowStore, InvalidPublication
from service.api_core import operational_view, partial_status_shadow as shadow
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


@router.get('/agent-state/publishers')
async def agent_state_publishers():
    """Every aify-env publisher the mirror holds and its availability now: what the operational view builds each agent's
    word from. `readable` is false when the mirror itself was lost, and then no publisher can be listed."""
    binding = shadow.bind()
    if binding.loss:
        return {'readable': False, 'publishers': []}
    return {'readable': True, 'publishers': [
        dict(machineId=machine, instance=instance, availability=operational_view.availability_of(publisher, binding.at),
             agents=len(publisher.rows))
        for (machine, instance), publisher in sorted(binding.view.items())]}
