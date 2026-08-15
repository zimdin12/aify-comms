"""The dispatch+messages domain package: one router, two route surfaces, one shared owner.

v0.5.2l. The reviewer's preferred shape over two mutually-borrowing modules: dispatch and messages
borrow eight helpers from each other, so splitting them into independent modules would have produced
two shims per helper and no real owner. Here they are one ownership unit with an explicit internal
boundary.
"""

from service.api_core.routing import domain_router
from service.routers.dispatch_messages.controls import router as _controls_router
from service.routers.dispatch_messages.dispatch import router as _dispatch_router
from service.routers.dispatch_messages.messages import router as _messages_router

router = domain_router()
router.include_router(_dispatch_router)
# Controls left `dispatch.py` in v0.5.4 with a clean closure — see controls.py.
router.include_router(_controls_router)
router.include_router(_messages_router)
