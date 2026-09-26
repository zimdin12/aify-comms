"""The dashboard page, which carries the operator key, is served only to a browser that showed the API key.

Until v0.7.4 anything that could reach 8801 got the page with the operator key in it; the key prompt came
only after the page's own service calls failed. The operator ruled the dashboard must sit behind API_KEY
(service/dashboard_access.py). Driven through the real app with the config replaced.
"""

from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from service import new_dashboard_app
from service.dashboard_access import COOKIE, session_token

API_KEY = "banana"
OPERATOR_KEY = "op-secret-for-this-test"


class TheDashboardPageRequiresTheApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(api_key=API_KEY, operator_key=OPERATOR_KEY, operator_key_dir="",
                                      data_dir=tempfile.mkdtemp(), version="test")
        patcher = mock.patch.object(new_dashboard_app, "get_config", lambda: self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(new_dashboard_app.app)

    def _carries_operator_key(self, response) -> bool:
        return OPERATOR_KEY in response.text or "__AIFY_OPERATOR_KEY__" in response.text

    def test_a_browser_with_nothing_gets_the_login_page_and_no_operator_key(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 401)
        self.assertIn('action="/login"', r.text)
        self.assertFalse(self._carries_operator_key(r), "the operator key reached an unauthenticated browser")

    def test_the_bookmark_opens_the_page_and_signs_the_browser_in(self):
        r = self.client.get("/", params={"api_key": API_KEY})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._carries_operator_key(r), "CONTROL: an authenticated page carries the operator key")
        cookie = r.headers.get("set-cookie", "")
        self.assertIn(f"{COOKIE}={session_token(API_KEY)}", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("samesite=strict", cookie.lower())
        self.assertNotIn(f"={API_KEY};", cookie, "the cookie must not hold the key itself")
        again = self.client.get("/")
        self.assertEqual(again.status_code, 200, "the cookie did not keep the browser signed in")

    def test_a_wrong_key_is_refused(self):
        r = self.client.get("/", params={"api_key": "apple"})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(self._carries_operator_key(r))

    def test_the_login_form_signs_in_with_the_right_key_only(self):
        wrong = self.client.post("/login", data={"key": "apple"}, follow_redirects=False)
        self.assertEqual(wrong.status_code, 401)
        self.assertIn("That key was refused", wrong.text)
        right = self.client.post("/login", data={"key": API_KEY}, follow_redirects=False)
        self.assertEqual(right.status_code, 303)
        self.assertIn(f"{COOKIE}={session_token(API_KEY)}", right.headers.get("set-cookie", ""))

    def test_changing_the_api_key_signs_every_browser_out(self):
        self.client.cookies.set(COOKIE, session_token(API_KEY))
        self.assertEqual(self.client.get("/").status_code, 200)
        self.config.api_key = "a-new-key"
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_the_login_page_stores_the_key_where_the_dashboard_reads_it(self):
        page = self.client.get("/").text
        self.assertIn('import { writeApiKey } from "/assets/api-key.mjs"', page)
        self.assertIn('import { defaultApiOrigin } from "/assets/api-origin.mjs"', page)
        self.assertIn('data-default-api-port="8800"', page)

    def test_CONTROL_with_no_api_key_configured_the_page_is_open_as_the_service_is(self):
        self.config.api_key = ""
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self._carries_operator_key(r))

    def test_CONTROL_assets_are_served_without_the_cookie(self):
        """The login page loads two of them, and none carries a secret."""
        self.assertEqual(self.client.get("/assets/api-key.mjs").status_code, 200)
