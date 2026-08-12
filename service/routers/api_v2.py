"""The `/api/v1` router: composition, and nothing else.

Until v0.5.3 this file was `api_v2.py`, 20,545 lines at its peak and 6,987 by the end of the
domain extraction — and by then it declared ZERO routes. It was a helper library living at a
router's address, which meant anyone looking for the claim gate went looking in a router, and
anyone reading `service/routers/` assumed these were HTTP surfaces. The helpers moved to
`service/control_plane.py`; what remains here is the composition they were never part of.

There is deliberately NO re-export of the control-plane helpers from this module. A compatibility
shim would have preserved the misleading import surface and hidden every stale import instead of
failing on it, which is the opposite of what the move is for.

Domains are included HERE rather than in `main.py` so they keep the same `/api/v1` prefix and the
same metadata surface. Include order moves a domain's routes within `app.routes`; absolute order is
deliberately not an invariant. The property that can change meaning is pattern-shadows-literal, and
the shadow-pair gate is what pins that.
"""

from service.api_core.routing import domain_router

from service.routers.usage import router as _usage_router
from service.routers.settings import router as _settings_router
from service.routers.contracts import router as _contracts_router
from service.routers.stats import router as _stats_router
from service.routers.shared import router as _shared_router
from service.routers.analytics import router as _analytics_router
from service.routers.environments import router as _environments_router
from service.routers.spawn_requests import router as _spawn_requests_router
from service.routers.channels import router as _channels_router
from service.routers.sessions import router as _sessions_router
from service.routers.terminals import router as _terminals_router
from service.routers.meta import router as _meta_router
from service.routers.maintenance import router as _maintenance_router
from service.routers.dispatch_messages import router as _dispatch_messages_router
from service.routers.agents import router as _agents_router

router = domain_router(tags=["api"])

router.include_router(_usage_router)
router.include_router(_settings_router)
router.include_router(_contracts_router)
router.include_router(_stats_router)
router.include_router(_shared_router)
router.include_router(_analytics_router)
router.include_router(_environments_router)
router.include_router(_spawn_requests_router)
router.include_router(_channels_router)
router.include_router(_sessions_router)
router.include_router(_terminals_router)
router.include_router(_meta_router)
router.include_router(_maintenance_router)
router.include_router(_dispatch_messages_router)
router.include_router(_agents_router)
