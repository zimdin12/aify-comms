"""HTTP adapter for shadow ingest. Admission middleware is owned by main.py."""
from fastapi import HTTPException, Query, Request, Response

from service.api_core.agent_state_shadow import AgentStateShadowStore, InvalidPublication
from service.api_core.routing import domain_router
from service import clock
from service.api_core import partial_status_shadow as shadow
from service.api_core.shadow_windows import recent_windows
from service.api_core.shadow_evidence_store import PAGE_LIMIT, read_page
from service.db import get_db

router = domain_router()


@router.get('/agent-state/shadow-report')
async def shadow_report():
    return shadow.recorder.report()


@router.get('/agent-state/shadow-windows')
async def shadow_windows(limit: int = Query(20, ge=1, le=200)):
    return {'windows': await recent_windows(limit)}


@router.get('/agent-state/shadow-evidence')
async def shadow_evidence(window: int, after: int = Query(0, ge=0), limit: int = Query(PAGE_LIMIT, ge=1, le=PAGE_LIMIT),
                          frontier: int | None = Query(None, ge=0), owner: str | None = None,
                          variant: str | None = None, site: str | None = None):
    """One page of a window's disagreement evidence. Pass the first page's `frontier` back on every later page."""
    db = await get_db()
    try:
        page = await read_page(db, window, after, limit, frontier, owner, variant, site)
    finally:
        await db.close()
    if page is None:
        raise HTTPException(404, f'no shadow window {window}')
    return page


@router.post('/agent-state')
async def publish_agent_state(request: Request):
    try:
        result = await AgentStateShadowStore().apply(await request.body(), applied_at=clock.now())
    except InvalidPublication as error:
        raise HTTPException(422, str(error)) from error
    if result.applied:
        return Response(status_code=204)
    return {'applied': False, 'reason': result.reason}
