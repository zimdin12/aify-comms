"""The loopback REST client every `comms_*` SSE tool calls — all twenty-six of them.

`api_url` and `api` were among the service functions the suite never entered, which is a strange
place for a coverage hole: this is the ONE point where the SSE MCP transport talks to the service.
Everything an agent does over SSE — register, send, dispatch, read an inbox, tail a console — is a
call to `api()`. Its tests were the tools' tests, and every one of those replaces `_api` with a
canned payload, so the function they all stand on had never run.

WHAT IS ACTUALLY AT RISK HERE, in the order it would hurt:

* THE API KEY. It is attached from config on every call. Drop it and every SSE tool 401s at once —
  loud, and the easiest of these to notice.
* THE METHOD DISPATCH. `params` are sent on GET and DELETE, `json` on POST. A body attached to the
  wrong verb is not an error: httpx sends it, the service ignores it, and the tool reports whatever
  the service does with a request that carried none of its arguments.
* THE ERROR PATH. There is no `raise_for_status`, deliberately: a 400 from the service carries a
  `detail` the agent is meant to read. Turning that into an exception would replace the service's
  explanation with a stack trace.
* THE NON-JSON FALLBACK. An HTML error page from a proxy becomes `{status, text}` truncated to 500
  characters, rather than an exception from `resp.json()`.

These run against a real HTTP server on loopback rather than a mocked transport, because the client
constructs its own `httpx.AsyncClient` inline — there is nothing to inject, and a test that patched
httpx would be testing the patch.
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


class _RecordingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        parsed = urlparse(self.path)
        self.server.requests.append({
            "method": self.command,
            "path": parsed.path,
            "query": parsed.query,
            "headers": dict(self.headers),
            "body": body,
        })
        status, payload = self.server.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _record
    do_POST = _record
    do_DELETE = _record
    do_PATCH = _record

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

    @property
    def requests(self) -> list:
        return self.server.requests

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
        # `api_url()` memoises into this global; pointing it at the fake service is the whole
        # redirection, and it is the module's own state rather than a patched attribute.
        api_client._BASE_URL = self.service.base
        self.cfg.api_key = ""

    def tearDown(self):
        api_client._BASE_URL, self.cfg.api_key, self.cfg.port = self._saved
        self.service.close()

    def call(self, *args, **kwargs):
        return asyncio.run(api_client.api(*args, **kwargs))

    @property
    def last(self) -> dict:
        return self.service.requests[-1]


class ApiUrlTests(ApiClientTestCase):
    def test_the_url_is_built_from_the_CONFIGURED_port(self):
        """The transport runs inside the container beside the service, so this is a loopback call —
        but the port is configuration, and a hardcoded one breaks every SSE tool on a service that
        was moved off 8800."""
        api_client._BASE_URL = None
        self.cfg.port = 18811
        self.assertEqual(api_client.api_url(), "http://127.0.0.1:18811/api/v1")

    def test_the_url_is_MEMOISED_after_the_first_call(self):
        """Called on every one of the twenty-six tool paths. The cache is why a config re-read is
        not on the hot path — and why a port changed after the first call is not picked up, which is
        correct for a process whose config is fixed at boot."""
        api_client._BASE_URL = None
        self.cfg.port = 18811
        first = api_client.api_url()
        self.cfg.port = 29999
        self.assertEqual(api_client.api_url(), first)


class RequestShapeTests(ApiClientTestCase):
    def test_a_GET_reaches_the_path_under_the_api_prefix(self):
        self.assertEqual(self.call("GET", "/agents"), {"ok": True})
        self.assertEqual(self.last["method"], "GET")
        self.assertEqual(self.last["path"], "/api/v1/agents")

    def test_GET_params_become_the_QUERY_STRING(self):
        """Nine tool call sites pass `params=` — the inbox limit, the console line count, the
        channel history size. Dropped, every one of them silently gets the endpoint's default."""
        self.call("GET", "/messages/inbox/a", params={"limit": "5", "unreadOnly": "true"})
        self.assertIn("limit=5", self.last["query"])
        self.assertIn("unreadOnly=true", self.last["query"])

    def test_a_POST_carries_its_JSON_BODY(self):
        """Nine more call sites, and these are the writes: register, send, dispatch, channel
        create, run control. A body that does not arrive is a request the service answers with a
        422 about missing fields the caller definitely supplied."""
        self.call("POST", "/messages/send", {"from": "a", "to": "b", "body": "hello"})
        self.assertEqual(self.last["method"], "POST")
        self.assertEqual(json.loads(self.last["body"]),
                         {"from": "a", "to": "b", "body": "hello"})

    def test_a_POST_with_no_body_sends_NO_body_at_all(self):
        """`json_data` defaults to None and httpx then omits the body entirely — it does not send
        the literal `null`, which is what I assumed when I wrote this test.

        Worth knowing rather than worth changing: every one of the nine POST call sites passes a
        dict today, so this shape is unreachable from the tools. If one ever stops, the endpoint
        sees a bodyless POST and answers 422 about missing fields — which the error path below
        returns to the agent verbatim, so it is at least legible."""
        self.call("POST", "/clear")
        self.assertEqual(self.last["method"], "POST")
        self.assertEqual(self.last["body"], b"")

    def test_a_DELETE_carries_its_params(self):
        self.call("DELETE", "/shared/notes.md", params={"agentId": "a"})
        self.assertEqual(self.last["method"], "DELETE")
        self.assertIn("agentId=a", self.last["query"])

    def test_an_UNKNOWN_METHOD_makes_no_request_at_all(self):
        """The tools name their verb as a string. A typo has to come back as an error the caller
        can read, and must not become a request with some default verb."""
        result = self.call("PATCH", "/agents/a")
        self.assertEqual(result, {"error": "Unknown method: PATCH"})
        self.assertEqual(self.service.requests, [], "an unknown method still reached the service")

    def test_the_method_is_matched_EXACTLY(self):
        """Upper-case only, as the call sites write it. Pinned so a lower-case verb fails visibly
        here rather than as a mystery at one call site."""
        self.assertEqual(self.call("get", "/agents"), {"error": "Unknown method: get"})


