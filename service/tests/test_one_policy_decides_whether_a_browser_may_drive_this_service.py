r"""HTTP and the WebSocket must ask ONE question, and it must be the stronger one.

WHAT WAS WRONG. The HTTP guard refused only `Sec-Fetch-Site: cross-site`, which left two ways in:

  * a hostile `Origin` carrying NO Fetch Metadata header passed untouched -- `Origin` is itself a
    browser signal, and the guard never consulted the one header that names the caller;
  * `same-site` passed WHOLESALE, and same-site means the registrable DOMAIN matches, not the host.
    An attacker-controlled sibling subdomain is same-site, so the weakest of the three values was
    being treated as proof.

Meanwhile the WebSocket check already compared by HOST -- the right model. Two guards for one
question, disagreeing, is how the weaker one becomes the way in.

WHAT THESE PIN. The policy itself, then that BOTH doors consult it. A pure predicate proven in
isolation leaves the call to it unproven, and this repo has shipped exactly that before.
"""

from __future__ import annotations

import unittest

from service.api_core.browser_origin import SAFE_METHODS, browser_request_is_allowed

HOST = "aify.local:8800"
ALLOWED = ["https://named.example"]


#: `aify.local` is TRUSTED in these cases on purpose. The same-host shortcut now requires it: two
#: client-supplied values agreeing is not evidence, because under DNS rebinding Origin and Host are
#: both the attacker's name. Cases that need an UNTRUSTED host pass `trusted_hosts=[]` explicitly.
TRUSTED = ["aify.local"]


def allowed(**over) -> bool:
    return browser_request_is_allowed(**{
        "host": HOST, "allowed_origins": ALLOWED, "trusted_hosts": TRUSTED, **over})


class TheOriginIsReadFirstAndIsConclusiveTests(unittest.TestCase):
    def test_a_hostile_origin_is_refused_even_with_NO_fetch_metadata(self):
        """THE FIRST HOLE. The old guard keyed on `Sec-Fetch-Site` alone, so a request carrying a
        hostile `Origin` and no Fetch Metadata was never examined at all."""
        self.assertFalse(allowed(origin="https://evil.example", sec_fetch_site=""))

    def test_a_hostile_origin_is_refused_even_when_fetch_metadata_says_same_origin(self):
        """No Fetch Metadata value rescues an Origin we do not serve. A header a page can influence
        must not be able to vouch for one it cannot."""
        for site in ("same-origin", "same-site", "none", ""):
            with self.subTest(sec_fetch_site=site):
                self.assertFalse(allowed(origin="https://evil.example", sec_fetch_site=site))

    def test_the_operators_own_host_is_allowed_on_ANY_port(self):
        """Dashboard Next answers on another PORT of the same host. Ports do not make a different
        site, and an attacker cannot serve from the operator's own hostname."""
        self.assertTrue(allowed(origin="http://aify.local:8801", sec_fetch_site="same-site"))
        self.assertTrue(allowed(origin="http://aify.local", sec_fetch_site="same-origin"))

    def test_an_origin_the_operator_NAMED_is_allowed(self):
        self.assertTrue(allowed(origin="https://named.example", sec_fetch_site="cross-site"))
        self.assertTrue(allowed(origin="https://named.example/", sec_fetch_site="cross-site"),
                        "a trailing slash must not decide an operator's configuration")

    def test_a_wildcard_in_cors_origins_grants_NOTHING(self):
        """A wildcard is the absence of a decision about who may drive this from a browser, not a
        decision to trust every page -- and reading it as 'everyone' makes the guard a no-op in
        exactly the default configuration it protects."""
        self.assertFalse(browser_request_is_allowed(
            origin="https://evil.example", host=HOST, allowed_origins=["*"]))

    def test_an_unparseable_origin_is_refused_rather_than_treated_as_ours(self):
        for junk in ("://", "not a url", "null"):
            with self.subTest(origin=junk):
                self.assertFalse(allowed(origin=junk, sec_fetch_site="same-origin"))

    def test_a_host_that_merely_ENDS_WITH_ours_is_not_ours(self):
        """`evil-aify.local` and `aify.local.evil.example` both contain the name. Comparison is on
        the parsed hostname, so neither is a match."""
        for origin in ("http://evil-aify.local", "http://aify.local.evil.example"):
            with self.subTest(origin=origin):
                self.assertFalse(allowed(origin=origin, sec_fetch_site="same-site"))

    def test_an_IPv6_host_is_matched_rather_than_split_on_a_colon(self):
        """Splitting `[::1]` on the last colon yields `":"`, which matches nothing -- so a real
        same-origin request would be refused. `urlsplit` knows the brackets."""
        self.assertTrue(browser_request_is_allowed(
            origin="http://[::1]:8801", host="[::1]:8800", allowed_origins=[]))


