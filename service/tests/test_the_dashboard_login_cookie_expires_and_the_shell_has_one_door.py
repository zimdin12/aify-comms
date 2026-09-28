"""The 8801 login cookie carries its own issue time and expires on the server; the shell has one door.

THE DEFECTS (external review, 2026-09-29, L1 and L2).

L2. The cookie was `HMAC(API_KEY, "aify-dashboard-session-v1")`: one value per key, the same for every
browser and every sign-in, and checked with no notion of age. `max_age` only asks the browser to drop
it, so a copied cookie opened the page carrying the operator key for as long as the API key stood.
The cookie is now `<issued_at>.<HMAC(API_KEY, issued_at)>`, and the server refuses one older than
`COOKIE_MAX_AGE` or stamped in the future.

L1. `/assets/index.html` served the dashboard shell without the login. It never carried the operator
key, which only `/` injects, so it exposed nothing the login protects; it is refused anyway, so the
login is the only way to the page and nothing added to the shell later can slip past it.

Driven through the real app with the config and the clock replaced, like
`test_the_dashboard_page_requires_the_api_key.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from service import new_dashboard_app
from service.dashboard_access import COOKIE, COOKIE_MAX_AGE

API_KEY = "banana"
OPERATOR_KEY = "op-secret-for-this-test"
T0 = 1_790_000_000


class TheDashboardLoginCookieExpiresTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(api_key=API_KEY, operator_key=OPERATOR_KEY,
                                      data_dir=tempfile.mkdtemp(), version="test")
        self.now = T0
        for name, value in (("get_config", lambda: self.config), ("_now_seconds", lambda: self.now)):
            patcher = mock.patch.object(new_dashboard_app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(new_dashboard_app.app)

    def _sign_in(self) -> str:
        response = self.client.post("/login", data={"key": API_KEY}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        return response.cookies.get(COOKIE) or ""

    def _page_with(self, cookie: str) -> int:
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE, cookie)
        return self.client.get("/").status_code

    def test_two_sign_ins_get_different_cookies(self):
        first = self._sign_in()
        self.now = T0 + 60
        second = self._sign_in()
        self.assertTrue(first and second)
        self.assertNotEqual(first, second, "the cookie is a fixed function of the API key")

    def test_a_fresh_cookie_opens_the_page(self):
        """CONTROL: the refusals below would pass if every cookie were refused."""
        cookie = self._sign_in()
        self.now = T0 + COOKIE_MAX_AGE - 1
        self.assertEqual(self._page_with(cookie), 200)

    def test_the_server_refuses_a_cookie_past_its_age(self):
        cookie = self._sign_in()
        self.now = T0 + COOKIE_MAX_AGE + 1
        self.assertEqual(self._page_with(cookie), 401, "the server honoured a cookie past COOKIE_MAX_AGE")

    def test_a_cookie_whose_time_was_edited_is_refused(self):
        cookie = self._sign_in()
        _, _, signature = cookie.partition(".")
        self.now = T0 + COOKIE_MAX_AGE + 10
        self.assertEqual(self._page_with(f"{self.now}.{signature}"), 401, "an edited issue time was honoured")

    def test_a_cookie_stamped_in_the_future_is_refused(self):
        """A key holder could otherwise mint one that outlives the age limit."""
        stamp = T0 + 10 * COOKIE_MAX_AGE
        signature = hmac.new(API_KEY.encode(), f"aify-dashboard-session-v2:{stamp}".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self._page_with(f"{stamp}.{signature}"), 401)

    def test_the_old_fixed_cookie_is_refused(self):
        """What every browser signed in before this change holds; it signs in again once."""
        legacy = hmac.new(API_KEY.encode(), b"aify-dashboard-session-v1", hashlib.sha256).hexdigest()
        self.assertEqual(self._page_with(legacy), 401)

    def test_the_bookmark_still_signs_the_browser_in(self):
        self.client.cookies.clear()
        response = self.client.get("/", params={"api_key": API_KEY})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._page_with(response.cookies.get(COOKIE) or ""), 200)


class TheShellHasOneDoorTests(unittest.TestCase):
    def setUp(self):
        config = SimpleNamespace(api_key=API_KEY, operator_key=OPERATOR_KEY,
                                 data_dir=tempfile.mkdtemp(), version="test")
        patcher = mock.patch.object(new_dashboard_app, "get_config", lambda: config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(new_dashboard_app.app)

    def test_the_shell_is_not_served_as_an_asset(self):
        self.assertEqual(self.client.get("/assets/index.html").status_code, 404)

    def test_CONTROL_the_assets_the_shell_and_the_login_page_load_are_still_served(self):
        for path in ("/assets/app.js", "/assets/api-key.mjs", "/assets/api-origin.mjs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
