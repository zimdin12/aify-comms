r"""A page the operator visits could drive the whole fleet, and the audit said so a year ago.

KNOWN_ISSUES, from the 2026-06-28 audit: "CORS `*` + DNS-rebinding means a malicious web page in the
operator's browser could drive it even on loopback". Every mutating endpoint was reachable that way,
including `POST /agents/{id}/console/input`, which types into a live PTY. Binding loopback does not
help -- the browser is already on the machine -- and `CORS *` means the page can read the replies too.

IT STAYED OPEN BECAUSE BOTH RECOMMENDED FIXES ARE OPERATOR DECISIONS. Requiring a key means
distributing one; binding loopback means giving up LAN access. Measured on the running container on
2026-08-30: `GET /api/v1/agents` returns 200 with no key AND with a wrong key, on a port published to
`0.0.0.0`. So the default deployment is the exposed one, and it is the one that needed protecting.

`Sec-Fetch-Site` costs nothing on either side. The BROWSER attaches it and page script cannot remove
it; no program sends it at all. So a cross-site page is refused while every bridge, CLI and `curl`
carries on, with no key, no configuration and no decision to make.

WHAT IS DELIBERATELY NOT REFUSED:

  * `same-origin` -- the classic dashboard, served from this very port.
  * `same-site` -- Dashboard Next on :8801. Ports do not make a different site, and refusing this
    would break the second dashboard while stopping nothing: an attacker cannot serve from localhost.
  * absent -- every program. Refusing on absence would refuse everything this service exists to serve,
    and a browser cannot omit the header.

`cors_origins` is honoured, because an operator who named an origin there has already decided that
origin may drive this service. `*` grants no exemption: a wildcard is the absence of a decision.

The policy itself -- every allowed and refused case above -- is pinned once, on the predicate and on
`create_app()`, in `test_one_policy_decides_whether_a_browser_may_drive_this_service.py`. What stays
here is what only the middleware decides: its refusal text, the headers it does NOT read, how it
normalises `cors_origins`, and that it runs outside the key check.
"""

from __future__ import annotations

#: A REALISTIC HOST. `TestClient` defaults to `http://testserver`, and the guard now requires every
#: request to arrive on a Host this service trusts -- loopback, a literal IP, or a name the
#: operator declared. `testserver` is none of those, and nothing real sends it; a bridge, a CLI
#: or `curl` reaches the service exactly like this.
LOOPBACK = "http://127.0.0.1:8800"

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from service.main import CrossSiteBrowserMiddleware

CROSS = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}


def _app(allowed_origins=None, trusted_hosts=None) -> FastAPI:
    app = FastAPI()
    # `testserver` is what TestClient sends as Host. It has to be TRUSTED for a same-host origin
    # claim to be believed, because Origin and Host both come from the client and agree perfectly
    # under DNS rebinding -- so the shortcut is gated on a host we independently accept.
    app.add_middleware(
        CrossSiteBrowserMiddleware, allowed_origins=allowed_origins,
        trusted_hosts=trusted_hosts if trusted_hosts is not None else ["testserver"])

    @app.get("/api/v1/agents")
    async def _read():
        return {"ok": True}

    @app.post("/api/v1/agents/x/console/input")
    async def _type_into_a_live_pty():
        return {"ok": True}

    return app


class APageOnAnotherSiteCannotDriveThisServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_app(), base_url=LOOPBACK)

    def test_a_cross_site_page_cannot_type_into_a_live_console(self):
        """The endpoint the audit named. It is the sharpest one, not the only one."""
        response = self.client.post("/api/v1/agents/x/console/input", headers=CROSS)
        self.assertEqual(response.status_code, 403)
        self.assertIn("cors_origins", response.text,
                      "the refusal must say how an operator legitimises their own dashboard")

    def test_Nodes_own_fetch_headers_do_not_trip_it(self):
        """Node sends `sec-fetch-mode: cors` on every request -- measured against a local server. A
        guard that read THAT as a browser signal refused the real client, which is exactly what
        happened in aify-env and cost seven red tests to notice."""
        response = self.client.get(
            "/api/v1/agents",
            headers={"sec-fetch-mode": "cors", "user-agent": "node", "accept-language": "*"})
        self.assertEqual(response.status_code, 200)


class TheOperatorsOwnConfigIsHonouredTests(unittest.TestCase):
    def test_a_trailing_slash_or_different_case_still_matches(self):
        """An origin is compared, not parsed, so the two spellings an operator actually writes must
        both work -- otherwise the exemption silently does nothing and looks like the guard is broken."""
        client = TestClient(_app(["https://Dash.Example/"]), base_url=LOOPBACK)
        response = client.get("/api/v1/agents", headers={
            "sec-fetch-site": "cross-site", "origin": "https://dash.example"})
        self.assertEqual(response.status_code, 200)


class TheREALAppInstallsItTests(unittest.TestCase):
    """Everything above builds its own app, so all of it stays green with the guard removed from
    `create_app()` -- proven by mutation, which is the only way that gap ever shows. A middleware
    nothing installs is a defence that cannot fire.

    Ordering is asserted by BEHAVIOUR rather than by reading the list: with a key configured, a
    cross-site page must get 403 and not 401. A 401 would mean the key check ran first, which would
    leave the keyless default -- the shipped one -- unprotected.
    """

    @staticmethod
    def _real_app(api_key: str = ""):
        """`create_app()` with the config it would have, and the key actually in effect.

        PATCHING `API_KEY` IN THE ENVIRONMENT DOES NOTHING HERE, which is how the ordering test below
        came to be vacuous on its first draft: `get_config()` caches a module-level singleton, so by
        the time any test runs the config is already loaded and an env change is never re-read. The
        middleware list showed no `APIKeyMiddleware` at all -- the test was passing because there was
        nothing to order against.
        """
        import dataclasses
        from unittest import mock
        from service.config import get_config
        from service.main import create_app
        patched = dataclasses.replace(get_config(), api_key=api_key)
        with mock.patch("service.main.get_config", return_value=patched):
            return create_app()

    def test_it_runs_OUTSIDE_the_api_key_check(self):
        app = self._real_app(api_key="a-configured-key")
        installed = [m.cls.__name__ for m in app.user_middleware]
        self.assertIn("APIKeyMiddleware", installed,
                      "the key middleware is not even installed, so this proves no ordering")
        client = TestClient(app, base_url=LOOPBACK)
        response = client.get("/api/v1/agents", headers=CROSS)
        self.assertEqual(response.status_code, 403,
                         "a 401 here means the key check ran first, leaving the keyless default open")
