"""The loopback REST client every `comms_*` tool the service serves calls.

A leaf: it calls only `service.config` and `httpx`. It is the single point where the MCP tools talk to
the service, so it is also the single place an auth-header or timeout change has to be made.
"""

from __future__ import annotations

import httpx

from service.config import get_config

_BASE_URL = None
#: Characters of an error body a tool reports, in both copies (`mcp/stdio/aify-service-endpoint.mjs`).
ERROR_BODY_LIMIT = 300


def api_url():
    global _BASE_URL
    if _BASE_URL is None:
        cfg = get_config()
        _BASE_URL = f"http://127.0.0.1:{cfg.port}/api/v1"
    return _BASE_URL


class ServiceError(Exception):
    """A non-2xx answer, worded as the JS bridge's `httpCall` throws it: `HTTP <status>: <body>`."""

    def __init__(self, response: httpx.Response):
        # Cut short, as the JS cuts it: an agent reads this, and a proxy's error page would flood it.
        super().__init__(f"HTTP {response.status_code}: {response.text[:ERROR_BODY_LIMIT]}")
        self.status = response.status_code
        try:
            self.payload = response.json()
        except ValueError:
            self.payload = {}


async def request(method: str, path: str, *, body: dict | None = None, form: dict | None = None) -> httpx.Response:
    """One request to this service's API, as it answered. `path` carries its own query string."""
    headers = {}
    cfg = get_config()
    if cfg.api_key:
        headers["X-API-Key"] = cfg.api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await client.request(method, f"{api_url()}{path}", headers=headers, json=body, data=form)


async def call(method: str, path: str, body: dict | None = None, *, form: dict | None = None) -> dict:
    """The JS `httpCall` (0.9.11 port): the parsed answer, or `ServiceError` on any non-2xx.

    A tool that does not catch the error reports it as its result, the way the JS SDK reports a thrown
    error (`registration.register_tools`).
    """
    resp = await request(method, path, body=body, form=form)
    if resp.status_code >= 300:
        raise ServiceError(resp)
    return resp.json() if resp.text.strip() else {}
