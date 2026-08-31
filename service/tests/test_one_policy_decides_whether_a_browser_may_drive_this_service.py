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

from service.api_core.browser_origin import (
    SAFE_METHODS, browser_request_is_allowed, path_is_navigable,
)

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
        """Every bridge, CLI and curl this service exists to serve sends neither header, and they
        keep working. NOT because absence is read as "not a browser" -- it was, and that was the
        residual: a browser predating Fetch Metadata omits both too. They pass because they reach
        the service on a Host it trusts, which is checked here whatever the headers say."""
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
        # `testserver` is the Host TestClient sends, and a browser-identified request must arrive
        # on a TRUSTED host now -- otherwise the rebinding fix is walk-aroundable by omitting
        # `Origin`. In a real deployment this is loopback; here it is TestClient's stand-in.
        patched = dataclasses.replace(
            get_config(), api_key=api_key, trusted_hosts=["testserver"])
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
        # `testserver` is the Host TestClient sends, and a browser-identified request must arrive
        # on a TRUSTED host now -- otherwise the rebinding fix is walk-aroundable by omitting
        # `Origin`. In a real deployment this is loopback; here it is TestClient's stand-in.
        patched = dataclasses.replace(
            get_config(), api_key=api_key, trusted_hosts=["testserver"])
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

        self.assertIn("trusted_hosts", call,
                      "the websocket door does not receive the resolved trusted hosts, so a "
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
        self.assertIn("effective_trusted_hosts(config.trusted_hosts, config.https_sites)", source)
        self.assertNotIn("getattr(config, 'trusted_hosts'", source)
        self.assertNotIn("getattr(config, 'https_sites'", source)


class RebindingReachesTheNoOriginArmTooTests(unittest.TestCase):
    """The trusted-Host check guarded the Origin branch and NOTHING ELSE.

    EXECUTED BY REVIEW, not reasoned: `GET /api/v1/messages/inbox/x` with
    `Sec-Fetch-Site: same-origin`, no Origin, and `Host: evil.example` was ALLOWED. Under DNS
    rebinding the browser genuinely regards the rebound attacker name as same-origin -- it IS the
    origin, once the name resolves here -- and a GET may omit `Origin` entirely. So the whole
    rebinding fix could be walked around by simply not sending the header it keyed on. And the GET
    it reaches settles read receipts and completes stranded dispatch runs.

    The rule is now: ANY browser-identified request (Fetch Metadata present at all) must arrive on a
    trusted Host before any same-origin or same-site shortcut applies. A program sending neither
    header is untouched, which is every bridge, CLI and curl.
    """

    def test_a_no_origin_same_origin_GET_on_an_UNTRUSTED_host_is_refused(self):
        self.assertFalse(browser_request_is_allowed(
            method="GET", path="/api/v1/messages/inbox/x", sec_fetch_site="same-origin",
            origin="", host="evil.example", allowed_origins=[], trusted_hosts=["aify.local"]))

    def test_the_same_request_on_a_TRUSTED_host_is_allowed(self):
        """The control. Without it the rule above could be satisfied by refusing everything."""
        self.assertTrue(browser_request_is_allowed(
            method="GET", path="/api/v1/messages/inbox/x", sec_fetch_site="same-origin",
            origin="", host="aify.local:8800", allowed_origins=[], trusted_hosts=["aify.local"]))

    def test_a_browser_identified_by_DEST_ALONE_is_also_host_checked(self):
        """`Sec-Fetch-Dest` identifies a browser just as well; keying only on `Sec-Fetch-Site` would
        leave the same walk-around one header along."""
        self.assertFalse(browser_request_is_allowed(
            method="GET", path="/", sec_fetch_site="", sec_fetch_dest="document",
            origin="", host="evil.example", allowed_origins=[], trusted_hosts=["aify.local"]))

    def test_NO_HEADERS_AT_ALL_IS_STILL_HOST_CHECKED(self):
        """This test used to assert the opposite, on the reasoning that "both headers absent is not a
        browser". Executed by review: browsers that predate Fetch Metadata send none of it, and a
        same-origin GET carries no `Origin`, so a rebound page in such a browser was classified as a
        program and skipped the Host check entirely -- against the mutating GET routes. The check is
        unconditional now, and does not rest on guessing what kind of client sent the request."""
        self.assertFalse(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="", sec_fetch_dest="",
            origin="", host="evil.example", allowed_origins=[], trusted_hosts=["aify.local"]))

    def test_AND_THE_HEADER_LESS_CLIENTS_THAT_MATTER_ARE_UNTOUCHED(self):
        """The cost of making it unconditional, paid where it would actually land. A bridge, a CLI or
        `curl` reaches this service on loopback or on an address, and REBINDING NEEDS A NAME -- there
        is no DNS answer to poison when the client typed an IP, and page script cannot set `Host`.
        So every one of these is served with nothing configured."""
        for host in ("127.0.0.1:8800", "localhost:8800", "[::1]:8800",
                     "192.168.1.50:8800", "10.0.0.7:8800"):
            with self.subTest(host=host):
                self.assertTrue(browser_request_is_allowed(
                    method="POST", path="/api/v1/agents", sec_fetch_site="", sec_fetch_dest="",
                    origin="", host=host, allowed_origins=[], trusted_hosts=[]))

    def test_a_NAMED_host_is_served_once_the_operator_declares_it(self):
        """The one case that needs configuration, and the escape hatch for it. A program reaching the
        service by a hostname -- through the HTTPS proxy, or by a container name -- is refused until
        the operator says that name is this service, via `TRUSTED_HOSTS` or `HTTPS_SITES`."""
        self.assertFalse(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="", sec_fetch_dest="",
            origin="", host="stevenz-l:8443", allowed_origins=[], trusted_hosts=[]))
        self.assertTrue(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="", sec_fetch_dest="",
            origin="", host="stevenz-l:8443", allowed_origins=[], trusted_hosts=["stevenz-l"]))


