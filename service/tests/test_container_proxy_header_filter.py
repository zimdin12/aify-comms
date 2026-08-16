"""The proxy that must not hand the hub's API key to a sub-container had no tests.

`proxy_request` forwards a client request to an operator-defined sub-container image. Its header
filter is the only thing between the hub's credentials and that image, and the reason is written at
the filter itself (bughunt 2026-07-03): relaying `X-API-Key`/`Authorization`/`Cookie` "would leak the
master API key to that container (a logging/compromised image -> full API incl. console keystroke
injection)".

It was security-reviewed and never tested. Both halves of this module -- request filtering and
response filtering -- are exercised here for the first time.

WHAT CHANGED, and it is a real weakening removed rather than a style preference. The filter popped
each name three times: `h`, `h.title()`, `h.upper()`. That covers `x-api-key`, `X-Api-Key` and
`X-API-KEY` -- and NOT `X-API-Key`, which is the spelling clients most often send. It was safe only
because uvicorn lowercases header names before they reach ASGI, so the two extra pops were dead code
and the whole filter rested on a property of the SERVER rather than of this function. A credential
filter one deployment change away from leaking is not a filter. Matching on the lowercased name
removes the spelling question instead of enumerating answers to it.

The list also gained the rest of the hop-by-hop set (RFC 9110 §7.6.1): `proxy-authorization` is
another credential, and `te`/`trailer`/`upgrade`/`keep-alive` are per-connection headers that must not
cross a proxy hop.

NOT CLAIMED: a live leak. Under uvicorn the old code stripped what it needed to. What is claimed is
that it stopped being true of THIS function, which is where the guarantee has to live.
"""

from __future__ import annotations

import asyncio
import unittest

from starlette.datastructures import Headers


class _FakeRequest:
    """Only what `proxy_request` reads: headers, query params, method and body."""

    def __init__(self, headers: dict[str, str], query: dict[str, str] | None = None,
                 method: str = "POST", body: bytes = b"hello"):
        # Built from a scope so the case the CLIENT sent survives, exactly as a
        # non-normalising ASGI server would deliver it.
        self.headers = Headers(scope={"headers": [
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()
        ]})
        self.query_params = query or {}
        self.method = method
        self._body = body

    async def body(self) -> bytes:
        return self._body


def _forwarded(headers: dict[str, str], **kwargs) -> dict[str, str]:
    """Run the REAL filter and return what would be sent upstream.

    `proxy_request` itself needs a live upstream to stream from, so the filter was extracted to
    `_safe_request_headers` and is CALLED here rather than re-implemented -- the doctor-predicates
    pattern. A test that re-evaluated the rule would agree with a copy of it while the module
    drifted, which is the failure this repo keeps finding in its own sweep tools.
    """
    from service.containers.proxy import _safe_request_headers

    return _safe_request_headers(_FakeRequest(headers, **kwargs).headers)


