"""The canary for the route gates, which read as passes when they find nothing.

EXTERNAL REVIEW, 2026-09-21, finding 7. `test_route_inventory`, `test_route_metadata_inventory`,
`test_every_route_is_exercised`, `test_removed_agent_is_refused_everywhere`,
`test_bridge_write_bodies_are_declared` and three smaller censuses all ask which routes the app
serves. From fastapi 0.137.0 `include_router` leaves a lazy `_IncludedRouter` in `app.routes` instead
of flattening the child routes into it, so a plain walk finds 11 entries where the app serves 129 and
every one of those gates passes having measured almost nothing.

They now all walk through `service/tests/served_routes.py`, which asks fastapi's own
`iter_route_contexts` (0.138.0 and later). THIS FILE IS WHAT NOTICES IF THAT WALK COLLAPSES AGAIN --
a future fastapi changing what the helper yields, or somebody putting `app.routes` back. Measured
2026-09-23: with the helper returning the raw `app.routes` on fastapi 0.139.2, the first three cases
below go red (the fourth is the negative control and stays green), and so does every one of the
eight other files that walk it.

WHAT IT DOES NOT DO: pass judgement on which routes exist. That is the other files' work. This one
asks only whether there is anything there to judge.
"""

from __future__ import annotations

import unittest

from service.tests.served_routes import walk_routes


def _routes():
    from service.main import create_app

    return walk_routes(create_app())


def _route_paths() -> list[str]:
    return [route.path for route in _routes() if getattr(route, "path", None)]


class RouteInventoryIsNotEmptyTest(unittest.TestCase):
    #: Well below the real count (129 on 2026-09-21) and far above what a collapsed walk yields (8
    #: with a path, of 11 entries). A floor, not a pin: routes come and go, and a gate that had to be
    #: edited whenever one did would be edited without being thought about.
    FLOOR = 60

    def test_the_shared_walk_sees_the_whole_inventory(self):
        paths = _route_paths()
        self.assertGreater(
            len(paths), self.FLOOR,
            "the route walk in service/tests/served_routes.py has collapsed: every route gate is now "
            "passing on an almost empty inventory. Check what the resolved fastapi's "
            "iter_route_contexts yields.",
        )

    def test_no_entry_is_a_router_the_walk_did_not_see_through(self):
        """The mechanism itself, named, so the failure says WHAT changed rather than just a count."""
        opaque = [type(route).__name__ for route in _routes() if not getattr(route, "path", None)]
        self.assertEqual(
            opaque, [],
            "an entry with no `path` is a router the walk cannot see through "
            f"(found {opaque}); the gates are blind to everything behind it",
        )

    def test_the_walk_finds_routes_this_service_is_known_to_serve(self):
        """The positive control, on both sides of an include: a count can be large and still be the
        wrong thing entirely."""
        paths = set(_route_paths())
        # Named from the served inventory, not from memory: the first version of this control asked
        # for `/api/v1/messages`, which this service does not serve -- it serves `/messages/send` and
        # `/messages/recent`. A control that names something absent fails for its own reason and
        # tells you nothing about the instrument.
        for known in ("/api/v1/agents", "/api/v1/messages/send", "/health"):
            self.assertIn(known, paths, f"{known} is served; a walk that misses it is not measuring routes")

    def test_the_walk_does_not_invent_routes(self):
        """The negative control: an instrument that cannot say ABSENT cannot say PRESENT."""
        self.assertNotIn("/api/v1/definitely-not-a-route", set(_route_paths()))


if __name__ == "__main__":
    unittest.main()
