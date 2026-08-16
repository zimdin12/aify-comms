"""`proxy_request` — the composition that decides what actually reaches a sub-container.

Its three filters are already tested by calling them. `proxy_request` itself was among the 71
service functions the suite never entered, and it is the part that has to USE them: a change that
passed `request.headers` straight through would leave every filter test green while the hub's master
API key went to an operator-defined container image on the next request.

So these tests are about the JOIN, not the filters:
  * the request the upstream actually receives — method, body, params, headers;
  * the response the caller actually gets — status and headers;
  * that the upstream response is CLOSED, including when the caller stops reading early.

DRIVEN THROUGH A REAL ASGI REQUEST against a FAKE httpx client. The client is what talks to the
network, so replacing it leaves every line of `proxy_request` running while nothing is dialled. The
alternative — a real sub-container — would test docker.

WHY THE STRIPPING MATTERS, from the module's own incident note: relaying `X-API-Key`/`Authorization`
/`Cookie` verbatim to a sub-container image leaks the master API key to that container, and the
master key admits console keystroke injection. The sub-container authenticates with its own
credentials, not the hub's.
"""

from __future__ import annotations

import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from service.containers import proxy as proxy_module


class _FakeUpstreamResponse:
    def __init__(self, *, status_code=200, headers=None, chunks=(b"hello",), fail_on=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)
        self._fail_on = fail_on
        self.closed = False

    async def aiter_bytes(self):
        for index, chunk in enumerate(self._chunks):
            if self._fail_on is not None and index == self._fail_on:
                raise RuntimeError("upstream went away mid-stream")
            yield chunk

    async def aclose(self):
        self.closed = True


class _FakeClient:
    """Records the request `proxy_request` built, and hands back a canned response."""

    def __init__(self, response: _FakeUpstreamResponse):
        self.response = response
        self.built = None
        self.streamed = None

    def build_request(self, *, method, url, headers, content, params):
        self.built = {
            "method": method, "url": url, "headers": dict(headers),
            "content": content, "params": dict(params),
        }
        return self.built

    async def send(self, req, stream=False):
        self.streamed = stream
        return self.response


class ContainerProxyForwardingTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()

        @self.app.api_route("/proxy", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def _proxy(request: Request):
            return await proxy_module.proxy_request(request, "http://sub-container:8080/v1/thing")

        self.client = TestClient(self.app)
        self._real_client = proxy_module._client

    def tearDown(self):
        proxy_module._client = self._real_client
        self.client.close()

    def _with_upstream(self, **kwargs) -> _FakeClient:
        fake = _FakeClient(_FakeUpstreamResponse(**kwargs))
        proxy_module._client = fake
        return fake

    # ── what the sub-container receives ──────────────────────────────────────────────────────

    def test_the_method_url_and_body_are_forwarded_unchanged(self):
        fake = self._with_upstream()
        response = self.client.post("/proxy", content=b"payload")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.built["method"], "POST")
        self.assertEqual(fake.built["url"], "http://sub-container:8080/v1/thing")
        self.assertEqual(fake.built["content"], b"payload")

    def test_an_empty_body_is_forwarded_as_NOTHING_not_as_empty_bytes(self):
        """`content=b""` and `content=None` are different to httpx: the first makes it send a
        content-length of 0 on a GET, which some upstreams reject."""
        fake = self._with_upstream()
        self.client.get("/proxy")
        self.assertIsNone(fake.built["content"])

    def test_the_hub_CREDENTIALS_never_reach_the_sub_container(self):
        """THE ONE THAT MATTERS, asserted on the composition rather than the filter. A change that
        forwarded `request.headers` directly would leave every filter test green and leak the
        master key to an operator-defined image."""
        fake = self._with_upstream()
        self.client.get("/proxy", headers={
            "X-API-Key": "master-key",
            "Authorization": "Bearer master-token",
            "Cookie": "session=abc",
            "X-Trace-Id": "keep-me",
        })
        forwarded = {name.lower(): value for name, value in fake.built["headers"].items()}
        for credential in ("x-api-key", "authorization", "cookie"):
            with self.subTest(header=credential):
                self.assertNotIn(credential, forwarded)
        self.assertEqual(forwarded.get("x-trace-id"), "keep-me",
                         "an ordinary header must still be forwarded")

    def test_hop_by_hop_headers_are_not_relayed_across_the_proxy(self):
        fake = self._with_upstream()
        self.client.get("/proxy", headers={"Connection": "keep-alive", "TE": "trailers"})
        forwarded = {name.lower() for name in fake.built["headers"]}
        self.assertNotIn("connection", forwarded)
        self.assertNotIn("te", forwarded)
        self.assertNotIn("host", forwarded, "the hub's own Host would misroute the upstream")

    def test_an_api_key_in_the_QUERY_STRING_is_dropped_too(self):
        """The same credential arrives both ways, so both are filtered — and the composition is
        what proves the query filter is actually reached."""
        fake = self._with_upstream()
        self.client.get("/proxy", params={"api_key": "master-key", "model": "gpt"})
        self.assertEqual(fake.built["params"], {"model": "gpt"})

    def test_the_upstream_is_asked_to_STREAM(self):
        """Buffering would defeat the purpose: these responses are SSE and token streams, and a
        buffered proxy turns a live LLM stream into a long silence and then a wall of text."""
        fake = self._with_upstream()
        self.client.get("/proxy")
        self.assertIs(fake.streamed, True)

    # ── what the caller gets back ────────────────────────────────────────────────────────────

    def test_the_upstream_status_is_preserved(self):
        for status in (200, 201, 404, 500):
            with self.subTest(status=status):
                self._with_upstream(status_code=status)
                self.assertEqual(self.client.get("/proxy").status_code, status)

    def test_the_body_arrives_in_order_and_whole(self):
        self._with_upstream(chunks=(b"one ", b"two ", b"three"))
        self.assertEqual(self.client.get("/proxy").content, b"one two three")

    def test_headers_that_would_LIE_about_the_body_are_dropped(self):
        """`aiter_bytes()` decodes, so `content-encoding` no longer describes the stream and
        `content-length` is the compressed size. Keeping either makes a client decode garbage —
        the 2026-07-03 bughunt."""
        self._with_upstream(headers={
            "content-encoding": "gzip",
            "content-length": "9999",
            "transfer-encoding": "chunked",
            "content-type": "application/json",
            "x-upstream": "keep-me",
        })
        response = self.client.get("/proxy")
        lowered = {name.lower() for name in response.headers}
        self.assertNotIn("content-encoding", lowered)
        self.assertNotIn("transfer-encoding", lowered)
        self.assertEqual(response.headers.get("x-upstream"), "keep-me")
        self.assertEqual(response.headers.get("content-type"), "application/json")

    # ── the upstream connection is not leaked ────────────────────────────────────────────────

    def test_the_upstream_response_is_CLOSED_after_a_normal_stream(self):
        """The client is pooled, so an unclosed response holds a connection out of the pool for the
        lifetime of the process. A hundred of those is a hub that stops proxying."""
        fake = self._with_upstream(chunks=(b"a", b"b"))
        self.client.get("/proxy")
        self.assertTrue(fake.response.closed, "the upstream response was never closed")

    def test_the_upstream_response_is_closed_even_when_the_stream_FAILS(self):
        """`finally`, not the happy path. An upstream that dies mid-stream is the normal case for a
        long inference request, and that is exactly when the connection must not leak."""
        fake = self._with_upstream(chunks=(b"a", b"b"), fail_on=1)
        with self.assertRaises(Exception):
            self.client.get("/proxy")
        self.assertTrue(fake.response.closed, "a failed stream leaked its upstream connection")
