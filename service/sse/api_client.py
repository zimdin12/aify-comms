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
        # FAIL CLOSED, NOT INTO A CONFIDENT EMPTY. Reported by a reviewer on another instance
        # 2026-08-18. This used to return the parsed JSON whatever the status, and
        # `{"status":…, "text":…}` when the body would not parse — neither of which carries the
        # `detail` key every caller in this package checks (`if "detail" in r: return f"Error: …"`).
        # So a 500, a proxy's HTML error page or a dropped upstream rendered as `r.get("messages", [])`
        # -> `[]` -> "Inbox empty." to the agent, and "No agents registered", and "No results". An
        # agent then ACTS on an absence that was really an outage — the worst possible failure for a
        # tool whose whole job is telling an agent what it has been asked to do.
        #
        # Returning an error SHAPE fixes every caller without touching one, because they already
        # branch on `detail`. Note the JSON case is not the only one: a 500 whose body parses fine
        # was equally silent before, since nothing looked at the status code at all.
        # ADDITIVE, deliberately. The `{"status":…, "text":…}` pair is kept exactly where it was —
        # four tests pin it with reasons that still hold (a 204 must not raise; a proxy's 502 must
        # surface as what happened; the text must stay truncated because an agent reads it) — and the
        # only change is that an error now ALSO carries `detail`. Rewriting the shape instead would
        # have churned every caller and every test for a signal that could simply be added.
        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("detail"):
            return payload                      # the service explained itself; nothing to add
        if resp.status_code >= 400:
            body = (resp.text or "").strip()
            explanation = f"HTTP {resp.status_code} from {path}"
            error = {"detail": f"{explanation}: {body[:300]}" if body else explanation,
                     "status": resp.status_code, "text": resp.text[:500]}
            # A JSON error body that simply used a different key (`{"error": "db locked"}`) keeps its
            # own fields — they are what the service chose to say.
            return {**payload, **error} if isinstance(payload, dict) else error
        if payload is None:
            body = (resp.text or "").strip()
            if not body:
                # A no-content success: 204 from a DELETE. Adding `detail` here would report every
                # successful deletion as a failure to the agent that asked for it.
                return {"status": resp.status_code, "text": resp.text[:500]}
            # 2xx that did not parse and was not empty: something answered that is not this API — a
            # proxy, a captive portal, a login page. That must not read as success either.
            return {"detail": f"HTTP {resp.status_code} from {path}: non-JSON response: {body[:300]}",
                    "status": resp.status_code, "text": resp.text[:500]}
        return payload
