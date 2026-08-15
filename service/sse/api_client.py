"""The loopback REST client every `comms_*` SSE tool calls.

Extracted from `mcp/sse_server.py` in v0.5.4 — a layer-0 leaf: it calls nothing else that file
declares, only `service.config` and `httpx`. It is the single point where the SSE transport talks to
the service, so it is also the single place an auth-header or timeout change has to be made, which
is the argument for it having a name rather than sitting in the middle of a 730-line tool registry.

The bodies below are byte-identical to the ones that stood in `sse_server.py`; only the private
names became public (`_api_url` -> `api_url`, `_api` -> `api`). The transport imports them back
under their original private aliases, so all twenty-six call sites and the test that swaps `_api`
for a canned payload are untouched by the move.
"""

from __future__ import annotations

import httpx

from service.config import get_config

_BASE_URL = None


def api_url():
    global _BASE_URL
    if _BASE_URL is None:
        cfg = get_config()
        _BASE_URL = f"http://127.0.0.1:{cfg.port}/api/v1"
    return _BASE_URL


async def api(method: str, path: str, json_data: dict = None, params: dict = None) -> dict:
    """Call the internal REST API."""
    url = f"{api_url()}{path}"
    headers = {}
    cfg = get_config()
    if cfg.api_key:
        headers["X-API-Key"] = cfg.api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers, params=params)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=json_data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers, params=params)
        else:
            return {"error": f"Unknown method: {method}"}
        try:
            return resp.json()
        except Exception:
            return {"status": resp.status_code, "text": resp.text[:500]}
