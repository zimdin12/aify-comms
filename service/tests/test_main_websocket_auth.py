import tempfile
import unittest

#: A REALISTIC HOST. `TestClient` defaults to `http://testserver`, and the guard now requires every
#: request to arrive on a Host this service trusts -- loopback, a literal IP, or a name the
#: operator declared. `testserver` is none of those, and nothing real sends it; a bridge, a CLI
#: or `curl` reaches the service exactly like this.
LOOPBACK = "http://127.0.0.1:8800"
#: `TestClient.websocket_connect` sends `Host: testserver` REGARDLESS of `base_url` -- measured,
#: by spying on the guard: it received `host="testserver"` from a client built on
#: `http://127.0.0.1:8800`. So a websocket test states the Host itself, or it is testing the
#: refusal of a hostname nothing real sends.
WS_HOST = {"host": "127.0.0.1:8800"}


from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import service.main as main_module
from service.config import ServiceConfig


class WebsocketAuthTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_get_config = main_module.get_config
        config = ServiceConfig(
            data_dir=self._tmpdir.name,
            config_dir=self._tmpdir.name,
            api_key="secret",
            mcp_enabled=False,
            cors_origins=["*"],
        )
        main_module.get_config = lambda: config
        self.app = main_module.create_app()

    def tearDown(self):
        main_module.get_config = self._original_get_config
        self._tmpdir.cleanup()

    def test_websocket_requires_api_key(self):
        with TestClient(self.app, base_url=LOOPBACK) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws?agent_id=tester", headers=WS_HOST):
                    pass

    def test_websocket_accepts_valid_api_key_and_tracks_agent(self):
        with TestClient(self.app, base_url=LOOPBACK) as client:
            with client.websocket_connect("/ws?agent_id=tester&api_key=secret", headers=WS_HOST) as ws:
                ws.send_text("ping")
                self.assertIn("tester", client.app.state.ws_manager.online_agents())


class ABrowserCanAuthenticateTheSocketTests(WebsocketAuthTests):
    """A browser has exactly one credential here, and until 2026-09-02 the socket ignored it.

    THE TWO FORMS THE GUARD ALREADY ACCEPTED ARE BOTH UNREACHABLE FROM A PAGE. The WebSocket API
    takes no headers, so `X-API-Key` is for programs only; and the dashboard builds its URL as
    `new WebSocket(`${wsOrigin}/ws`)` (`realtime-socket.mjs:53`) with no query param. So setting
    `API_KEY` closed every dashboard handshake with 1008. The client backs off and retries
    (`realtime-socket.mjs:94`), which turns a permanent refusal into a dashboard that reports
    "reconnecting" for ever -- and reports it while the polls fail separately with 401, so the two
    halves of one cause look like two faults.

    THE COOKIE IS THE ONE CREDENTIAL THAT REACHES HERE, and the service already issues it: visiting
    `/?api_key=...` trades the key for an HttpOnly cookie precisely so a browser can authenticate
    at all. The middleware read it and the socket did not, which is the shape this repo keeps
    finding -- two readers of one credential, one of them never updated.
    """

    def _browser(self, client, value="secret"):
        """A page that has been through the query-param exchange, and nothing else.

        Deliberately NO header and NO query param: with either one present this test would pass on
        the old code and prove nothing about a browser.
        """
        client.cookies.set("aify_api_key", value)
        return client

    def test_a_page_holding_the_cookie_gets_its_socket(self):
        """THE DEFECT. This closed 1008 before the cookie was read."""
        with TestClient(self.app, base_url=LOOPBACK) as client:
            self._browser(client)
            with client.websocket_connect("/ws?agent_id=tester", headers=WS_HOST) as ws:
                ws.send_text("ping")
                self.assertIn("tester", client.app.state.ws_manager.online_agents())

    def test_a_wrong_cookie_is_still_refused(self):
        """NEGATIVE CONTROL. A guard that accepted the cookie's PRESENCE rather than its VALUE
        would pass the test above and authenticate anybody who can set a cookie."""
        with TestClient(self.app, base_url=LOOPBACK) as client:
            self._browser(client, value="not-the-key")
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws?agent_id=tester", headers=WS_HOST):
                    pass

    def test_the_cookie_name_is_the_one_the_middleware_writes(self):
        """The two sites are a matched pair: the middleware sets it, the socket reads it, and a
        divergence is invisible -- the page simply never connects, with no error naming a cookie.

        Asserted against the constant rather than trusted, and against the literal a browser
        actually holds, so renaming the constant alone cannot make this agree with itself.
        """
        self.assertEqual(main_module.APIKeyMiddleware.COOKIE, "aify_api_key")


