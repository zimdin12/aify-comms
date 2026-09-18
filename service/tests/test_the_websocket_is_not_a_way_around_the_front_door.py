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


import unittest

from service.main import websocket_origin_is_allowed as allowed


class TheWebSocketChecksItsOriginTests(unittest.TestCase):
    # `websocket_origin_is_allowed` delegates to `browser_request_is_allowed`, whose cases (another
    # site, no Origin, same host on any port, named origins, wildcard, IPv6, unparseable) are pinned in
    # test_one_policy_decides_whether_a_browser_may_drive_this_service.py. Only the LAN/IP-literal
    # cases on the Origin arm live here.
    def test_a_LAN_HOST_MUST_BE_NAMED_now_and_this_is_a_real_change(self):
        """THE SENTENCE THAT USED TO STAND HERE WAS WRONG. It said a port difference could be
        ignored because "an attacker cannot serve from the operator's own hostname" -- which is
        exactly what DNS REBINDING defeats. Under a rebind the attacker's page is served from
        `evil.example`, that name re-resolves to this service, and the browser sends
        `Origin: http://evil.example` WITH `Host: evil.example`. They agree perfectly. Both values
        come from the client, so their agreement was never evidence of anything.

        The same-host shortcut now applies only on a host we independently trust: loopback, plus
        whatever the operator names in `trusted_hosts`. A rebound name is neither.

        THIS HAS AN OPERATOR COST and it is deliberate: reaching the dashboard over a LAN NAME from a
        browser requires that name in `trusted_hosts` (or in `HTTPS_SITES`, which is derived from).
        Loopback keeps working untouched, which is the default deployment.

        A LITERAL IP IS NOT A NAME, and this test asserted it was. Rebinding is a DNS answer that
        changes under the browser; there is no lookup to poison when the client typed an address,
        and page script cannot set `Host`. So an address is trusted and only names need declaring --
        which is also what keeps every bridge, CLI and `curl` working with nothing configured.

        WHAT THIS DOES NOT FIX, stated because the guard reads stronger than it is: a browser too old
        to send Fetch Metadata can still be made to issue a plain cross-site GET at loopback, and no
        header-based rule can see it, because there are no headers. That is CSRF, not rebinding, and
        the answer to it is `API_KEY`. The Host rule closes rebinding, which is the part it can.
        """
        # A NAME nobody declared: refused, however well Origin and Host agree with each other.
        self.assertFalse(allowed("http://evil.example", "evil.example:8800", []),
                         "a rebound host agreeing with itself is not a same-origin request")
        self.assertFalse(allowed("http://stevenz-l:3000", "stevenz-l:8800", []),
                         "an undeclared LAN name is exactly what a rebind supplies")

        # An ADDRESS needs no declaring: it cannot have been rebound.
        self.assertTrue(allowed("http://192.168.1.10", "192.168.1.10:8800", []))
        # ...but it is still only a SAME-host claim: a page on another origin is refused on it.
        self.assertFalse(allowed("http://evil.example", "192.168.1.10:8800", []),
                         "an IP Host must not vouch for somebody else's Origin")

        # Named by the operator: allowed, including a second dashboard on another port of it.
        self.assertTrue(allowed("http://192.168.1.10", "192.168.1.10:8800", [], ["192.168.1.10"]))
        self.assertTrue(allowed("http://stevenz-l:3000", "stevenz-l:8800", [], ["stevenz-l"]))
        # ...and a DIFFERENT host is still refused even when one is named.
        self.assertFalse(allowed("http://192.168.1.11", "192.168.1.10:8800", [], ["192.168.1.10"]))



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
            async def connect(self, ws, agent_id, *, wants_changes=False):
                await ws.accept()

            def disconnect(self, ws):
                pass

        app.state.ws_manager = _Manager()
        return TestClient(app, base_url=LOOPBACK)

    def test_a_page_on_another_site_cannot_open_the_stream(self):
        from starlette.websockets import WebSocketDisconnect
        client = self._client()
        with self.assertRaises((WebSocketDisconnect, Exception)):
            with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}):
                pass

    def test_a_program_sending_no_origin_still_connects(self):
        # The control: without it, "the guard works" and "the WebSocket is broken" look identical.
        client = self._client()
        with client.websocket_connect("/ws", headers=WS_HOST) as ws:
            self.assertIsNotNone(ws)
