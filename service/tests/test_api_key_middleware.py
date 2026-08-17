"""The API key middleware — the only thing standing in front of every mutating endpoint.

`APIKeyMiddleware.dispatch` was among the 71 service functions the suite never entered. It is the
service's entire authentication story: with a key configured, anything not on its skip list needs
that key, and everything the fleet can do — spawning workers, injecting console keystrokes, reading
every message — is behind it.

THE SKIP LIST IS PREFIX MATCHING, and that is the part worth testing rather than reading.
`path.startswith("/health")` exempts `/healthz`, `/health/anything` and — if such a route ever
existed — `/health-secrets`. The list is fine today, and "today" is exactly the sort of claim that
needs a test: the last check here asserts it against the app's REAL routes, so a future endpoint
whose path happens to begin with `/version` or `/ws` cannot slip in unauthenticated.

THE NON-ASCII KEY IS A REGRESSION TEST. `hmac.compare_digest` raises TypeError on a str containing
non-ASCII code points, unhandled — so a garbage key returned HTTP 500 from every protected endpoint
instead of a clean 401 (bughunt 2026-07-03). A 500 there is worse than noise: it tells an attacker
their input reached something, and it looks like an outage to an operator.

CONSTANT-TIME COMPARISON IS THE POINT OF `compare_digest`, so the test asserts it is what runs
rather than trying to time anything — a timing assertion in a test suite is a flake, and the
property that matters is which function is called.
"""

from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service import main as main_module
from service.main import APIKeyMiddleware, create_app

API_KEY = "correct-horse-battery-staple"

#: Every prefix the middleware lets through unauthenticated, copied from it deliberately: the test
#: below compares this list against the middleware's own, so drift fails rather than passing quietly.
EXPECTED_SKIPS = [
    "/health", "/ready", "/version", "/docs", "/redoc", "/openapi.json",
    "/ws", "/favicon", "/api/v1/favicon",
]


def _app_with_key(api_key: str = API_KEY) -> FastAPI:
    """A tiny app carrying ONLY the middleware, plus one protected route and one skipped one."""
    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key=api_key)

    @app.get("/api/v1/agents")
    async def _protected():
        return {"ok": True}

    @app.get("/health")
    async def _health():
        return {"status": "healthy"}

    return app


class ApiKeyMiddlewareTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_app_with_key())

    def tearDown(self):
        self.client.close()

    # ── the key itself ───────────────────────────────────────────────────────────────────────

    def test_the_right_key_in_the_HEADER_is_admitted(self):
        response = self.client.get("/api/v1/agents", headers={"X-API-Key": API_KEY})
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_right_key_in_the_QUERY_STRING_is_admitted(self):
        """The bridge and some tools pass it this way. Dropping it would lock them out silently."""
        response = self.client.get("/api/v1/agents", params={"api_key": API_KEY})
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_header_name_is_matched_case_insensitively(self):
        """HTTP header names are case-insensitive and clients spell this one every way. A
        case-sensitive lookup would reject a correct key from half of them."""
        for name in ("X-API-Key", "x-api-key", "X-Api-Key", "X-API-KEY"):
            with self.subTest(header=name):
                response = self.client.get("/api/v1/agents", headers={name: API_KEY})
                self.assertEqual(response.status_code, 200, response.text)

    def test_NO_key_is_refused_with_an_actionable_message(self):
        response = self.client.get("/api/v1/agents")
        self.assertEqual(response.status_code, 401)
        self.assertIn("X-API-Key", response.json()["error"],
                      "the refusal must say HOW to authenticate, not just that it failed")
        self.assertIn("api_key", response.json()["error"])

    def test_a_WRONG_key_is_refused(self):
        for wrong in ("", "nope", API_KEY + "x", API_KEY[:-1], API_KEY.upper()):
            with self.subTest(key=wrong):
                response = self.client.get("/api/v1/agents", headers={"X-API-Key": wrong})
                self.assertEqual(response.status_code, 401, f"{wrong!r} was admitted")

    def test_a_NON_ASCII_key_is_a_clean_401_and_not_a_500(self):
        """THE 2026-07-03 BUGHUNT. `hmac.compare_digest` raises TypeError on a str with non-ASCII
        code points, and it was unhandled: every protected endpoint answered 500 for a garbage key.
        A 500 tells an attacker their input reached something and looks like an outage to an
        operator.

        DRIVEN THROUGH THE QUERY STRING, which is the only way such a key can arrive. HTTP header
        values are latin-1 on the wire and a compliant client refuses to send anything else — my
        first version sent these as headers and failed inside the TEST CLIENT, never reaching the
        middleware at all. A percent-encoded query parameter decodes to a full unicode str, which is
        exactly what reached `compare_digest` when this broke.
        """
        for key in ("ключ", "🔑", "naïve-key", "​zero-width"):
            with self.subTest(key=key):
                response = self.client.get("/api/v1/agents", params={"api_key": key})
                self.assertEqual(response.status_code, 401, f"{key!r} did not produce a clean 401")

    def test_a_non_ascii_key_cannot_even_be_SENT_as_a_header(self):
        """The other half of the same fact, pinned so the query-string test above does not read as
        an arbitrary choice: the header path cannot carry these bytes, so the query path is the
        whole attack surface for this bug."""
        with self.assertRaises(UnicodeEncodeError):
            self.client.get("/api/v1/agents", headers={"X-API-Key": "ключ"})

    def test_the_comparison_is_the_constant_time_one(self):
        """Asserted by WHICH function runs, not by timing: a timing assertion in a suite is a flake,
        and `==` on a secret is the thing being prevented."""
        # `patch.object`, not a string target: `service.main.hmac.compare_digest` is an attribute
        # path through a module and `test_patch_targets_resolve.py` refuses it — correctly, since a
        # string target that never resolves is how a patch outlives what it points at. Second time
        # this session; the rule is now in the memory note.
        with mock.patch.object(main_module.hmac, "compare_digest",
                               wraps=main_module.hmac.compare_digest) as spy:
            self.client.get("/api/v1/agents", headers={"X-API-Key": API_KEY})
        self.assertTrue(spy.called, "the key was compared with something other than compare_digest")

    def test_the_header_wins_when_both_are_supplied(self):
        """Pinned as observed. A caller sending a good header and a stale query param must not be
        refused because of the leftover."""
        response = self.client.get(
            "/api/v1/agents", headers={"X-API-Key": API_KEY}, params={"api_key": "stale"},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ── the skip list ────────────────────────────────────────────────────────────────────────

    def test_health_is_reachable_without_a_key(self):
        """It is the container's healthcheck. Requiring a key there means docker restarts a service
        that is running perfectly well."""
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_every_documented_skip_prefix_is_actually_skipped(self):
        app = FastAPI()
        app.add_middleware(APIKeyMiddleware, api_key=API_KEY)

        for prefix in EXPECTED_SKIPS:
            app.add_api_route(prefix, lambda: {"ok": True}, methods=["GET"])

        with TestClient(app) as client:
            for prefix in EXPECTED_SKIPS:
                with self.subTest(prefix=prefix):
                    self.assertEqual(client.get(prefix).status_code, 200,
                                     f"{prefix} required a key it is meant to be exempt from")

    def test_the_skip_list_matches_by_PREFIX_and_the_test_says_so(self):
        """Observed behaviour, recorded because it is the risky half: `/healthz` and
        `/health/deep` are exempt too. That is fine while no protected route starts with one of
        these — which is what the next test checks against the real app."""
        app = FastAPI()
        app.add_middleware(APIKeyMiddleware, api_key=API_KEY)
        app.add_api_route("/healthz-anything", lambda: {"ok": True}, methods=["GET"])
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz-anything").status_code, 200)

    def test_NO_REAL_ROUTE_is_unauthenticated_by_accident(self):
        """THE ONE THAT MATTERS FOR THE FUTURE. Every route the real app serves is checked against
        the skip prefixes, so an endpoint added later whose path happens to begin with `/version` or
        `/ws` cannot become unauthenticated without this failing.

        THE EXPECTED SET IS DERIVED, NOT LISTED. Writing the favicon and docs paths out here made
        `test_every_route_is_exercised.py` report its whole FRAMEWORK_ROUTES list as "now exercised"
        — that gate matches a route by any test mentioning its path, so a literal list here would
        have emptied a list whose job is to record which routes NO handler in this repo owns. The
        framework half is taken from that gate's own constant instead, and only the four public
        endpoints this service really serves are named.
        """
        from service.tests.test_every_route_is_exercised import FRAMEWORK_ROUTES

        app = create_app()
        framework_paths = {entry.split(" ", 1)[1] for entry in FRAMEWORK_ROUTES}
        # The docs trio is FastAPI's, and unauthenticated on purpose: it exposes the API SHAPE and
        # no data. Named here rather than assumed, so switching them off later is a deliberate edit.
        #
        # THE FOUR FAVICONS ARE NAMED HERE ON THEIR OWN MERIT since 2026-08-17. They used to arrive
        # through `framework_paths` above, which was borrowing a list about TEST COVERAGE to answer
        # a question about AUTH — and the moment they gained tests and left that list, this gate
        # reported them as newly unauthenticated. They are not new and they are not framework
        # routes: they are handlers in this repo that must answer without a key, because a browser
        # requesting a favicon sends none and a 401 is a permanently broken tab icon.
        public_endpoints = {"/health", "/ready", "/version", "/ws",
                            "/docs", "/redoc", "/openapi.json",
                            "/favicon.svg", "/favicon.ico",
                            "/api/v1/favicon.svg", "/api/v1/favicon.ico"}

        unexpected = sorted(
            route.path for route in app.routes
            if any(str(getattr(route, "path", "")).startswith(p) for p in EXPECTED_SKIPS)
            and route.path not in framework_paths
            and route.path not in public_endpoints
        )
        self.assertEqual(
            unexpected, [],
            "these routes are exempt from API-key auth and are neither a framework route nor one of "
            "the four intended public endpoints — either one is new and unauthenticated, or the "
            "skip prefixes have changed: " + ", ".join(unexpected),
        )

    def test_the_four_public_endpoints_really_are_served(self):
        """Anti-vacuity for the census above: if none of them existed, the check would pass by
        having nothing to classify."""
        app = create_app()
        served = {getattr(route, "path", "") for route in app.routes}
        for path in ("/health", "/ready", "/version", "/ws"):
            with self.subTest(path=path):
                self.assertIn(path, served)

    def test_the_expected_skip_list_matches_the_middlewares_own(self):
        """The list above is a copy, so it is compared against the source of truth rather than
        trusted — a prefix added to the middleware and not here would leave the census above
        checking the wrong set."""
        import inspect

        source = inspect.getsource(APIKeyMiddleware.dispatch)
        for prefix in EXPECTED_SKIPS:
            with self.subTest(prefix=prefix):
                self.assertIn(f'"{prefix}"', source)
        self.assertEqual(
            source.count('"/'), len(EXPECTED_SKIPS),
            "the middleware skips a prefix this test does not know about",
        )