class ApiKeyTests(ApiClientTestCase):
    def test_the_configured_key_is_sent_on_every_call(self):
        """Attached from config per call, not captured at import. Dropping it 401s every SSE tool
        at once on any deployment that set a key."""
        self.cfg.api_key = "secret-value"
        for method, kwargs in (("GET", {}), ("POST", {}), ("DELETE", {})):
            with self.subTest(method=method):
                self.call(method, "/agents", **kwargs)
                self.assertEqual(self.last["headers"].get("X-API-Key"), "secret-value")

    def test_NO_key_header_is_sent_when_none_is_configured(self):
        """The default deployment has no key. Sending an empty one is a header a strict proxy can
        reject, and it makes "unset" indistinguishable from "set to nothing"."""
        self.cfg.api_key = ""
        self.call("GET", "/agents")
        self.assertNotIn("x-api-key", {k.lower() for k in self.last["headers"]})

    def test_a_key_set_AFTER_import_is_still_picked_up(self):
        """The key's VALUE is read inside the call, not snapshotted at import.

        Note what this does NOT prove, because my first version of it claimed otherwise: moving
        `cfg = get_config()` to module scope is not a behaviour change at all. `get_config()` is a
        pure singleton with no reload path, so the captured object is the same object and sees every
        later mutation. Only a capture of the VALUE — `_API_KEY = get_config().api_key` at import —
        is distinguishable, and that is the mutation this test kills."""
        self.call("GET", "/agents")
        self.cfg.api_key = "rotated"
        self.call("GET", "/agents")
        self.assertEqual(self.last["headers"].get("X-API-Key"), "rotated")


class ResponseHandlingTests(ApiClientTestCase):
    def test_the_json_body_is_returned_as_is(self):
        self.service.respond_with(200, {"agents": [{"id": "a"}], "count": 1})
        self.assertEqual(self.call("GET", "/agents"), {"agents": [{"id": "a"}], "count": 1})

    def test_an_ERROR_STATUS_still_returns_the_services_explanation(self):
        """No `raise_for_status`, and that is the design: a 400 carries a `detail` written for the
        agent to read. Raising instead would replace the reason with a stack trace, at the one
        boundary where the agent is the audience."""
        self.service.respond_with(400, {"detail": "Need 'to' or 'toRole'"})
        self.assertEqual(self.call("POST", "/dispatch", {}), {"detail": "Need 'to' or 'toRole'"})

    def test_a_NON_JSON_body_becomes_a_status_and_text_pair(self):
        """A proxy error page, a gateway timeout, an nginx 502. `resp.json()` would raise inside a
        tool call and surface as a transport error rather than as what happened."""
        self.service.respond_with(502, b"<html><body>Bad Gateway</body></html>")
        result = self.call("GET", "/agents")
        self.assertEqual(result["status"], 502)
        self.assertIn("Bad Gateway", result["text"])

    def test_the_fallback_text_is_TRUNCATED(self):
        """It goes into an MCP tool result an agent reads. A full HTML error page would push the
        answer out of the agent's context to say "the proxy is unhappy"."""
        self.service.respond_with(500, b"x" * 5000)
        result = self.call("GET", "/agents")
        self.assertEqual(len(result["text"]), 500)

    def test_an_EMPTY_body_falls_back_rather_than_raising(self):
        """204-shaped answers. An empty string is not JSON, and the fallback is what keeps that
        from becoming an exception."""
        self.service.respond_with(204, b"")
        result = self.call("DELETE", "/shared/x")
        self.assertEqual(result, {"status": 204, "text": ""})


if __name__ == "__main__":
    unittest.main()