class WithNoOriginFetchMetadataIsAllThereIsTests(unittest.TestCase):
    def test_cross_site_is_refused_with_no_origin_at_all(self):
        self.assertFalse(allowed(sec_fetch_site="cross-site"))

    def test_same_site_is_refused_for_an_UNSAFE_method(self):
        """THE SECOND HOLE. Same-site is the registrable domain, not the host, so a sibling
        subdomain qualifies -- and it was passing wholesale."""
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertFalse(allowed(sec_fetch_site="same-site", method=method))

    def test_same_site_NAVIGATION_to_the_NON_MUTATING_surface_still_works(self):
        """Clicking through from Dashboard Next to the classic UI must keep working. It is scoped to
        a positive top-level navigation AND to paths that do not mutate -- not to "any safe method",
        which was wrong here: `GET /messages/inbox/{agent}` settles read receipts and completes
        stranded dispatch runs."""
        for path in ("/", "/health", "/api/v1/dashboard"):
            with self.subTest(path=path):
                self.assertTrue(allowed(
                    sec_fetch_site="same-site", method="GET",
                    sec_fetch_dest="document", path=path))

    def test_a_same_site_request_that_is_NOT_a_navigation_is_refused(self):
        """A page-initiated fetch is not a navigation, whatever its method."""
        self.assertFalse(allowed(
            sec_fetch_site="same-site", method="GET", sec_fetch_dest="empty", path="/"))

    def test_a_same_site_navigation_to_a_MUTATING_path_is_refused(self):
        """The hole this closes. A sibling subdomain could navigate to an API GET that writes."""
        self.assertFalse(allowed(
            sec_fetch_site="same-site", method="GET", sec_fetch_dest="document",
            path="/api/v1/messages/inbox/somebody"))

    def test_same_origin_and_none_are_allowed(self):
        self.assertTrue(allowed(sec_fetch_site="same-origin", method="POST"))
        self.assertTrue(allowed(sec_fetch_site="none", method="GET"))

    def test_a_PROGRAM_sending_neither_header_is_allowed(self):
        """A browser cannot omit both. Refusing on absence would refuse every bridge, every CLI and
        every curl this service exists to serve, and protect nobody."""
        self.assertTrue(allowed(method="POST"))