class ANavigablePathMustNotBEAPREFIXTests(unittest.TestCase):
    """`startswith` matched far more than the routes it named.

    EXECUTED BY REVIEW: `/health-evil`, `/docsanything` and `/api/v1/dashboard-evil` were all
    navigable, so a same-site navigation reached any route whose path merely BEGAN with a safe one.
    """

    def test_a_path_that_merely_STARTS_WITH_a_navigable_one_is_not_navigable(self):
        for path in ("/health-evil", "/docsanything", "/api/v1/dashboard-evil"):
            with self.subTest(path=path):
                self.assertFalse(path_is_navigable(path))

    def test_the_real_navigable_paths_and_their_children_still_are(self):
        """The control: an exact match and a genuine sub-path both remain reachable."""
        for path in ("/", "/health", "/api/v1/dashboard", "/api/v1/dashboard/index.html"):
            with self.subTest(path=path):
                self.assertTrue(path_is_navigable(path))


class TheTrustedHostListIsDERIVEDFromWhatTheOperatorAlreadyDeclaredTests(unittest.TestCase):
    """`HTTPS_SITES` is the same value Caddy is given, and `config/Caddyfile` says every name you
    intend to reach it by must be listed. That makes it the existing answer to the question this
    guard asks, so it is derived rather than asked for a second time under another name.

    THE COST THIS AVOIDS IS CONCRETE. The operator's own Caddy serves `stevenz-l:8443` and
    `stevenz-l.local:8443`. With the Host check unconditional and nothing derived, browser access by
    either name would be refused after a rebuild, and the cause -- a security fix landing three
    commits earlier -- would not be visible from the symptom.
    """

    def test_the_names_caddy_is_served_as_become_trusted_hosts(self):
        from service.api_core.browser_origin import effective_trusted_hosts
        resolved = effective_trusted_hosts(
            [], "localhost:8443, 127.0.0.1:8443, stevenz-l:8443, stevenz-l.local:8443")
        self.assertEqual(resolved, ["localhost", "127.0.0.1", "stevenz-l", "stevenz-l.local"])

    def test_a_request_on_such_a_name_is_then_served(self):
        """The union reaching the DECISION, not just being computed. A list built correctly and
        handed to nobody is the shape this file already caught once."""
        from service.api_core.browser_origin import effective_trusted_hosts
        resolved = effective_trusted_hosts([], "stevenz-l:8443")
        self.assertTrue(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="same-origin",
            origin="https://stevenz-l:8443", host="stevenz-l:8443",
            allowed_origins=[], trusted_hosts=resolved))
        # NEGATIVE CONTROL: a name NOT in that declaration is still refused, so the allowance
        # tracks the operator's list rather than the presence of any list at all.
        self.assertFalse(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="same-origin",
            origin="https://evil.example", host="evil.example",
            allowed_origins=[], trusted_hosts=resolved))

    def test_an_operators_explicit_list_and_the_derived_one_are_UNIONED_not_replaced(self):
        from service.api_core.browser_origin import effective_trusted_hosts
        self.assertEqual(
            effective_trusted_hosts(["box.lan"], "stevenz-l:8443"), ["box.lan", "stevenz-l"])
        # A name in both places appears once, so listing it twice is not a way to change behaviour.
        self.assertEqual(
            effective_trusted_hosts(["stevenz-l"], "stevenz-l:8443"), ["stevenz-l"])

    def test_an_unset_HTTPS_SITES_adds_nothing_rather_than_an_empty_name(self):
        """An empty entry in the list would match a request carrying no Host at all."""
        from service.api_core.browser_origin import effective_trusted_hosts
        for value in ("", None, "   ", ",,"):
            with self.subTest(value=value):
                self.assertEqual(effective_trusted_hosts([], value), [])
        self.assertFalse(browser_request_is_allowed(
            method="POST", path="/api/v1/agents", sec_fetch_site="same-origin",
            origin="", host="", allowed_origins=[],
            trusted_hosts=effective_trusted_hosts([], "")))
