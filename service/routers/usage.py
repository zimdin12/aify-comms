"""The `usage` route domain: quota pools and consumption.

v0.5.2b — the FIRST route domain extracted from `service/routers/api_v2.py`, chosen because it is the
smallest and most read-mostly one there is. The point of this slice is to prove the mechanism, not to
win line count: four handlers, no mutating writes to agent state, and an artifact (the usage pools)
that is trivially observable if anything goes wrong.

BUILT WITH `domain_router()`, NOT `APIRouter()`. That is the whole reason the harness shipped first.
The factory fixes `route_class=JsonApiRoute`, which carries the bounded SQLite write-lock retry, and
refuses an override. A domain built by hand would keep every body, path and method, pass the whole
suite, and silently 503 under concurrent writes.

The service's own pool reading, `_POOL_CACHE` with it, moved to `service/usage_pools.py` in 0.9.9 so the status view
can ask for it too.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.clock import now as _now
from service.usage_cache import consumption_set, consumption_summary
from service.usage_pools import read_pools, record_pool
from service.api_core.request_body import json_object_body

# NO tags here. The parent router applies tags=["api"] when it includes this one, and FastAPI
# COMBINES them -- declaring it in both produced tags=["api","api"], which is visible in the
# OpenAPI spec. The route metadata gate caught it on the first domain, which is exactly the
# class of silent surface change it was built for.
router = domain_router()


@router.post("/usage")
async def post_usage(request: Request):
    body = await json_object_body(request)
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(400, "source_id is required")
    payload = dict(body)
    payload["updated_at"] = _now()
    record_pool(source_id, payload)
    return {"ok": True, "source_id": source_id}


@router.get("/usage")
async def get_usage():
    """Usage pools, collected BY THE SERVICE for OpenAI and Anthropic, so a fix costs no agent restart.

    The collector used to live only in the environment bridge, so every quota fix required
    restarting it -- which cycles the operator's managed agents -- and after that bridge was deleted
    in v0.6.3 nothing collected the Anthropic pool at all. Quota is a file read plus one HTTP GET per
    source. Bridge posts are still accepted (other hosts), but a fresh service-side reading wins.
    """
    return await read_pools()


@router.post("/usage/consumption")
async def post_usage_consumption(request: Request):
    rows = (await json_object_body(request)).get("rows") or []
    if not isinstance(rows, list):
        raise HTTPException(400, "rows must be a list")
    consumption_set(rows)
    return {"ok": True, "count": len(rows)}


@router.get("/usage/consumption")
async def get_usage_consumption():
    return consumption_summary()
