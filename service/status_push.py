"""Keeping pushed agent statuses current while somebody is watching, without a dashboard poll.

WHY THIS EXISTS. An agent's status is derived and cached in memory (reconcilers/status_cache.py). A
heartbeat or a turn marker only EXPIRES the cached entry; the recompute happens when something next
asks. Until the dashboard stopped polling, its `GET /agents` every 15 seconds was that something, so a
status change reached the screen within one poll as a side effect of a request that also rebuilt the
whole roster. The reconcile sweep asks too, but only once a minute.

With the dashboard updating on change, nothing asked in between. This loop asks instead: every
`STATUS_PUSH_INTERVAL_S` it recomputes the expired entries, and any status that moved is published by
the cache itself as a change to `agents` (service/change_feed.py). It is the same recompute the
roster ran, minus building and serialising the roster.

ONLY WHILE A DASHBOARD THAT READS CHANGES IS CONNECTED. With none there is nobody to push to, and the sweep
still keeps the cache honest for every other reader, so an idle host with no tab open pays nothing.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from service.api_core.status_refresh import _refresh_expired_agent_live_states
from service.db import get_db

logger = logging.getLogger(__name__)

#: The dashboard's old poll interval, so a status change is no slower to appear than it was.
STATUS_PUSH_INTERVAL_S = 15.0


async def refresh_expired_statuses_once() -> int:
    """Recompute every expired status entry. Returns how many were recomputed. Writes nothing."""
    db = await get_db()
    try:
        return await _refresh_expired_agent_live_states(db)
    finally:
        await db.close()


async def periodic_status_push(manager, *, interval_s: float = STATUS_PUSH_INTERVAL_S) -> None:
    while True:
        await asyncio.sleep(interval_s)
        if not manager.change_subscribers():
            continue
        try:
            await refresh_expired_statuses_once()
        except asyncio.CancelledError:
            raise
        except sqlite3.OperationalError as exc:
            # A busy database is the next tick's problem, not a reason to stop pushing.
            logger.debug("status push skipped a tick: %s", exc)
        except Exception:  # noqa: BLE001 -- one failed tick must not end the loop
            logger.exception("status push tick failed")
