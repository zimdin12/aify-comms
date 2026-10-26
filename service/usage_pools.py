"""The service's own reading of the two quota pools, cached per pool and shared by every reader.

Moved out of `service/routers/usage.py` in 0.9.9 so the status view can ask for it too: a router may not be imported
from `api_core`. The bodies are unchanged; `read_pools` is the old `GET /usage` body, and `refresh_soon` is how a read
of the roster asks for a fresh reading without waiting for one (a read triggers it, never a timer).

`_POOL_CACHE` is mutable process state, registered in `service/tests/test_process_global_identity.py`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from service.api_core.settings import _load_settings
from service.api_core.settings_spec import BY_KEY
from service.clock import now as _now
from service.db import get_db
from service.usage_anthropic import collect_anthropic_pool
from service.usage_cache import limited_until, usage_all, usage_get, usage_set
from service.usage_openai import collect_openai_pool

logger = logging.getLogger("aify_comms.usage_pools")


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
            # Into the shared cache too, so the status view reads this pool without asking the provider (0.9.9).
            record_pool(fresh["source_id"], fresh)
        cache["at"] = started
        cache["pool"] = fresh
        return fresh
    finally:
        cache["inflight"] = None


async def read_pools():
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




def record_pool(source_id: str, pool: dict[str, Any]) -> None:
    """Keep a pool reading, and when it moves an agent across its usage limit, have every cached word re-derived on
    its next read, so `blocked` comes and goes with the reading rather than with the cache's own expiry."""
    from service.api_core.switched_view import expire_words
    from service.reconcilers.status_cache import _LIVE_STATE_CACHE

    before = limited_until(usage_get(source_id))
    usage_set(source_id, pool)
    if limited_until(usage_get(source_id)) != before:
        expire_words(list(_LIVE_STATE_CACHE))


#: Whether a roster read may start a provider read. ENABLED ONLY BY THE SERVICE'S LIFESPAN (service/main.py), like the
#: connection pool: a test app that reads the roster must never ask a provider with the host's own credentials.
BACKGROUND_REFRESH = {"enabled": False}


def refresh_soon() -> None:
    """Start a reading of any pool older than its interval, without waiting for it: the same cache, interval and
    in-flight read `read_pools` uses, so however many readers call this, each pool is asked at most once per
    `usage_poll_minutes`. A failure is logged and dropped; the caller is a read that must not fail for it."""
    if not BACKGROUND_REFRESH["enabled"]:
        return

    async def run():
        try:
            await read_pools()
        except Exception:
            logger.debug("background usage refresh failed", exc_info=True)

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass  # no loop: nothing to schedule on
