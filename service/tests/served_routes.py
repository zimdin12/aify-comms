"""Every route an app serves, flattened: the ONE walk the route gates share.

From fastapi 0.137.0 `include_router` no longer flattens child routes into `app.routes`; it leaves a
lazy `_IncludedRouter` there, so a plain walk of `app.routes` finds 11 entries where the app serves 129,
and every gate built on that walk passes having measured almost nothing. fastapi 0.138.0 added
`fastapi.routing.iter_route_contexts`, which yields one context per served route with the include
applied: the full path, the combined tags and dependencies, and everything else (`endpoint`,
`body_field`, `dependant`, `status_code`...) by delegation. Measured 2026-09-23: at 0.139.2 it yields the
same 129 (path, methods, name) rows as 0.136.1's flat `app.routes`, byte for byte.

A context is not the route object, so `type(context)` is always RouteContext. Ask `declared_class` for
the class the route was declared with (JsonApiRoute, APIRoute, Mount ...).

`test_the_route_inventory_is_not_empty.py` is the canary: it fails if this walk ever collapses again.
"""

from __future__ import annotations

from fastapi.routing import iter_route_contexts


def walk_routes(app) -> list:
    """Every route `app` serves, in matching order, as fastapi's own route contexts."""
    return list(iter_route_contexts(app.routes))


def declared_class(route) -> type:
    """The class the route was declared with, not the context wrapping it."""
    return type(route.original_route)
