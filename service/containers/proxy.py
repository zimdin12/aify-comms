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


async def proxy_request(request: Request, target_url: str) -> StreamingResponse:
    """
    Forward an HTTP request to a target URL, streaming the response back.
    Preserves method, headers, body, query params, and streaming.
    """
    client = get_client()

    headers = dict(request.headers)
    # Strip hop-by-hop AND the hub's OWN credentials before forwarding (bughunt
    # 2026-07-03): relaying X-API-Key/Authorization/Cookie verbatim to an operator-defined
    # sub-container image would leak the master API key to that container (a
    # logging/compromised image → full API incl. console keystroke injection). The
    # sub-container authenticates with its own creds, not the hub's.
    for h in ["host", "transfer-encoding", "connection", "x-api-key", "authorization", "cookie"]:
        headers.pop(h, None)
        headers.pop(h.title(), None)
        headers.pop(h.upper(), None)

    # Drop the ?api_key= query param too (same leak via the URL).
    query_params = {k: v for k, v in request.query_params.items() if k.lower() != "api_key"}

    body = await request.body()

    req = client.build_request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body if body else None,
        params=query_params,
    )

    response = await client.send(req, stream=True)

    resp_headers = dict(response.headers)
    # aiter_bytes() DECODES the body (gzip/br), so we must drop BOTH content-encoding
    # AND content-length (the latter is the COMPRESSED size and would be wrong for the
    # decoded stream). Bughunt 2026-07-03: the old code popped content-encoding but
    # streamed aiter_raw() (still-encoded) while KEEPING content-length → the client
    # decoded garbage for any compressed upstream response.
    for h in ["transfer-encoding", "connection", "content-encoding", "content-length"]:
        resp_headers.pop(h, None)
        resp_headers.pop(h.title(), None)

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
