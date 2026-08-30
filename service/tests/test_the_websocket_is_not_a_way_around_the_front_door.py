r"""Both middlewares are blind to WebSockets, and WebSockets are not subject to CORS.

TWO FACTS THAT ONLY MATTER TOGETHER, and each was checked rather than assumed:

  * `BaseHTTPMiddleware.__call__` opens with `if scope["type"] != "http": await self.app(...); return`
    -- read out of Starlette's own source. So neither `APIKeyMiddleware` nor the cross-site guard ever
    sees a WebSocket, and `/ws` is on the key middleware's skip list anyway.
  * The same-origin policy does not cover WebSocket handshakes. A page on any site can open
    `ws://localhost:8800/ws` with no preflight and no CORS involvement whatsoever.

So with no key configured -- the shipped default, measured against the running container -- a page the
operator visits could open this stream and read whatever the service pushes.

HONESTLY BOUNDED. The handler is `await ws.receive_text()  # Keep alive, ignore client messages`, so a
page cannot COMMAND anything through it. This is information disclosure, not control, which makes it a
smaller hole than the HTTP one and not a non-hole.

THE HANDSHAKE CARRIES `Origin`, attached by the browser and unremovable by page script, exactly as on
a cross-origin HTTP request. One difference from the HTTP guard: the dashboard is itself a browser
client, so `Origin` being PRESENT cannot be the refusal. The comparison is by HOST, which lets any port
on the same host through -- Dashboard Next answers on :8801 -- and refuses another site.
"""

from __future__ import annotations

import unittest

from service.main import websocket_origin_is_allowed as allowed


class TheWebSocketChecksItsOriginTests(unittest.TestCase):
    def test_a_page_on_another_site_is_refused(self):
        self.assertFalse(allowed("https://evil.example", "localhost:8800", ["*"]))
        self.assertFalse(allowed("http://evil.example:8800", "localhost:8800", []))

    def test_a_program_sending_no_Origin_is_allowed(self):
        """The control, and the one that matters most: bridges, CLIs and tests are what this endpoint
        exists to serve, and none of them sends an Origin. A browser cannot omit it."""
        self.assertTrue(allowed("", "localhost:8800", []))
        self.assertTrue(allowed(None, "localhost:8800", []))
        self.assertTrue(allowed("   ", "localhost:8800", []))

    def test_the_dashboard_on_this_very_origin_is_allowed(self):
        self.assertTrue(allowed("http://localhost:8800", "localhost:8800", []))

    def test_a_second_dashboard_on_ANOTHER_PORT_is_allowed(self):
        """Dashboard Next answers on :8801. Ports do not make a different site, so a port difference
        must not refuse it."""
        self.assertTrue(allowed("http://localhost:8801", "localhost:8800", []))

    def test_a_LAN_HOST_MUST_BE_NAMED_now_and_this_is_a_real_change(self):
        """THE SENTENCE THAT USED TO STAND HERE WAS WRONG. It said a port difference could be
        ignored because "an attacker cannot serve from the operator's own hostname" -- which is
        exactly what DNS REBINDING defeats. Under a rebind the attacker's page is served from
        `evil.example`, that name re-resolves to this service, and the browser sends
        `Origin: http://evil.example` WITH `Host: evil.example`. They agree perfectly. Both values
        come from the client, so their agreement was never evidence of anything.

        The same-host shortcut now applies only on a host we independently trust: loopback, plus
        whatever the operator names in `trusted_hosts`. A rebound name is neither.

        THIS HAS AN OPERATOR COST and it is deliberate: reaching the dashboard over a LAN name or
        address from a browser now requires that name in `trusted_hosts`. Loopback keeps working
        untouched, which is the default deployment.
        """
        # Not named: refused, however well Origin and Host agree with each other.
        self.assertFalse(allowed("http://192.168.1.10", "192.168.1.10:8800", []))
        self.assertFalse(allowed("http://evil.example", "evil.example:8800", []),
                         "a rebound host agreeing with itself is not a same-origin request")

        # Named by the operator: allowed, including a second dashboard on another port of it.
        self.assertTrue(allowed("http://192.168.1.10", "192.168.1.10:8800", [], ["192.168.1.10"]))
        self.assertTrue(allowed("http://stevenz-l:3000", "stevenz-l:8800", [], ["stevenz-l"]))
        # ...and a DIFFERENT host is still refused even when one is named.
        self.assertFalse(allowed("http://192.168.1.11", "192.168.1.10:8800", [], ["192.168.1.10"]))

    def test_an_origin_named_in_cors_origins_is_allowed(self):
        self.assertTrue(allowed("https://dash.example", "localhost:8800", ["https://dash.example"]))
        self.assertTrue(allowed("https://dash.example", "localhost:8800", ["https://Dash.Example/"]))

    def test_a_WILDCARD_grants_nothing(self):
        """Matching the HTTP guard: `*` is the default, and reading it as "every page may listen"
        would make this a no-op in exactly the configuration it exists to protect."""
        self.assertFalse(allowed("https://evil.example", "localhost:8800", ["*"]))

    def test_a_host_header_with_no_port_still_compares(self):
        self.assertTrue(allowed("http://localhost", "localhost", []))

    def test_an_IPv6_host_header_compares_by_its_address(self):
        # `Host: [::1]:8800` is what a browser sends for the IPv6 loopback, and the brackets are part
        # of the header rather than of the name.
        self.assertTrue(allowed("http://[::1]:8800", "[::1]:8800", []))
        # NO PORT AT ALL is the case that caught a real bug. Splitting the host on its last colon is
        # right for `localhost:8800` and wrong for `[::1]`, which has no port to strip -- the split
        # yielded `":"`, matching no origin, so a legitimate same-origin request was refused.
        self.assertTrue(allowed("http://[::1]", "[::1]", []))
        self.assertTrue(allowed("http://[::1]:9000", "[::1]:8800", []),
                        "another port on the same IPv6 host is the second dashboard")
        self.assertFalse(allowed("http://[::2]", "[::1]", []))

    def test_an_unparseable_origin_is_refused_rather_than_guessed(self):
        """Fails closed. A value that is not an origin cannot be shown to be same-host, and a guard
        that passes what it could not parse is decoration."""
        for junk in ("not-a-url", "://", "http://", "javascript:alert(1)"):
            with self.subTest(origin=junk):
                self.assertFalse(allowed(junk, "localhost:8800", []))


