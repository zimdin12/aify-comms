"""The five routes that describe the SERVICE rather than a resource: `/`, two favicons, two redirects.

`root` and the favicon handlers were among the service functions the suite never entered. They are
small, and that is exactly why they rot quietly: nothing downstream fails when the API root lists an
endpoint that moved, or when a favicon route points at a file that is no longer beside it.

THE ROOT ENDPOINT IS A DISCOVERY SURFACE. It advertises eleven endpoint paths, and an agent or an
operator reading it has no way to tell a live path from one that was renamed two releases ago. So it
is tested as a CENSUS against the app's own route table rather than against a copied list — a
hardcoded expectation here would be the same lie in a second place.

ITS VERSION FIELD HAS ALREADY BEEN WRONG ONCE, in the way that matters: it was a literal, and it
reported "4.0.0" through the v0.1, v0.1.1 and v0.1.2 releases. The test below changes the loaded
config and requires the endpoint to follow, which no literal can do.

FOUR FAVICON ROUTES SERVE ONE FILE, declared in three modules — `routers/meta.py` (under the API
prefix), `main.py` (at the root) and `new_dashboard_app.py` (the other process). They are not
consolidated here: two are on a different app and one set exists because browsers request
`/favicon.ico` from the ORIGIN, not from wherever the API happens to be mounted. What they get
instead is an AGREEMENT test — the duplication is fine as long as it cannot drift, and the way it
drifts is one of them keeping a path to a file that has moved.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from service.config import get_config
from service.main import create_app
from service.new_dashboard_app import app as dashboard_app

FAVICON_FILE = Path(__file__).resolve().parents[1] / "favicon.svg"


class ServiceRootTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        self.cfg = get_config()
        self._saved_version = self.cfg.version

    def tearDown(self):
        self.cfg.version = self._saved_version
        self.client.close()

    def _root(self) -> dict:
        response = self.client.get("/api/v1/")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_root_identifies_the_service(self):
        payload = self._root()
        self.assertEqual(payload["service"], "aify-comms")
        self.assertEqual(payload["storage"], "sqlite")

    def test_the_version_comes_from_the_LOADED_CONFIG(self):
        """The recorded bug, asserted rather than commented: this field was a literal and reported
        `4.0.0` across three real releases. A literal cannot follow the config; this does."""
        self.cfg.version = "9.9.9-test"
        self.assertEqual(self._root()["version"], "9.9.9-test")

    def test_EVERY_advertised_endpoint_is_actually_SERVED(self):
        """A census against the app's own routes, not against a copy of the list. An endpoint map
        is read by agents and operators to find their way around, and a path that moved leaves no
        trace here — the entry keeps looking authoritative while it 404s."""
        served = {getattr(route, "path", "") for route in self.app.routes}
        for name, path in self._root()["endpoints"].items():
            with self.subTest(endpoint=name):
                self.assertTrue(
                    any(route == path or route.startswith(path.rstrip("/") + "/")
                        for route in served),
                    f"{name} advertises {path}, which no route serves",
                )

    def test_the_advertised_paths_all_sit_under_the_api_prefix(self):
        """They are absolute paths a client pastes onto the origin. A relative one would resolve
        against whatever page the reader happened to be on."""
        for name, path in self._root()["endpoints"].items():
            with self.subTest(endpoint=name):
                self.assertTrue(path.startswith("/api/v1/"), f"{name} -> {path}")


class FaviconTests(unittest.TestCase):
    """One file, four routes, three modules. Tested for agreement rather than consolidated."""

    def setUp(self):
        self.client = TestClient(create_app())
        self.dashboard = TestClient(dashboard_app)

    def tearDown(self):
        self.client.close()
        self.dashboard.close()

    def test_the_favicon_file_the_routes_point_at_EXISTS(self):
        """Every one of them resolves a path relative to its own module. The failure is a 500 on a
        request the browser makes unprompted, so it shows up in a console rather than in a report."""
        self.assertTrue(FAVICON_FILE.is_file(), f"{FAVICON_FILE} is missing")

    def test_every_favicon_route_serves_THE_SAME_BYTES(self):
        expected = FAVICON_FILE.read_bytes()
        for client, path in (
            (self.client, "/favicon.svg"),
            (self.client, "/favicon.ico"),
            (self.client, "/api/v1/favicon.svg"),
            (self.client, "/api/v1/favicon.ico"),
            (self.dashboard, "/favicon.svg"),
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, expected)

    def test_EVERY_route_declares_the_SVG_media_type(self):
        """There is no `.ico` file in this repo. Both `.ico` routes exist because browsers request
        `/favicon.ico` from the origin whether or not the page asked for one, and a 404 on every
        page load is noise an operator has to learn to ignore. Serving the SVG under the SVG media
        type is the honest half — the browser reads the type, not the extension, and declaring
        `image/x-icon` over SVG bytes is how a favicon renders as a broken image instead.

        Asserted for ALL FOUR routes: my first version checked only the root `.ico`, so a mutation
        of the API-prefixed one's media type went uncaught."""
        for path in ("/favicon.svg", "/favicon.ico", "/api/v1/favicon.svg", "/api/v1/favicon.ico"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.headers["content-type"], "image/svg+xml")
                self.assertTrue(response.content.lstrip().startswith(b"<"), response.content[:40])

    def test_favicons_are_served_WITHOUT_an_api_key(self):
        """They are on the middleware's skip list, and they have to be: a browser requesting a
        favicon sends no key, and a 401 on it is a permanently broken tab icon on an authenticated
        deployment."""
        cfg = get_config()
        saved = cfg.api_key
        cfg.api_key = "a-real-key"
        try:
            client = TestClient(create_app())
            for path in ("/favicon.svg", "/favicon.ico", "/api/v1/favicon.svg"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 200)
            client.close()
        finally:
            cfg.api_key = saved


# The two dashboard REDIRECT routes are not tested here. `test_dashboard_redirect.py` already drives
# them through `create_app()` and pins the exact status and Location, and a second weaker copy beside
# it would be a test that passes for whichever of the two happens to be right.


if __name__ == "__main__":
    unittest.main()
