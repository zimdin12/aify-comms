"""The agents domain package: one router, six route surfaces.

v0.5.2m, the LAST domain. Sub-routers are included in FIRST-APPEARANCE order of the original
definitions, so the route surface keeps its original relative sequence. Verified separately
that no agents path pattern can swallow an agents literal, so order is safe either way — but
preserving it means the shadow gate is comparing like with like.
"""

from service.api_core.routing import domain_router
from service.routers.agents.config import router as _config_router
from service.routers.agents.console import router as _console_router
from service.routers.agents.attributes import router as _attributes_router
from service.routers.agents.identity import router as _identity_router
from service.routers.agents.rename import router as _rename_router
from service.routers.agents.session_ops import router as _session_ops_router
from service.routers.agents.session_mode import router as _session_mode_router
from service.routers.agents.liveness import router as _liveness_router

router = domain_router()
router.include_router(_config_router)
router.include_router(_console_router)
router.include_router(_identity_router)
# Attribute PATCHes and rename left `identity.py` in v0.5.4, still in first-appearance order.
router.include_router(_rename_router)
router.include_router(_attributes_router)
router.include_router(_session_ops_router)
router.include_router(_session_mode_router)
router.include_router(_liveness_router)
