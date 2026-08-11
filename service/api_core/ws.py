"""WebSocket hub access. The accessor is a leaf; the manager deliberately is NOT.

v0.5.1h. `_get_ws` is reached by twelve route domains and is five lines long, which made it an
obvious core candidate — but the interesting decision here was what NOT to move.

THE MANAGER STAYS ON `app.state`. It is created per application in `service/main.py`
(`app.state.ws_manager = ConnectionManager()`), so it is app-scoped, not process-scoped. Promoting it
to a module-level singleton would look like tidying and would actually be a behaviour change: every
`create_app()` would share one manager, so tests would stop getting isolated hubs and connections
from one app instance would be visible to another. The reviewer's condition for moving this family
was exactly that — leaf-own it only if it does not depend on app-local setup — and the measurement
says it does.

So only the ACCESSOR moves. It takes the request and reads app state, which needs nothing from this
service at all: a true leaf whose only import is FastAPI's `Request` type.
"""

from __future__ import annotations

from fastapi import Request


async def _get_ws(request: Request):
    try:
        return request.app.state.ws_manager
    except Exception:
        return None
