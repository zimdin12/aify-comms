"""The loopback REST client every `comms_*` tool the service serves calls (`request` and `call`).

Every tool test replaces `call` with a canned answer, so the client they all stand on runs only here.

WHAT IS AT RISK, in the order it would hurt:

* THE API KEY. Attached from config on every call. Drop it and every tool 401s at once.
* THE BODY. A JSON body that does not arrive is a request the service answers with a 422 about fields
  the caller definitely supplied.
* THE ERROR PATH. A non-2xx raises `ServiceError` worded `HTTP <status>: <body>`, as the JS `httpCall`
  throws, with the status and parsed payload kept for a tool that branches on them (comms_register's
  404). A non-JSON body (a proxy's HTML page) must still raise that, not a JSON decode error.

These run against a real HTTP server on loopback: the client builds its own `httpx.AsyncClient`, so
there is nothing to inject, and a test that patched httpx would be testing the patch.
"""

from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from service.config import get_config
from service.sse import api_client
from service.sse.api_client import ServiceError


class _RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        self.server.requests.append({
            "method": self.command, "path": parsed.path, "query": parsed.query,
            "headers": dict(self.headers), "body": body,
        })
        status, payload = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_DELETE = do_PATCH = _record

    def log_message(self, *args):
        pass


class _FakeService:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
        self.server.daemon_threads = True
        self.server.requests = []
        self.respond_with(200, {"ok": True})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/api/v1"

    def respond_with(self, status: int, payload) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.server.reply = (status, body)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ApiClientTestCase(unittest.TestCase):
    def setUp(self):
        self.service = _FakeService()
        self.cfg = get_config()
        self._saved = (api_client._BASE_URL, self.cfg.api_key, self.cfg.port)
        # `api_url()` memoises into this global; pointing it at the fake service is the whole redirection.
        api_client._BASE_URL = self.service.base
        self.cfg.api_key = ""

    def tearDown(self):
        api_client._BASE_URL, self.cfg.api_key, self.cfg.port = self._saved
        self.service.close()

    def call(self, *args, **kwargs):
        return asyncio.run(api_client.call(*args, **kwargs))

    @property
    def last(self) -> dict:
        return self.service.server.requests[-1]


class ApiUrlTests(ApiClientTestCase):
    def test_the_url_is_built_from_the_CONFIGURED_port(self):
        """A loopback call, but the port is configuration: a hardcoded one breaks every tool on a
        service moved off 8800."""
        api_client._BASE_URL = None
        self.cfg.port = 18811
        self.assertEqual(api_client.api_url(), "http://127.0.0.1:18811/api/v1")

    def test_the_url_is_MEMOISED_after_the_first_call(self):
        """Config is fixed at boot, so a re-read is not on the hot path."""
        api_client._BASE_URL = None
        self.cfg.port = 18811
        first = api_client.api_url()
        self.cfg.port = 29999
        self.assertEqual(api_client.api_url(), first)


class RequestShapeTests(ApiClientTestCase):
    def test_the_path_and_its_query_reach_the_service_under_the_api_prefix(self):
        self.assertEqual(self.call("GET", "/messages/inbox/a?limit=5"), {"ok": True})
        self.assertEqual((self.last["method"], self.last["path"], self.last["query"]), ("GET", "/api/v1/messages/inbox/a", "limit=5"))

    def test_a_body_arrives_as_JSON_on_any_verb(self):
        for method in ("POST", "PATCH"):
            with self.subTest(method=method):
                self.call(method, "/agents/a/profile", {"role": "coder"})
                self.assertEqual((self.last["method"], json.loads(self.last["body"])), (method, {"role": "coder"}))


class ApiKeyTests(ApiClientTestCase):
    def test_the_configured_key_is_sent_on_every_call(self):
        self.cfg.api_key = "secret-value"
        for method in ("GET", "POST", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.call(method, "/agents")
                self.assertEqual(self.last["headers"].get("X-API-Key"), "secret-value")

    def test_NO_key_header_is_sent_when_none_is_configured(self):
        """An empty header is one a strict proxy can reject, and it makes unset read as set to nothing."""
        self.call("GET", "/agents")
        self.assertNotIn("x-api-key", {k.lower() for k in self.last["headers"]})

    def test_a_key_set_AFTER_import_is_still_picked_up(self):
        """The key's VALUE is read inside the call. Kills `_API_KEY = get_config().api_key` at import."""
        self.call("GET", "/agents")
        self.cfg.api_key = "rotated"
        self.call("GET", "/agents")
        self.assertEqual(self.last["headers"].get("X-API-Key"), "rotated")


class ResponseHandlingTests(ApiClientTestCase):
    def test_the_json_body_is_returned_as_is(self):
        self.service.respond_with(200, {"agents": [{"id": "a"}], "count": 1})
        self.assertEqual(self.call("GET", "/agents"), {"agents": [{"id": "a"}], "count": 1})

    def test_an_empty_success_is_an_empty_answer(self):
        self.service.respond_with(204, b"")
        self.assertEqual(self.call("DELETE", "/shared/x"), {})

    def test_an_ERROR_STATUS_raises_with_the_services_explanation(self):
        self.service.respond_with(404, {"detail": "Agent 'a' not found"})
        with self.assertRaises(ServiceError) as raised:
            self.call("PATCH", "/agents/a/profile", {"role": "coder"})
        self.assertEqual((raised.exception.status, raised.exception.payload), (404, {"detail": "Agent 'a' not found"}))
        self.assertEqual(str(raised.exception), 'HTTP 404: {"detail": "Agent \'a\' not found"}')

    def test_a_NON_JSON_error_page_still_raises_as_what_happened(self):
        self.service.respond_with(502, b"<html><body>Bad Gateway</body></html>")
        with self.assertRaises(ServiceError) as raised:
            self.call("GET", "/agents")
        self.assertEqual((raised.exception.status, raised.exception.payload), (502, {}))
        self.assertIn("Bad Gateway", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
