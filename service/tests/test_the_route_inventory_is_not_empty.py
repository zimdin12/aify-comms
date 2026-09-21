"""The canary for five gates that read as passes when they find nothing.

EXTERNAL REVIEW, 2026-09-21, finding 7. `test_route_inventory`, `test_route_metadata_inventory`,
`test_every_route_is_exercised`, `test_removed_agent_is_refused_everywhere` and
`test_bridge_write_bodies_are_declared` all walk `app.routes`. From fastapi 0.140 `include_router`
leaves a lazy `_IncludedRouter` there instead of flattening the child routes into it, so the walk
finds a handful of entries and every one of those gates passes having measured almost nothing.

MEASURED, both sides, on the same application: fastapi 0.141.1 in this project's own container builds
an app with 11 route entries, 3 of them `_IncludedRouter`; fastapi 0.136.1 builds it with 129. The
suite was therefore honest only where the resolved version happened to be old enough -- which is not
a property anybody can see from a green run.

`service/requirements.txt` now bounds fastapi below 0.140. THIS FILE IS WHAT NOTICES IF THAT SLIPS:
an upper bound in a requirements file is a decision somebody can undo in one character, and the
failure it prevents is silent. A red test here is loud, and it names the five gates that went quiet.

WHAT IT DOES NOT DO: pass judgement on which routes exist. That is the other five files' work. This
one asks only whether there is anything there to judge.
"""

from __future__ import annotations

import unittest


def _route_paths() -> list[str]:
    from service.main import create_app

    app = create_app()
    return [route.path for route in app.routes if hasattr(route, "path")]


class RouteInventoryIsNotEmptyTest(unittest.TestCase):
    #: Well below the real count (129 on 2026-09-21) and far above what a collapsed walk yields (8
    #: with a path, of 11 entries). A floor, not a pin: routes come and go, and a gate that had to be
    #: edited whenever one did would be edited without being thought about.
    FLOOR = 60

    def test_the_app_exposes_its_routes_to_a_plain_walk(self):
        paths = _route_paths()
        self.assertGreater(
            len(paths), self.FLOOR,
            "app.routes has collapsed: five inventory gates are now passing on an empty walk. "
            "Check the resolved fastapi version against the upper bound in service/requirements.txt.",
        )

    def test_no_route_entry_is_a_lazily_included_router(self):
        """The mechanism itself, named, so the failure says WHAT changed rather than just a count."""
        from service.main import create_app

        lazy = [type(route).__name__ for route in create_app().routes if not hasattr(route, "path")]
        self.assertEqual(
            lazy, [],
            "an entry with no `path` is a router the walk cannot see through "
            f"(found {lazy}); the gates that walk app.routes are blind to everything behind it",
        )

    def test_the_walk_finds_routes_this_service_is_known_to_serve(self):
        """The positive control: a count can be large and still be the wrong thing entirely."""
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
