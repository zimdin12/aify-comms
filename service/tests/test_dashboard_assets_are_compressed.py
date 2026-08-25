"""The dashboard shell compresses too, not just the API.

GZipMiddleware went onto the service app (:8800) first, and that covered the polling API and none of
the page. The shell and every ES module it loads are served by a SEPARATE FastAPI app on :8801 which
had no middleware at all — so the fix looked complete and covered the smaller half.

Two instruments said so independently:

  * Chrome's trace of a cold load, Document request latency: "Compression was applied: FAILED",
    25.5 kB wasted on the document alone.
  * A direct check: /assets/app.js returned 54,605 bytes whether or not the client offered gzip.

Measured over the 73 files this app serves, 2026-08-25: 753,268 bytes raw against 258,748 gzipped —
482 KB per cold load, 2.9x. The 60-plus unbundled ES modules are the bulk of it.

NOT A LATENCY WIN ON LOCALHOST, and the trace is explicit: estimated savings FCP 0 ms, LCP 0 ms
against a measured LCP of 131 ms. There is no round trip here to give back. It is a bandwidth win that
becomes a latency win only for a browser that is not on this machine. Recorded because the temptation
with a compression change is to claim it made something faster.

TWO APPS, so this file tests the one the other test does not. test_responses_are_compressed.py builds
create_app(); nothing built new_dashboard_app until now, which is exactly how the gap survived.
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient


def dashboard_client() -> TestClient:
    """The REAL shell app. Importing it is the point: the gap was that no test ever built it."""
    from service.new_dashboard_app import app

    return TestClient(app)


class DashboardAssetsAreCompressed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = dashboard_client()

    def test_a_module_comes_back_gzipped(self):
        response = self.client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("content-encoding"), "gzip",
            "the dashboard's own assets are still uncompressed; the API was the smaller half",
        )

    def test_the_shell_document_comes_back_gzipped(self):
        """The document Chrome measured 25.5 kB of waste on."""
        response = self.client.get("/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-encoding"), "gzip")

    def test_the_module_still_parses_after_the_round_trip(self):
        """Compression is worthless if the module arrives damaged, and a broken app.js is a blank page
        rather than an error anyone would attribute to this."""
        response = self.client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
        body = response.text
        self.assertIn("import", body, "app.js did not survive decompression intact")
        self.assertGreater(len(body), 10_000, "app.js came back truncated")

    def test_a_client_that_does_not_ask_is_not_given_gzip(self):
        """Negative control. Without it these tests cannot tell negotiation from a blanket rewrite, and
        a blanket rewrite breaks every client that did not opt in."""
        response = self.client.get("/assets/app.js", headers={"Accept-Encoding": "identity"})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers.get("content-encoding"), "gzip")

    def test_the_revalidation_headers_still_ride_along(self):
        """This app's other middleware forces revalidation so an ES module cannot be served stale from
        cache. Compression sits in front of it; if the cache-control header were lost, every reload
        would serve yesterday's modules — a far worse bug than uncompressed bytes."""
        response = self.client.get("/assets/app.js", headers={"Accept-Encoding": "gzip"})
        self.assertIn(
            "no-cache", (response.headers.get("cache-control") or "").lower(),
            "the revalidation header was lost when compression was added",
        )


if __name__ == "__main__":
    unittest.main()
