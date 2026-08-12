"""The `usage` route domain: quota pools and consumption.

v0.5.2b — the FIRST route domain extracted from `service/routers/api_v2.py`, chosen because it is the
smallest and most read-mostly one there is. The point of this slice is to prove the mechanism, not to
win line count: four handlers, no mutating writes to agent state, and an artifact (the usage pools)
that is trivially observable if anything goes wrong.

BUILT WITH `domain_router()`, NOT `APIRouter()`. That is the whole reason the harness shipped first.
The factory fixes `route_class=JsonApiRoute`, which carries the bounded SQLite write-lock retry, and
refuses an override. A domain built by hand would keep every body, path and method, pass the whole
suite, and silently 503 under concurrent writes.

`_OPENAI_POOL_CACHE` moved with the handlers and is mutable process state, so it is registered in
`service/tests/test_process_global_identity.py` alongside the other forkable globals — a second
module-level assignment would give two importers separate caches and the only symptom would be quota
readings that disagree depending on which path served them.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.clock import now as _now
from service.usage_cache import consumption_set, consumption_summary, usage_all, usage_set
from service.usage_openai import collect_openai_pool

logger = logging.getLogger("aify_comms.routers.usage")

# NO tags here. The parent router applies tags=["api"] when it includes this one, and FastAPI
# COMBINES them -- declaring it in both produced tags=["api","api"], which is visible in the
# OpenAPI spec. The route metadata gate caught it on the first domain, which is exactly the
# class of silent surface change it was built for.
router = domain_router()


@router.post("/usage")
async def post_usage(request: Request):
    body = await request.json()
    source_id = str((body or {}).get("source_id") or "").strip()
    if not source_id:
        raise HTTPException(400, "source_id is required")
    payload = dict(body)
    payload["updated_at"] = _now()
    usage_set(source_id, payload)
    return {"ok": True, "source_id": source_id}


_OPENAI_POOL_TTL_SECONDS = 120.0


_OPENAI_POOL_CACHE: dict[str, Any] = {"at": 0.0, "pool": None}


@router.get("/usage")
async def get_usage():
    """Usage pools — collected BY THE SERVICE for OpenAI, so a fix costs no agent restart.

    The collector used to live only in the environment bridge, so every quota fix required
    restarting it — which cycles the operator's managed agents. Quota is a file read plus one HTTP
    GET; it has no business costing a restart. Bridge posts are still accepted (other hosts), but
    a fresh service-side reading wins.
    """
    pools = usage_all()
    try:
        now = time.monotonic()
        if now - float(_OPENAI_POOL_CACHE["at"] or 0) > _OPENAI_POOL_TTL_SECONDS:
            fresh = await collect_openai_pool()
            _OPENAI_POOL_CACHE["at"] = now
            _OPENAI_POOL_CACHE["pool"] = fresh
        fresh = _OPENAI_POOL_CACHE["pool"]
        if fresh:
            fresh = dict(fresh)
            fresh["updated_at"] = _now()
            fresh["stale"] = False
            pools = [p for p in pools if p.get("source_id") != fresh["source_id"]] + [fresh]
    except Exception:
        logger.debug("service-side OpenAI usage collection failed; keeping bridge-posted pool", exc_info=True)
    return {"pools": pools}


@router.post("/usage/consumption")
async def post_usage_consumption(request: Request):
    body = await request.json()
    rows = (body or {}).get("rows") or []
    consumption_set(rows)
    return {"ok": True, "count": len(rows)}


@router.get("/usage/consumption")
async def get_usage_consumption():
    return consumption_summary()
