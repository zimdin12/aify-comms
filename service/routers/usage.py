"""The `usage` route domain: quota pools and consumption.

v0.5.2b — the FIRST route domain extracted from `service/routers/api_v2.py`, chosen because it is the
smallest and most read-mostly one there is. The point of this slice is to prove the mechanism, not to
win line count: four handlers, no mutating writes to agent state, and an artifact (the usage pools)
that is trivially observable if anything goes wrong.

BUILT WITH `domain_router()`, NOT `APIRouter()`. That is the whole reason the harness shipped first.
The factory fixes `route_class=JsonApiRoute`, which carries the bounded SQLite write-lock retry, and
refuses an override. A domain built by hand would keep every body, path and method, pass the whole
suite, and silently 503 under concurrent writes.

`_POOL_CACHE` (once `_OPENAI_POOL_CACHE`) moved with the handlers and is mutable process state, so it is registered in
`service/tests/test_process_global_identity.py` alongside the other forkable globals — a second
module-level assignment would give two importers separate caches and the only symptom would be quota
readings that disagree depending on which path served them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.clock import now as _now
from service.usage_cache import consumption_set, consumption_summary, usage_all, usage_set
from service.usage_anthropic import collect_anthropic_pool
from service.usage_openai import collect_openai_pool
from service.db import get_db
from service.api_core.settings import _load_settings
from service.api_core.settings_spec import BY_KEY
from service.api_core.request_body import json_object_body

logger = logging.getLogger("aify_comms.routers.usage")

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
    usage_set(source_id, payload)
    return {"ok": True, "source_id": source_id}


# ONE CACHE FOR BOTH POOLS, keyed by collector. Each pool is read at most once per
# `usage_poll_minutes` (default 5) however many agents and dashboards ask: one read serves everyone.
_POOL_CACHE: dict[str, dict[str, Any]] = {
    "openai": {"at": 0.0, "pool": None, "inflight": None},
    "anthropic": {"at": 0.0, "pool": None, "inflight": None},
}


def _collectors():
    """Looked up when called, so a test that replaces a collector is the one used."""
    return (("openai", collect_openai_pool), ("anthropic", collect_anthropic_pool))


async def _poll_seconds() -> float:
    """The operator's `usage_poll_minutes`, in seconds; the declared default when settings cannot be read."""
    try:
        db = await get_db()
        try:
            return float((await _load_settings(db))["usage_poll_minutes"]) * 60
        finally:
            await db.close()
    except Exception:
        return float(BY_KEY["usage_poll_minutes"].default) * 60


async def _read_pool(name: str, collect, ttl: float):
    """The cached reading, or the one poll in flight for it: readers that arrive while a poll runs wait
    for that poll instead of starting their own (two simultaneous readers asked twice until the 0.7.4
    review). Two ways a poll comes back empty, handled differently: a collector that RAISES caches
    nothing, so the next reader polls again; one that returns None (how both collectors report a refused
    or unreachable provider) is a reading, cached for the interval like any other, so a provider that is
    down is asked once per interval, not once per reader. Either way any posted pool is served."""
    cache = _POOL_CACHE[name]
    if time.monotonic() - float(cache["at"] or 0) <= ttl:
        return cache["pool"]
    poll = cache.get("inflight")
    if poll is None or poll.done():
        poll = asyncio.ensure_future(_poll(name, collect))
        cache["inflight"] = poll
    # Shielded, so a reader that disconnects does not cancel the poll the others wait on.
    return await asyncio.shield(poll)


async def _poll(name: str, collect):
    cache = _POOL_CACHE[name]
    try:
        started = time.monotonic()
        fresh = await collect()
        if fresh:
            # Stamped when READ FROM THE PROVIDER, not when served: stamping on every GET made a cached
            # reading report an age of zero (v0.7 scan A14).
            fresh = dict(fresh, updated_at=_now(), stale=False)
        cache["at"] = started
        cache["pool"] = fresh
        return fresh
    finally:
        cache["inflight"] = None


@router.get("/usage")
async def get_usage():
    """Usage pools, collected BY THE SERVICE for OpenAI and Anthropic, so a fix costs no agent restart.

    The collector used to live only in the environment bridge, so every quota fix required
    restarting it -- which cycles the operator's managed agents -- and after that bridge was deleted
    in v0.6.3 nothing collected the Anthropic pool at all. Quota is a file read plus one HTTP GET per
    source. Bridge posts are still accepted (other hosts), but a fresh service-side reading wins.
    """
    pools = usage_all()
    ttl = await _poll_seconds()
    for name, collect in _collectors():
        try:
            fresh = await _read_pool(name, collect, ttl)
            if fresh:
                fresh = dict(fresh)
                pools = [p for p in pools if p.get("source_id") != fresh["source_id"]] + [fresh]
        except Exception:
            logger.debug("service-side %s usage collection failed; keeping any posted pool", name, exc_info=True)
    return {"pools": pools}


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