class ACorsPreflightCarriesNoCredentialsTests(unittest.TestCase):
    """A preflight has no key BY SPECIFICATION, so demanding one refuses every cross-origin call.

    The browser strips credentials from a preflight -- no cookie, no `Authorization`, and certainly
    no `X-API-Key`, since the whole point of the request is to ASK whether that header may be sent.
    The key middleware saw a request with no credential and answered 401, before CORSMiddleware ever
    got to answer the question.

    WHAT THAT BREAKS IS INVISIBLE. A failed preflight does not surface as "401" anywhere a caller can
    see; the fetch rejects with a generic network error and the real request is never sent. So every
    cross-origin call needing a preflight -- any POST the dashboard makes to the service port -- fails
    with no cause attached. This service is served on one port and its dashboard on another, so that
    is the ordinary case here, not an edge one.

    LETTING IT PAST GRANTS NOTHING: a preflight returns policy headers and no body, and the request it
    authorises still arrives separately and still needs a valid key. The last test pins exactly that,
    because an exemption that also exempted the real request would be a hole rather than a fix.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._original_get_config = main_module.get_config
        config = ServiceConfig(
            data_dir=self._tmpdir.name,
            config_dir=self._tmpdir.name,
            api_key="secret",
            mcp_enabled=False,
            cors_origins=["*"],
        )
        main_module.get_config = lambda: config
        self.app = main_module.create_app()

    def tearDown(self):
        main_module.get_config = self._original_get_config
        self._tmpdir.cleanup()

    def _preflight(self, client):
        return client.options(
            "/api/v1/agents",
            headers={
                "Origin": "http://127.0.0.1:8801",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-api-key",
            },
        )

    def test_a_preflight_is_answered_rather_than_refused(self):
        """THE DEFECT: this was 401, so the request it authorises was never sent."""
        with TestClient(self.app, base_url=LOOPBACK) as client:
            response = self._preflight(client)
            self.assertNotEqual(
                response.status_code, 401,
                "the preflight was refused for having no credential, which it cannot have -- so every "
                "cross-origin call that needs one fails with no cause the caller can see",
            )
            self.assertLess(response.status_code, 400)

    def test_the_preflight_says_the_key_header_may_be_sent(self):
        """Answering is not enough; it has to answer YES to the header the dashboard needs."""
        with TestClient(self.app, base_url=LOOPBACK) as client:
            allowed = self._preflight(client).headers.get("access-control-allow-headers", "")
            self.assertIn("x-api-key", allowed.lower())

    def test_the_REAL_request_still_needs_the_key(self):
        """NEGATIVE CONTROL, and the reason the exemption is safe.

        An exemption that leaked into ordinary requests would turn the key off entirely -- and would
        pass the two tests above while doing it.
        """
        with TestClient(self.app, base_url=LOOPBACK) as client:
            self.assertEqual(client.get("/api/v1/agents").status_code, 401)
            keyed = client.get("/api/v1/agents", headers={"X-API-Key": "secret"})
            self.assertNotEqual(
                keyed.status_code, 401,
                "POSITIVE CONTROL: a valid key must still get in, or this test proves nothing",
            )
