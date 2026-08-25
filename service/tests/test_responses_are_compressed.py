"""The service negotiates compression, and a large response comes back compressed.

Measured on the live instance 2026-08-25, before this existed: one dashboard poll cycle fetched
1,093,414 bytes across its six largest endpoints, and `curl --compressed` returned exactly the same
count -- the service advertised no encoding, so there was nothing for a client to negotiate. Those
bytes gzip to 243,370. At the ~15s poll that is 250 MB/hour per open tab against 55.

BEHAVIOURAL, not a source pin. Asserting `GZipMiddleware` appears in main.py would prove a line was
written; it would still pass if the middleware were registered after something that already read the
response, or with a floor so high nothing ever qualified. So this builds the real app and reads what
comes back off the wire.
"""
from __future__ import annotations

import json
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient


def app_under_test() -> FastAPI:
    """The REAL factory. The shared test base builds a bare FastAPI and mounts the router, which
    carries no middleware at all -- a suite built on it cannot see this question."""
    from service.main import create_app

    return create_app()


class ResponsesAreCompressed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_under_test()
        # A route big enough to clear the floor, owned by this test so the assertion does not depend
        # on how much data some real endpoint happens to hold today.
        @cls.app.get("/_compression_probe/large")
        def _large():  # noqa: ANN202
            return {"rows": [{"id": f"agent-{i}", "status": "available"} for i in range(400)]}

        @cls.app.get("/_compression_probe/tiny")
        def _tiny():  # noqa: ANN202
            return {"ok": True}

        cls.client = TestClient(cls.app)

    def test_a_large_response_comes_back_gzipped(self):
        response = self.client.get(
            "/_compression_probe/large", headers={"Accept-Encoding": "gzip"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("content-encoding"), "gzip",
            "a large response was sent uncompressed even though the client offered gzip",
        )

    def test_the_client_still_gets_the_real_json(self):
        """The saving is worthless if the payload arrives mangled."""
        response = self.client.get(
            "/_compression_probe/large", headers={"Accept-Encoding": "gzip"},
        )
        body = json.loads(response.content)  # httpx decompresses; this proves it round-trips
        self.assertEqual(len(body["rows"]), 400)
        self.assertEqual(body["rows"][0]["id"], "agent-0")

    def test_a_client_that_does_not_ask_is_not_given_gzip(self):
        """Negative control. A test that only ever sees gzip cannot tell negotiation from a blanket
        rewrite, and a blanket rewrite would break every client that did not opt in."""
        response = self.client.get(
            "/_compression_probe/large", headers={"Accept-Encoding": "identity"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers.get("content-encoding"), "gzip")

    def test_a_tiny_response_is_left_alone(self):
        """Below the floor a gzip header costs more than it saves, and the small responses here
        (/settings at 1,477 bytes, /stats at 2,395) are the ones latency shows up on."""
        response = self.client.get(
            "/_compression_probe/tiny", headers={"Accept-Encoding": "gzip"},
        )
        self.assertNotEqual(response.headers.get("content-encoding"), "gzip")


if __name__ == "__main__":
    unittest.main()
