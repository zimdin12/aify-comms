"""The API key middleware — the only thing standing in front of every mutating endpoint.

`APIKeyMiddleware.dispatch` was among the 71 service functions the suite never entered. It is the
service's entire authentication story: with a key configured, anything not on its skip list needs
that key, and everything the fleet can do — spawning workers, injecting console keystrokes, reading
every message — is behind it.

PUBLIC PATHS MATCH EXACTLY. A future `/healthz-admin` must not inherit `/health`'s exemption.
Docs and the schema require the configured key too, including browser navigation using the
existing query-to-cookie carrier. The public-path policy is checked against real served routes.

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
from service.tests.served_routes import walk_routes

API_KEY = "correct-horse-battery-staple"

#: The independently specified public-path contract, checked against the middleware and real routes.
EXPECTED_SKIPS = {
    "/health", "/ready", "/version", "/ws",
    "/favicon.svg", "/favicon.ico", "/api/v1/favicon.svg", "/api/v1/favicon.ico",
}


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

    def test_agent_state_key_is_scoped_to_the_exact_post_only(self):
        app = _app_with_key()
        for route in ("/api/v1/agent-state", "/api/v1/agent-state/other", "/api/v1/other"):
            app.add_api_route(route, lambda: {"ok": True}, methods=["GET", "POST"])
        with TestClient(app) as client:
            header = {"x-aify-agent-state-key": API_KEY}
            self.assertEqual(client.post("/api/v1/agent-state", headers=header).status_code, 200)
            for method, route in (("GET", "/api/v1/agent-state"),
                                  ("POST", "/api/v1/agent-state/other"),
                                  ("POST", "/api/v1/other")):
                self.assertEqual(client.request(method, route, headers=header).status_code, 401)
            for key in ("", "wrong"):
                self.assertEqual(client.post("/api/v1/agent-state", headers={
                    "x-aify-agent-state-key": key}).status_code, 401)

    # ── the key itself ───────────────────────────────────────────────────────────────────────

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

    def test_every_documented_public_path_is_actually_skipped(self):
        app = FastAPI()
        app.add_middleware(APIKeyMiddleware, api_key=API_KEY)

        for prefix in EXPECTED_SKIPS:
            app.add_api_route(prefix, lambda: {"ok": True}, methods=["GET"])

        with TestClient(app) as client:
            for prefix in EXPECTED_SKIPS:
                with self.subTest(prefix=prefix):
                    self.assertEqual(client.get(prefix).status_code, 200,
                                     f"{prefix} required a key it is meant to be exempt from")

    def test_prefix_lookalikes_require_the_key_while_health_remains_public(self):
        app = _app_with_key()
        lookalikes = {path + suffix for path in EXPECTED_SKIPS for suffix in ("-admin", "/deep")}
        lookalikes |= {"/healthz-admin", "/docs-extra", "/redoc-extra", "/openapi.json-extra",
                       "/favicon-secrets", "/api/v1/favicon-secrets"}
        for path in lookalikes:
            app.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            for path in sorted(lookalikes):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 401, path)
                    self.assertEqual(client.get(path, headers={"X-API-Key": API_KEY}).status_code, 200, path)

    def test_public_exemptions_name_real_served_routes(self):
        """No invented exemptions; use the maintained walker, not the shallow lazy-router list."""
        served = {route.path for route in walk_routes(create_app())}
        self.assertIn("/api/v1/agents", served, "control: included routers were not expanded")
        self.assertNotIn("/__issue43_absent__", served)
        for path in EXPECTED_SKIPS:
            with self.subTest(path=path):
                self.assertIn(path, served)

    def test_the_expected_skip_list_matches_the_middlewares_own(self):
        """The list above is a copy, so it is compared against the source of truth rather than
        trusted — a prefix added to the middleware and not here would leave the census above
        checking the wrong set."""
        import ast
        import inspect
        import textwrap

        # THE LIST ITSELF, not every string in the method that happens to start with a slash. The
        # earlier version counted `source.count('"/')`, which was a fine proxy while `skip_paths` was
        # the only such literal -- and stopped being one the moment `set_cookie(path="/")` appeared.
        # That miscount was harmless; the same proxy would also have let a real new prefix hide behind
        # the removal of an unrelated string, which would not have been.
        tree = ast.parse(textwrap.dedent(inspect.getsource(APIKeyMiddleware.dispatch)))
        declared = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "skip_paths" for target in node.targets):
                value = node.value
                if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
                    value = value.args[0]
                declared.append(set(ast.literal_eval(value)))
        self.assertEqual(len(declared), 1, "skip_paths must have one explicit policy declaration")
        self.assertEqual(declared[0], EXPECTED_SKIPS, "the middleware exempts paths outside the public contract")


class ABrowserCanHoldTheKeyTests(unittest.TestCase):
    """Turning auth on locked the operator out of their own dashboard.

    MEASURED against the real `create_app()` with `API_KEY` set, which is the only place it shows --
    the suite's own base builds an app with NO middleware, so every test here was green either way:

        /                -> 401
        /api/v1/agents   -> 401
        /health          -> 200   (skip list)

    A browser cannot put `X-API-Key` on a document request. `?api_key=` was accepted but would have to
    be appended to the dashboard's URL and to every request its scripts make, which it does not do. So
    "protect the service" and "keep the dashboard usable" were mutually exclusive, and an installer
    flag that turned auth on would have handed over a locked door.
    """

    def setUp(self):
        self.client = TestClient(_app_with_key())

    def test_a_valid_key_in_the_URL_is_exchanged_for_a_cookie(self):
        response = self.client.get(f"/api/v1/agents?api_key={API_KEY}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("aify_api_key", response.cookies, "no cookie came back to hold the session")

    def test_the_cookie_alone_then_authenticates(self):
        """The whole point: paste the key once, and the dashboard works from then on."""
        self.client.get(f"/api/v1/agents?api_key={API_KEY}")
        self.assertEqual(self.client.get("/api/v1/agents").status_code, 200)

    def test_the_cookie_is_HttpOnly_and_SameSite_Lax(self):
        """SameSite is doing security work here, not tidiness. A cookie travels automatically, so
        without it every state-changing route becomes reachable from any page the operator visits --
        a hole the header-only scheme did not have, opened by the fix for a different problem."""
        header = self.client.get(f"/api/v1/agents?api_key={API_KEY}").headers["set-cookie"].lower()
        self.assertIn("httponly", header, "page script can read the key back out")
        self.assertIn("samesite=lax", header, "the cookie would ride along on a cross-site request")
        self.assertIn("path=/", header)

    def test_a_WRONG_key_in_the_URL_sets_no_cookie(self):
        response = self.client.get("/api/v1/agents?api_key=wrong")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("set-cookie", {k.lower() for k in response.headers},
                         "a refused request handed out a session anyway")

    def test_a_HEADER_call_gets_no_cookie(self):
        """A program has no use for one, and setting it on every call would keep refreshing the expiry
        of a browser session nobody is using."""
        response = self.client.get("/api/v1/agents", headers={"X-API-Key": API_KEY})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("set-cookie", {k.lower() for k in response.headers})

    def test_a_request_that_authenticated_BY_COOKIE_does_not_reset_it(self):
        self.client.get(f"/api/v1/agents?api_key={API_KEY}")
        response = self.client.get("/api/v1/agents")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("set-cookie", {k.lower() for k in response.headers})

    def test_a_FORGED_cookie_does_not_authenticate(self):
        """The cookie carries the key itself, so it is checked by the same constant-time comparison as
        every other carrier. It is a convenience for transport, never a second way in."""
        client = TestClient(_app_with_key())
        client.cookies.set("aify_api_key", "not-the-key")
        self.assertEqual(client.get("/api/v1/agents").status_code, 401)
