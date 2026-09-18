"""The routes every bridge and dashboard depends on must exist, by name.

The full API surface is snapshotted by `test_route_metadata_inventory.py`: every one of its rows
carries METHOD and PATH, so a route removed or added fails there. This file used to hold a second,
METHOD + PATH only snapshot of the same table (`data/route_inventory.txt`), which could only fail
when the metadata snapshot also failed.

What is left is the one thing a snapshot cannot give: a handful of routes named explicitly, so that
regenerating the snapshot wholesale cannot silently drop one the fleet cannot lose.
"""

from __future__ import annotations

import unittest


def _live_routes() -> list[str]:
    from service.main import create_app

    app = create_app()
    out = set()
    for route in app.routes:
        if not hasattr(route, "path"):
            continue
        methods = getattr(route, "methods", None)
        for method in sorted(methods) if methods else ["WS"]:
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add(f"{method} {route.path}")
    return sorted(out)


class RouteInventoryTests(unittest.TestCase):
    def test_the_endpoints_the_fleet_cannot_lose(self):
        """Named explicitly, so that even a wholesale snapshot regeneration cannot silently drop the
        handful of routes every bridge and dashboard depends on."""
        actual = set(_live_routes())
        for route in [
            "GET /health",
            "GET /api/v1/agents",
            "POST /api/v1/agents",
            "GET /api/v1/agents/{agent_id}",
            # Read from the real table, not guessed: my first draft asserted `POST /api/v1/messages`
            # and `GET /api/v1/dispatch-runs/{run_id}`, neither of which exists. A gate built on
            # invented paths fails for the wrong reason on day one and, worse, would have been
            # "fixed" by deleting the assertions.
            "POST /api/v1/messages/send",
            "GET /api/v1/messages/inbox/{agent_id}",
            "POST /api/v1/environments/heartbeat",
            "POST /api/v1/spawn-requests/claim",
            "GET /api/v1/dispatch/runs/{run_id}",
            "PATCH /api/v1/dispatch/runs/{run_id}",
            "WS /ws",
        ]:
            with self.subTest(route):
                self.assertIn(route, actual)


if __name__ == "__main__":
    unittest.main()
