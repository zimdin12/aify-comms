"""Service meta routes: the API root, the favicons, and the dashboard redirects.

v0.5.2k. Five trivial handlers that were never part of any domain — they describe the SERVICE, not a
resource. Split from the maintenance routes rather than dumped together with them: a redirect and a
destructive purge have nothing in common except that neither belonged to a domain, and "leftovers"
is not a responsibility.
"""

from __future__ import annotations

import logging

from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, RedirectResponse

from service.api_core.routing import domain_router
from service.config import get_config
from service.dashboard_redirect import dashboard_url

logger = logging.getLogger("aify_comms.routers.meta")

router = domain_router()


@router.get("/")
async def root():
    # Version comes from the loaded config (repo-root VERSION -> stamp -> config), never a
    # literal: this endpoint claimed "4.0.0" through the v0.1, v0.1.1 and v0.1.2 releases.
    return {
        "service": "aify-comms",
        "version": get_config().version,
        "storage": "sqlite",
        "endpoints": {
            "agents": "/api/v1/agents",
            "environments": "/api/v1/environments",
            "spawnRequests": "/api/v1/spawn-requests",
            "sessions": "/api/v1/sessions",
            "messages": "/api/v1/messages",
            "dispatch": "/api/v1/dispatch",
            "shared": "/api/v1/shared",
            "channels": "/api/v1/channels",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
        },
    }


@router.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    return FileResponse(Path(__file__).parent.parent / "favicon.svg", media_type="image/svg+xml")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    return FileResponse(Path(__file__).parent.parent / "favicon.svg", media_type="image/svg+xml")


@router.get("/dashboard", response_class=RedirectResponse)
async def dashboard(request: Request):
    return RedirectResponse(url=dashboard_url(request))


@router.get("/dashboard/dispatches", response_class=RedirectResponse)
async def dashboard_dispatches(request: Request):
    return RedirectResponse(url=dashboard_url(request))