class TheENDPOINTActuallyChecksTests(unittest.TestCase):
    """Everything above tests the predicate. This is whether `/ws` calls it.

    A helper with green tests and no call site is a feature that cannot fire, and this repo has
    shipped exactly that before. Proven by mutation: disconnecting the call leaves every test above
    green and only these red.
    """

    @staticmethod
    def _client():
        import dataclasses
        from unittest import mock
        from fastapi.testclient import TestClient
        from service.config import get_config
        from service.main import create_app
        # `cors_origins` from the real config, so this exercises what an operator would actually have.
        patched = dataclasses.replace(get_config(), api_key="")
        with mock.patch("service.main.get_config", return_value=patched):
            app = create_app()

        # `ws_manager` is built in the lifespan, which is not run here on purpose: starting it would
        # spin up the reconcile loop and the ntfy relay for a test about a handshake header. The guard
        # runs BEFORE the manager is touched, so a stand-in is enough to tell "accepted" from
        # "refused" -- and the connection tracking is not what is under test.
        class _Manager:
            async def connect(self, ws, agent_id):
                await ws.accept()

            def disconnect(self, ws):
                pass

        app.state.ws_manager = _Manager()
        return TestClient(app)

    def test_a_page_on_another_site_cannot_open_the_stream(self):
        from starlette.websockets import WebSocketDisconnect
        client = self._client()
        with self.assertRaises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
                pass

    def test_a_program_sending_no_origin_still_connects(self):
        # The control: without it, "the guard works" and "the WebSocket is broken" look identical.
        client = self._client()
        with client.websocket_connect("/ws") as ws:
            self.assertIsNotNone(ws)