class BothDoorsAskTheSamePolicyTests(unittest.TestCase):
    """The call sites, because a predicate proven in isolation leaves the call to it unproven.

    DRIVEN THROUGH `create_app()`, not through a hand-built app. A test that assembles its own
    FastAPI stays green with the middleware removed from the real one -- which is a defence that
    cannot fire, passing its own test. `test_a_page_on_another_site_cannot_drive_this_service.py`
    already learned that and carries the same helper.
    """

    @staticmethod
    def _real_app(api_key: str = ""):
        import dataclasses
        from unittest import mock
        from service.config import get_config
        from service.main import create_app
        patched = dataclasses.replace(get_config(), api_key=api_key)
        with mock.patch("service.main.get_config", return_value=patched):
            return create_app()

    def _client(self):
        from fastapi.testclient import TestClient
        return TestClient(self._real_app())

    #: The refusal this guard produces, so "was it refused" can be asked without asking "does the
    #: database work". These tests are about the browser boundary; a 500 from an unrelated cause
    #: still proves the request got PAST the guard, which is the whole claim. Asserting 200 made
    #: them depend on `create_app()` finding a healthy database -- and in a full-suite run an
    #: earlier test leaves `service.db._db_path` pointing at a temp file it has already deleted, so
    #: they failed on database health while the boundary was behaving correctly.
    REFUSAL = "Cross-site browser requests are refused"

    def assertReachedTheApp(self, response):
        self.assertNotEqual(response.status_code, 403, response.text)
        self.assertNotIn(self.REFUSAL, response.text)

    def test_the_HTTP_door_refuses_a_hostile_origin_with_no_fetch_metadata(self):
        """THE FIRST HOLE, at the door rather than in the predicate."""
        response = self._client().get("/api/v1/agents", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_the_HTTP_door_refuses_an_unsafe_SAME_SITE_request(self):
        """THE SECOND HOLE. A sibling subdomain is same-site, and it was passing wholesale."""
        response = self._client().post(
            "/api/v1/agents", json={"agentId": "policy-probe", "role": "coder"},
            headers={"Sec-Fetch-Site": "same-site"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_the_HTTP_door_still_serves_a_program(self):
        """A bridge, a CLI, a curl. Refusing these would protect nobody and break everything."""
        self.assertReachedTheApp(self._client().get("/api/v1/agents"))

    def test_a_same_site_NAVIGATION_to_the_DASHBOARD_is_still_served(self):
        """The operator's own link from one dashboard to the other must keep working -- but only to
        the surface that does not mutate."""
        # `/health` rather than the dashboard itself: the dashboard route REDIRECTS, and a redirect
        # chase is not what this test is about. Both are in the navigable set; this one answers.
        self.assertReachedTheApp(self._client().get(
            "/health", headers={"Sec-Fetch-Site": "same-site", "Sec-Fetch-Dest": "document"}))

    def test_a_same_site_GET_to_the_API_is_REFUSED_even_though_it_is_a_GET(self):
        """"GET is safe" is the general rule and it is FALSE here. `GET /messages/inbox/{agent}`
        settles read receipts, completes dispatch runs stranded by a dead bridge, and refreshes
        agent status. A blanket same-site GET allowance let a sibling subdomain mutate through the
        arm called safe."""
        response = self._client().get(
            "/api/v1/messages/inbox/somebody",
            headers={"Sec-Fetch-Site": "same-site", "Sec-Fetch-Dest": "empty"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_the_WEBSOCKET_door_asks_the_same_policy(self):
        """It already compared by host; the point is that it now shares the DECISION rather than
        carrying a second copy that can drift from the HTTP one."""
        from service import main as service_main

        self.assertFalse(service_main.websocket_origin_is_allowed(
            "https://evil.example", HOST, ALLOWED, TRUSTED))
        self.assertTrue(service_main.websocket_origin_is_allowed(
            "http://aify.local:8801", HOST, ALLOWED, TRUSTED))
        self.assertTrue(service_main.websocket_origin_is_allowed("", HOST, ALLOWED, TRUSTED),
                        "a program opening the stream must still be served")
        # ...and the rebinding case reaches the WebSocket door too.
        self.assertFalse(service_main.websocket_origin_is_allowed(
            "http://aify.local:8801", HOST, ALLOWED, []),
            "an untrusted Host must not be vouched for by an Origin that matches it")


class AOneTimeQueryCredentialMustNotSTAYInTheURLTests(unittest.TestCase):
    """`?api_key=` is exchanged for a cookie — and then the key is still in the address bar.

    THE EXPOSURE. The documented way to open the dashboard once a key is set is to visit
    `http://host:8800/?api_key=<the value>`. That works, and the key then lives in browser history,
    in the address bar over the operator's shoulder, in any bookmark made from that page, and in the
    `Referer` of every outbound link. None of it is needed: the cookie is already set by the time the
    response is written.

    A REDIRECT IS THE FIX, and it must be scoped to a BROWSER NAVIGATION rather than applied to
    everything. A program passing `?api_key=` is a supported caller and a 303 would change its
    contract; `Sec-Fetch-Dest: document` is set by browsers on a top-level navigation and by no
    program, so it distinguishes the case positively rather than by guessing from absence.
    """

    @staticmethod
    def _app(api_key: str):
        import dataclasses
        from unittest import mock
        from service.config import get_config
        from service.main import create_app
        patched = dataclasses.replace(get_config(), api_key=api_key)
        with mock.patch("service.main.get_config", return_value=patched):
            return create_app()

    def _client(self, api_key="sk-one-time-probe"):
        from fastapi.testclient import TestClient
        return TestClient(self._app(api_key), follow_redirects=False)

    def test_a_browser_navigation_is_redirected_to_the_same_url_WITHOUT_the_key(self):
        response = self._client().get(
            "/?api_key=sk-one-time-probe",
            headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
        )
        self.assertIn(response.status_code, (302, 303), response.text)
        location = response.headers.get("location", "")
        self.assertNotIn("api_key", location,
                         "the redirect carried the credential straight back into the address bar")
        self.assertTrue(location.startswith("/"), f"unexpected redirect target: {location!r}")

    def test_the_cookie_is_set_ON_that_redirect_or_the_operator_is_logged_out_again(self):
        """The whole point is that the browser keeps working after the key leaves the URL."""
        response = self._client().get(
            "/?api_key=sk-one-time-probe",
            headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
        )
        self.assertIn("aify_api_key", response.headers.get("set-cookie", ""))

    def test_other_query_parameters_SURVIVE_the_redirect(self):
        """Stripping the whole query would silently drop wherever the operator was going."""
        response = self._client().get(
            "/?api_key=sk-one-time-probe&page=runs&filter=failed",
            headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
        )
        location = response.headers.get("location", "")
        self.assertIn("page=runs", location)
        self.assertIn("filter=failed", location)
        self.assertNotIn("api_key", location)

    def test_a_PROGRAM_passing_the_key_in_the_query_is_NOT_redirected(self):
        """Supported caller, unchanged contract. A 303 here would break it for no security gain:
        a program's URL is not in anyone's history."""
        response = self._client().get("/api/v1/agents?api_key=sk-one-time-probe")
        self.assertNotIn(response.status_code, (302, 303),
                         "a program's query key was turned into a redirect, changing its contract")
        self.assertNotEqual(response.status_code, 401,
                            "the key in the query was not accepted for a program")

    def test_a_WRONG_key_is_still_refused_rather_than_redirected(self):
        """The redirect must sit AFTER the check. Redirecting first would turn a rejected
        credential into a 303 that looks like success."""
        response = self._client().get(
            "/?api_key=wrong",
            headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "none"},
        )
        self.assertEqual(response.status_code, 401, response.text)


class BothDoorsGetTheSAMEConfigTests(unittest.TestCase):
    """A shared policy is only shared if both call sites hand it the same inputs.

    THE GAP THIS PINS, which I shipped once in this very change: the HTTP middleware was given
    `config.trusted_hosts` and the WebSocket call site was not, so it fell back to loopback-only.
    An operator who named a LAN host would have found the dashboard working and the live console
    refusing, with nothing saying why. One policy, two callers, one of them under-informed -- the
    same shape as the two guards this change exists to merge.
    """

    def test_the_websocket_call_site_passes_the_configured_trusted_hosts(self):
        """SCOPED TO THE CALL, not to the file. My first version grepped all of `create_app` for
        `config.trusted_hosts` -- which the MIDDLEWARE line also contains, so deleting it from the
        websocket call left the test green. A mutation caught that; the assertion now reads the
        websocket call's own arguments."""
        import inspect
        from service import main as service_main

        lines = inspect.getsource(service_main.create_app).splitlines()
        at = next((i for i, line in enumerate(lines) if "websocket_origin_is_allowed(" in line), -1)
        self.assertGreaterEqual(at, 0, "no websocket_origin_is_allowed call found in create_app")
        # The call spans a few lines; take them. A REGEX WAS WRONG HERE: `\((.*?)\)` is non-greedy
        # and stopped at the first `)`, which belongs to `ws.headers.get("origin", "")` -- so it
        # captured a fragment and failed on correct code.
        call = " ".join(lines[at:at + 4])

        self.assertIn("config.trusted_hosts", call,
                      "the websocket door does not receive the operator's trusted hosts, so a "
                      "named LAN host would work over HTTP and be refused on the live stream")
        # POSITIVE CONTROL: the same slice finds the sibling argument, so a miss above is a real
        # absence rather than a window that landed on the wrong lines.
        self.assertIn("config.cors_origins", call)

    def test_the_http_middleware_reads_the_field_DIRECTLY(self):
        """`getattr(config, 'trusted_hosts', [])` hid the read from `test_every_config_knob_does
        _something`, which exists precisely to catch a declared field nothing consumes. The default
        was pointless too -- the field is declared, so it is always present."""
        import inspect
        from service import main as service_main

        source = inspect.getsource(service_main.create_app)
        self.assertIn("trusted_hosts=config.trusted_hosts", source)
        self.assertNotIn("getattr(config, 'trusted_hosts'", source)
