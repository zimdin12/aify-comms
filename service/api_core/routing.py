"""The shared route class and the domain-router factory.

v0.5.2a — the harness the whole domain phase is built on, extracted BEFORE the first domain moves so
that no domain has to invent it.

WHY THIS EXISTS AT ALL. `JsonApiRoute` is not decoration. It wraps every handler in a bounded retry
over SQLite write-lock contention — the fix for the recurring `database is locked` 503s — and it is
configured on the ROUTER, not on the decorator. A handler moved onto a bare `APIRouter()` keeps its
body, its path and its method, passes every existing test, and silently loses lock-retry: it simply
starts 503ing under concurrent writes, load-dependently, where CI will never see it. That is the
failure this phase is most likely to cause, so the router is not something a domain gets to build by
hand.

Use `domain_router()`. It cannot be constructed without the route class.

The class moved here verbatim from `service/routers/api_v2.py`, and the logger name moved with it —
nothing outside that module referenced `aify_comms.api_v2`, and the reviewer's standing ruling is
that the logger follows the owner rather than being aliased back.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

logger = logging.getLogger("aify_comms.routing")


class JsonApiRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        # Bounded retry on transient write-lock contention before surfacing a 503. busy_timeout
        # already lets SQLite spin on the lock for a few seconds; under a heavy concurrent-write
        # burst (e.g. many agents messaging during a test) a writer can still starve and raise
        # "database is locked", which previously 503'd and poisoned callers/tests. A lock error is
        # raised at BEGIN IMMEDIATE — before any commit — so re-running the handler is safe for the
        # atomic single-transaction writes this service uses; FastAPI caches the request body, so the
        # re-run re-reads it fine. Reads never take the write lock, so they never reach this path.
        _LOCK_RETRY_BACKOFFS = (0.1, 0.25, 0.5)

        async def custom_route_handler(request: Request):
            for attempt in range(len(_LOCK_RETRY_BACKOFFS) + 1):
                try:
                    return await original_handler(request)
                except (HTTPException, RequestValidationError):
                    raise
                except sqlite3.OperationalError as error:
                    message = str(error) or "database operation failed"
                    locked = "locked" in message.lower() or "busy" in message.lower()
                    if locked and attempt < len(_LOCK_RETRY_BACKOFFS):
                        await asyncio.sleep(_LOCK_RETRY_BACKOFFS[attempt])
                        continue  # the burst usually clears within a retry; absorb it, don't 503
                    status_code = 503 if locked else 500
                    logger.warning(
                        "DB OperationalError on %s %s after %d attempt(s): %s",
                        request.method, request.url.path, attempt + 1, message,
                    )
                    return JSONResponse(
                        status_code=status_code,
                        content={"ok": False, "error": f"Database temporarily unavailable: {message}"},
                    )
                except Exception as error:
                    # Never silently swallow an unexpected error into a tidy 500 —
                    # that is exactly what makes production incidents undebuggable.
                    logger.exception(
                        "Unhandled error on %s %s", request.method, request.url.path
                    )
                    return JSONResponse(
                        status_code=500,
                        content={"ok": False, "error": str(error) or error.__class__.__name__},
                    )

        return custom_route_handler


def domain_router(**kwargs) -> APIRouter:
    """Build a domain router that CANNOT be missing the lock-retry route class.

    Every route domain extracted from `api_v2.py` must be created through here. Passing
    `route_class` explicitly is rejected rather than honoured: the entire point is that no domain can
    opt out, or opt in to a subtly different one, by hand.
    """
    if "route_class" in kwargs:
        raise TypeError(
            "route_class is fixed to JsonApiRoute for domain routers. A domain that overrides it "
            "loses the bounded SQLite write-lock retry and will 503 under concurrent writes."
        )
    return APIRouter(route_class=JsonApiRoute, **kwargs)
