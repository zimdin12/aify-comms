"""`dashboard_url` — where the "Dashboard" link on the API service actually sends an operator.

The service (8800) and Dashboard Next (8801) are two processes. Every legacy dashboard entry point
on the API side is a redirect, and this function is the whole of the decision.

Its failure mode is a redirect to a host that does not answer, and the operator's only diagnosis is
a browser error page — from a URL they never typed, on a port they may not know is separate.

TWO SOURCES, IN ORDER. `AIFY_DASHBOARD_URL` is authoritative and exists for reverse proxies, where
nothing about the incoming request can be used to guess the public address. With it unset the URL is
DERIVED from the request: same scheme, same hostname, dashboard port. Deriving is what makes the
link work from another machine on the LAN — a hardcoded `localhost` would send every remote operator
to their own laptop, which is the kind of wrong that looks like the service being down.

The two ROUTE-level tests below came first and are unchanged: they drive the real redirects through
`create_app()` and pin the exact status and Location. Added underneath is the function itself, which
had no direct test — the branches the two routes never take are the reverse-proxy shapes and the
IPv6 rule, and those are where the awkward cases live.

BOTH ENVIRONMENT VARIABLES ARE SEALED in the unit tests. They are read at call time, and this suite
runs on the same machine as a live deployment.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

#: A REALISTIC HOST. `TestClient` defaults to `http://testserver`, and the browser guard now
#: requires every request to arrive on a Host this service trusts -- loopback, a literal IP, or
#: a name the operator declared. `testserver` is none of those, and no real client sends it.
#: Pointing the client at loopback is what a bridge, a CLI or `curl` actually does.
LOOPBACK = "http://127.0.0.1:8800"

from fastapi.testclient import TestClient
from starlette.requests import Request

import service.main as main_module
from service.config import ServiceConfig
from service.dashboard_redirect import dashboard_url

ENV_VARS = ("AIFY_DASHBOARD_URL", "AIFY_DASHBOARD_PORT")


def request_from(host: str, scheme: str = "http") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": scheme,
        "server": ("unused", 80),
        "path": "/dashboard",
        "query_string": b"",
        "headers": [(b"host", host.encode())],
    })


class DashboardRedirectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = ServiceConfig(
            data_dir=self.tmp.name,
            config_dir=self.tmp.name,
            api_key="",
            # NO `host=`. `ServiceConfig` no longer declares one: it was settable through `HOST` and
            # through `service.json` and read by nothing, while the container's CMD hardcodes
            # `--host 0.0.0.0`. This test never depended on it -- the redirect it proves is built
            # from the REQUEST's Host header and `AIFY_DASHBOARD_URL`, which is why passing a value
            # here changed nothing and made the field look alive.
            port=8800,
            mcp_enabled=False,
            # DECLARED, because the browser guard now requires every request to arrive on a Host
            # this service trusts. `aify.internal` is a NAME, and a name is exactly what a DNS
            # rebind supplies -- so it is refused until an operator says it is this service. The
            # test below drives the redirect on that name, which is what a real named deployment
            # looks like once `TRUSTED_HOSTS` or `HTTPS_SITES` declares it.
            trusted_hosts=["aify.internal"],
        )
        self.original_get_config = main_module.get_config
        self.addCleanup(setattr, main_module, "get_config", self.original_get_config)
        main_module.get_config = lambda: cfg
        self.client = TestClient(main_module.create_app(), base_url=LOOPBACK)

    def test_legacy_dashboard_entry_points_redirect_to_new_dashboard(self):
        with patch.dict(os.environ, {"AIFY_DASHBOARD_URL": "http://dashboard.example:8801/"}):
            for path in ("/", "/api/v1/dashboard", "/api/v1/dashboard/dispatches"):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 307, path)
                self.assertEqual(response.headers["location"], "http://dashboard.example:8801/", path)

    def test_redirect_defaults_to_request_host_on_new_dashboard_port(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AIFY_DASHBOARD_URL", None)
            response = self.client.get(
                "/api/v1/dashboard",
                headers={"host": "aify.internal:8800"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "http://aify.internal:8801/")


class DashboardUrlTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.pop(name, None) for name in ENV_VARS}
        for name in ENV_VARS:
            self.assertNotIn(name, os.environ, "the environment seal did not take")

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def setenv(self, name: str, value: str) -> None:
        os.environ[name] = value


class ConfiguredUrlTests(DashboardUrlTestCase):
    """`AIFY_DASHBOARD_URL` — the reverse-proxy case, where the request tells you nothing."""

    def test_a_configured_url_ends_in_exactly_ONE_trailing_slash(self):
        """Browsers cope, but the value is also pasted into docs and compared in tests. One shape.
        `rstrip("/")` removes all of them, not just the last -- worth pinning, because the obvious
        alternative (`removesuffix`) would leave `https://host//`."""
        for configured in ("https://comms.example.test", "https://comms.example.test///"):
            with self.subTest(configured=configured):
                self.setenv("AIFY_DASHBOARD_URL", configured)
                self.assertEqual(dashboard_url(request_from("h:8800")), "https://comms.example.test/")

    def test_a_configured_path_prefix_is_preserved(self):
        """A proxy that mounts the dashboard under a sub-path. Dropping it would redirect to the
        proxy's root, which is usually something else entirely."""
        self.setenv("AIFY_DASHBOARD_URL", "https://ops.example.test/aify/dashboard")
        self.assertEqual(
            dashboard_url(request_from("h:8800")), "https://ops.example.test/aify/dashboard/",
        )

    def test_an_EMPTY_or_WHITESPACE_value_falls_back_to_deriving(self):
        """Unset and set-to-empty must behave the same. A deployment that clears the variable to
        turn the override OFF would otherwise redirect to `/`. A `.env` line with a trailing space
        is the ordinary way this ends up whitespace-only."""
        for configured in ("", "   "):
            with self.subTest(configured=repr(configured)):
                self.setenv("AIFY_DASHBOARD_URL", configured)
                self.assertEqual(
                    dashboard_url(request_from("box.local:8800")), "http://box.local:8801/")


