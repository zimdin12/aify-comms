"""The route table is a snapshot, so a "pure move" cannot quietly change the API surface.

THE SECOND v0.5 GATE, and like the import-identity one it is established BEFORE the extraction.

Ten slices move 3,530 lines of reconcilers out of `api_v2.py`. The claim on that release is "no
behaviour change" — but a router file being rewritten is exactly where a decorator gets dropped with
the function it sat above, or a path changes case, or a method set narrows. None of that fails a
unit test; the endpoint simply stops existing, and the first person to find out is whoever calls it.

A count alone is not enough: dropping one route while adding another keeps the count identical. So
the gate holds the full sorted set, and any difference prints as added/removed lines.

WHEN THIS TEST FAILS AND THE CHANGE IS INTENTIONAL — adding an endpoint is normal work — update
EXPECTED_ROUTES in the same commit as the route. That is the point: the snapshot makes an API-surface
change a deliberate, reviewable line in a diff instead of a side effect of moving code.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "data" / "route_inventory.txt"


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
    def test_the_route_surface_matches_the_snapshot(self):
        expected = [l for l in SNAPSHOT.read_text(encoding="utf-8").splitlines() if l.strip()]
        actual = _live_routes()
        missing = [r for r in expected if r not in actual]
        added = [r for r in actual if r not in expected]
        self.assertEqual(
            (missing, added), ([], []),
            "The API surface changed.\n"
            f"  REMOVED (callers break): {missing}\n"
            f"  ADDED: {added}\n"
            "If this is intentional, update service/tests/data/route_inventory.txt in the same "
            "commit as the route change. If it happened while MOVING code, it is the bug this gate "
            "exists for — a decorator left behind with the function it sat above.",
        )

    def test_the_snapshot_is_not_empty(self):
        """A gate over an empty snapshot passes everything."""
        expected = [l for l in SNAPSHOT.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertGreater(len(expected), 100, "the snapshot looks truncated")

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
