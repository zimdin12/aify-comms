"""The agents domain package: one router, six route surfaces.

v0.5.2m, the LAST domain. Sub-routers are included in FIRST-APPEARANCE order of the original
definitions, so the route surface keeps its original relative sequence. Verified separately
that no agents path pattern can swallow an agents literal, so order is safe either way — but
preserving it means the shadow gate is comparing like with like.
"""

from service.api_core.routing import domain_router
from service.routers.agents.config import router as _config_router
from service.routers.agents.console import router as _console_router
from service.routers.agents.environment_assignment import router as _environment_assignment_router
from service.routers.agents.listen import router as _listen_router
from service.routers.agents.attributes import router as _attributes_router
from service.routers.agents.identity import router as _identity_router
from service.routers.agents.registration import router as _registration_router
from service.routers.agents.rename import router as _rename_router
from service.routers.agents.session_lease import router as _session_lease_router
from service.routers.agents.session_ops import router as _session_ops_router
from service.routers.agents.session_handle import router as _session_handle_router
from service.routers.agents.session_mode import router as _session_mode_router
from service.routers.agents.liveness import router as _liveness_router
from service.routers.agents.turn_boundaries import router as _turn_boundaries_router
from service.routers.agents.virtual_terminal import router as _virtual_terminal_router

router = domain_router()
router.include_router(_config_router)
# Environment assignment and the listen long-poll left `config.py` in v0.5.4, still in
# first-appearance order.
router.include_router(_environment_assignment_router)
router.include_router(_listen_router)
router.include_router(_console_router)
# Virtual-terminal provisioning left `console.py` in v0.5.4, still in first-appearance order.
router.include_router(_virtual_terminal_router)
router.include_router(_identity_router)
# `register_agent` left `identity.py` in v0.5.4 — 209 lines and four gates, against three short
# reads. Still in first-appearance order.
router.include_router(_registration_router)
# Attribute PATCHes and rename left `identity.py` in v0.5.4, still in first-appearance order.
router.include_router(_rename_router)
router.include_router(_attributes_router)
router.include_router(_session_ops_router)
# The session-lease trio left `session_ops.py` in v0.5.4, still in first-appearance order.
router.include_router(_session_lease_router)
router.include_router(_session_mode_router)
# The session-handle write left `session_mode.py` in v0.5.4 — a mode switch changes how an agent is
# driven, a handle change changes which conversation it is driving.
router.include_router(_session_handle_router)
router.include_router(_liveness_router)
# The turn-boundary pair left `liveness.py` in v0.5.4, still in first-appearance order.
router.include_router(_turn_boundaries_router)
