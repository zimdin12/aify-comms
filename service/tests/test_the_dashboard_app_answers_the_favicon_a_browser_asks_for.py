"""The dashboard app answers `/favicon.ico` without a key (2026-10-14 audit, D15).

Its sign-in page names no icon, so a browser falls back to `/favicon.ico` on the origin, and this app
answered that with a 404 on every visit. Measured live on port 8811: `/favicon.svg` 200, `/favicon.ico` 404.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from service import new_dashboard_app


class TheDashboardAppAnswersTheFavicon(unittest.TestCase):
    def test_both_favicon_paths_answer_with_the_svg_and_no_key(self):
        client = TestClient(new_dashboard_app.app)
        for path in ("/favicon.ico", "/favicon.svg"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(response.headers["content-type"], "image/svg+xml")
                self.assertTrue(response.content.lstrip().startswith(b"<"), response.content[:40])
        self.assertEqual(client.get("/no-such-icon.ico").status_code, 404, "control: an unknown path still 404s")


if __name__ == "__main__":
    unittest.main()
