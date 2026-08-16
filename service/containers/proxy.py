"""
Streaming HTTP reverse proxy for routing requests to sub-containers.
Supports SSE/chunked streaming (important for LLM inference).
"""

import logging

import httpx
from fastapi import Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Long-lived client for connection pooling
_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# Strip hop-by-hop AND the hub's OWN credentials before forwarding (bughunt 2026-07-03): relaying
# X-API-Key/Authorization/Cookie verbatim to an operator-defined sub-container image would leak the
# master API key to that container (a logging/compromised image → full API incl. console keystroke
# injection). The sub-container authenticates with its own creds, not the hub's.
#
# CASE-INSENSITIVE BY CONSTRUCTION, not by enumerating spellings. The filter used to pop each name
# three times — `h`, `h.title()`, `h.upper()` — which covers `x-api-key`, `X-Api-Key` and
# `X-API-KEY` but NOT `X-API-Key`, the spelling clients most often send. It was safe only because
# uvicorn lowercases header names before they reach ASGI, so the two extra pops were dead code and
# the guarantee lived in the SERVER rather than in this module. A credential filter must not be one
# deployment change away from leaking.
#
# The set also covers the full hop-by-hop list from RFC 9110 §7.6.1: `proxy-authorization` is another
# credential, and `te`/`trailer`/`upgrade`/`keep-alive` are per-connection headers that must not
# cross a proxy hop.
_STRIPPED_REQUEST_HEADERS = frozenset({
    "host", "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
    "proxy-authenticate", "proxy-authorization",
    "x-api-key", "authorization", "cookie",
})

# `aiter_bytes()` DECODES the body, so both of these become lies on the way back: `content-encoding`
# no longer describes the stream, and `content-length` is the COMPRESSED size. Bughunt 2026-07-03:
# the old code popped content-encoding but streamed still-encoded bytes while KEEPING content-length,
# so clients decoded garbage for any compressed upstream response.
_DROPPED_RESPONSE_HEADERS = frozenset({
    "transfer-encoding", "connection", "content-encoding", "content-length",
})


def _safe_request_headers(headers) -> dict[str, str]:
    """What may be forwarded to a sub-container. Extracted so it can be tested by CALLING it."""
    return {
        name: value for name, value in headers.items()
        if name.lower() not in _STRIPPED_REQUEST_HEADERS
    }


def _safe_query_params(params) -> dict[str, str]:
    """The same credential arrives by URL — `?api_key=` — so it is dropped there too."""
    return {name: value for name, value in params.items() if name.lower() != "api_key"}


def _safe_response_headers(headers) -> dict[str, str]:
    return {
        name: value for name, value in headers.items()
        if name.lower() not in _DROPPED_RESPONSE_HEADERS
    }


async def proxy_request(request: Request, target_url: str) -> StreamingResponse:
    """
    Forward an HTTP request to a target URL, streaming the response back.
    Preserves method, headers, body, query params, and streaming.
    """
    client = get_client()

    headers = _safe_request_headers(request.headers)
    query_params = _safe_query_params(request.query_params)

    body = await request.body()

    req = client.build_request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body if body else None,
        params=query_params,
    )

    response = await client.send(req, stream=True)

    # Case-insensitive on the way back too, and for a sharper reason than on the way out: these
    # headers came from an operator-defined sub-container, which is the side whose casing this hub
    # controls least. httpx normalises to lowercase today; the filter no longer depends on that.
    resp_headers = _safe_response_headers(response.headers)

    async def stream_body():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            try:
                await response.aclose()
            except Exception:
                pass  # Client may have disconnected

    return StreamingResponse(
        stream_body(),
        status_code=response.status_code,
        headers=resp_headers,
    )