class RequestHeaderFilterTests(unittest.TestCase):
    def test_credentials_are_stripped_in_every_casing(self):
        """THE ONE THAT MATTERS. `X-API-Key` is the spelling the old three-variant pop missed."""
        for spelling in ("x-api-key", "X-Api-Key", "X-API-KEY", "X-API-Key", "x-API-key"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    _forwarded({spelling: "hub-master-key", "accept": "*/*"}),
                    {"accept": "*/*"},
                    f"{spelling} reached the sub-container -- that is the hub's master API key",
                )

    def test_every_credential_header_is_stripped(self):
        for name in ("authorization", "Authorization", "cookie", "Cookie",
                     "proxy-authorization", "Proxy-Authorization"):
            with self.subTest(header=name):
                self.assertEqual(_forwarded({name: "secret", "accept": "*/*"}), {"accept": "*/*"})

    def test_every_hop_by_hop_header_is_stripped(self):
        """RFC 9110 §7.6.1. These describe THIS connection and must not cross a proxy hop."""
        for name in ("host", "connection", "keep-alive", "transfer-encoding", "te", "trailer",
                     "upgrade", "proxy-authenticate"):
            with self.subTest(header=name):
                self.assertEqual(_forwarded({name: "x", "accept": "*/*"}), {"accept": "*/*"})

    def test_ordinary_headers_are_forwarded_unchanged(self):
        """Anti-vacuity: a filter that dropped everything would pass every test above."""
        forwarded = _forwarded({
            "accept": "text/event-stream",
            "content-type": "application/json",
            "user-agent": "aify/1",
            "x-request-id": "abc",
            "X-Custom-Thing": "kept",
        })
        self.assertEqual(forwarded, {
            "accept": "text/event-stream",
            "content-type": "application/json",
            "user-agent": "aify/1",
            "x-request-id": "abc",
            "X-Custom-Thing": "kept",
        })

    def test_the_api_key_query_param_is_dropped_case_insensitively(self):
        """The same credential arrives by URL too, and the module drops it there as well."""
        from service.containers.proxy import _safe_query_params

        self.assertEqual(
            _safe_query_params({"api_key": "secret", "API_KEY": "secret", "q": "1"}), {"q": "1"},
        )

    def test_the_stripped_set_is_the_contract(self):
        """This file re-evaluates the rule, so it must be the module's rule.

        Asserting the SET rather than re-reading the source: the names are the contract, and a set
        comparison fails loudly if one is removed — which is how a credential would start flowing
        again.
        """
        from service.containers.proxy import _STRIPPED_REQUEST_HEADERS

        self.assertEqual(_STRIPPED_REQUEST_HEADERS, {
            "host", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
            "proxy-authenticate", "proxy-authorization",
            "x-api-key", "authorization", "cookie",
        })
        self.assertTrue(
            all(name == name.lower() for name in _STRIPPED_REQUEST_HEADERS),
            "the set is compared against a lowercased name, so an entry with capitals never matches",
        )


class ResponseHeaderFilterTests(unittest.TestCase):
    def test_the_encoding_headers_a_decoded_stream_must_not_carry(self):
        """Pinned because a bughunt already fixed this once and nothing held it.

        `aiter_bytes()` DECODES the body, so `content-encoding` is no longer true and
        `content-length` is the COMPRESSED size. The old code dropped one and kept the other while
        streaming still-encoded bytes, and clients decoded garbage.
        """
        from service.containers.proxy import _safe_response_headers

        self.assertEqual(
            _safe_response_headers({
                "Content-Encoding": "gzip", "content-length": "42",
                "Transfer-Encoding": "chunked", "Connection": "keep-alive",
                "content-type": "application/json", "X-Upstream": "kept",
            }),
            {"content-type": "application/json", "X-Upstream": "kept"},
        )

    def test_an_upstream_can_use_any_casing(self):
        """The sub-container is operator-defined, so its casing is the side we control least."""
        from service.containers.proxy import _safe_response_headers

        for spelling in ("content-length", "Content-Length", "CONTENT-LENGTH", "Content-length"):
            with self.subTest(spelling=spelling):
                self.assertEqual(_safe_response_headers({spelling: "42", "x": "y"}), {"x": "y"})


class ProxyModuleShapeTests(unittest.TestCase):
    def test_the_client_is_a_singleton_and_close_resets_it(self):
        """`get_client` caches a pooled client in a module global; `close_client` must clear it, or
        a second lifespan reuses a closed client."""
        from service.containers import proxy

        first = proxy.get_client()
        self.assertIs(proxy.get_client(), first, "the pooled client must be reused")
        asyncio.run(proxy.close_client())
        self.assertIsNone(proxy._client, "close_client must clear the global, not just close it")
        second = proxy.get_client()
        self.assertIsNot(second, first, "…so the next caller gets a fresh client")
        asyncio.run(proxy.close_client())