class DerivedUrlTests(DashboardUrlTestCase):
    """No override: same scheme, same host, dashboard port. The hostname coming from the REQUEST
    (not localhost), an ordinary hostname staying unbracketed and the path being the dashboard ROOT
    are pinned end to end by `test_redirect_defaults_to_request_host_on_new_dashboard_port`."""

    def test_the_SCHEME_is_preserved(self):
        """An https page redirecting to http is a mixed-content block in every current browser —
        the operator sees nothing happen at all."""
        self.assertEqual(
            dashboard_url(request_from("comms.example.test:8800", scheme="https")),
            "https://comms.example.test:8801/",
        )

    def test_a_request_with_no_port_still_gets_the_dashboard_port(self):
        self.assertEqual(dashboard_url(request_from("box.local")), "http://box.local:8801/")

    def test_the_dashboard_port_is_CONFIGURABLE(self):
        self.setenv("AIFY_DASHBOARD_PORT", "9001")
        self.assertEqual(dashboard_url(request_from("box.local:8800")), "http://box.local:9001/")

    def test_an_EMPTY_dashboard_port_falls_back_to_8801(self):
        """`or "8801"` after the strip. Without it the URL would end in a bare colon, which every
        browser rejects — a broken link rather than a wrong one."""
        self.setenv("AIFY_DASHBOARD_PORT", "   ")
        self.assertEqual(dashboard_url(request_from("box.local:8800")), "http://box.local:8801/")



class IPv6Tests(DashboardUrlTestCase):
    """The bracket rule, which exists because `url.hostname` removes them."""

    def test_an_IPv6_host_is_RE_BRACKETED(self):
        """Starlette hands back `::1` for a request to `[::1]:8800`. Reassembled without brackets
        the result is `http://::1:8801/`, which is not a URL a browser will follow -- the port is
        unparseable against the address. The exact comparison also rules out DOUBLE brackets.

        The `not host.startswith("[")` guard is NOT reachable from here: `request.url.hostname`
        strips brackets before the function ever sees the host, so a mutation that deletes that
        condition survives, and that is the accurate result rather than a gap to paper over. The
        guard is kept and documented in the source as defensive."""
        for authority, expected in (("[::1]:8800", "http://[::1]:8801/"),
                                    ("[2001:db8::42]:8800", "http://[2001:db8::42]:8801/")):
            with self.subTest(authority=authority):
                self.assertEqual(dashboard_url(request_from(authority)), expected)


if __name__ == "__main__":
    unittest.main()
