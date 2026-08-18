"""Sharing artifacts between agents: write one, read one, list them.

Extracted from `mcp/sse_server.py` in v0.5.4 — a whole subject, bodies untouched.

`comms_share` IS THE ONE TOOL IN THE TRANSPORT THAT DOES NOT GO THROUGH `_api`. The shared-file
endpoint takes form-encoded data rather than JSON, so it builds the request itself — which is why
this module imports `httpx` and `get_config` while its two neighbours need neither, and why the
transport's own `import httpx` becomes dead the moment this file exists. That import survived an
earlier slice only because the sweep caught me deleting it while `comms_share` was still using it,
400 lines below.

`comms_read` decides whether a payload has readable content or is a binary the server is holding,
and says which. That is a conclusion an agent acts on, so it is tested rather than assumed.

Patch `_api` HERE, not on the transport: these resolve it from this module.
"""

from __future__ import annotations

import httpx

from service.config import get_config
from service.sse.api_client import api as _api, api_url as _api_url


async def comms_share(from_agent: str, name: str, content: str, description: str = "") -> str:
    """Share an artifact (code, results, text) with other agents."""
    # Use form-encoded data to match the API
    url = f"{_api_url()}/shared"
    headers = {}
    cfg = get_config()
    if cfg.api_key:
        headers["X-API-Key"] = cfg.api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, data={
            "from_agent": from_agent, "name": name, "content": content, "description": description,
        })
        r = resp.json()
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f'Shared "{r.get("name", name)}" ({r.get("size", 0)} bytes).'


async def comms_read(name: str) -> str:
    """Read a shared artifact by name."""
    r = await _api("GET", f"/shared/{name}")
    if "detail" in r:
        return f"Error: {r['detail']}"
    if r.get("content"):
        meta = r.get("meta", {})
        header = f"From: {meta.get('from', '?')} | {meta.get('sharedAt', '')}" if meta.get("from") else ""
        if meta.get("description"):
            header += f" | {meta['description']}"
        return (header + "\n\n" + r["content"]) if header else r["content"]
    return f'"{name}" -- binary file on server.'


async def comms_files(query: str = "", fromAgent: str = "", limit: int = 50) -> str:
    """List shared artifacts.

    BOUNDED, and it did not used to be. This tool took NO parameters and returned every artifact the
    fleet had ever shared — measured live on 2026-08-18 at 333 files / 87,014 characters, which the
    caller's harness refused to inline. An unbounded list is not a listing, it is a claim on the
    agent's own context: the reply crowds out the work it was supposed to inform.

    `limit` caps it, `query` matches name or description, `fromAgent` filters by sharer. The reply
    always says how many were withheld, for the same reason `comms_search` says what it searched — a
    truncated list that does not admit it is truncated reads as "that is everything".
    """
    r = await _api("GET", "/shared")
    if "detail" in r:
        return f"Error: {r['detail']}"
    files = r.get("files", [])
    total = len(files)
    needle = (query or "").strip().lower()
    sharer = (fromAgent or "").strip().lower()
    if sharer:
        files = [f for f in files if str(f.get("from", "")).strip().lower() == sharer]
    if needle:
        files = [
            f for f in files
            if needle in str(f.get("name", "")).lower() or needle in str(f.get("description", "")).lower()
        ]
    matched = len(files)
    try:
        capped = max(1, int(limit))
    except (TypeError, ValueError):
        capped = 50
    shown = files[:capped]
    if not shown:
        # The total is only worth saying when a FILTER hid things — "0 matched, 333 exist" is the
        # useful sentence. On a genuinely empty store it is noise, and an existing test says so.
        if needle or sharer:
            return f"No shared artifacts matching that filter. ({total} shared in total.)"
        return "No shared artifacts."
    lines = [
        f"- {f['name']} ({f.get('size', 0)}B, from: {f.get('from', '?')}, {f.get('sharedAt', '')})"
        + (f" -- {f['description']}" if f.get("description") else "")
        for f in shown
    ]
    footer = f"\n\n(showing {len(shown)} of {matched} matched; {total} shared in total)"
    if matched > len(shown):
        footer += " — narrow with query=/fromAgent= or raise limit="
    return "\n".join(lines) + footer


async def comms_unshare(name: str, requestedBy: str) -> str:
    """Delete a shared artifact you shared.

    There was NO tool for this until 2026-08-18 — the endpoint existed, but the only agent-reachable
    way to remove an artifact was `comms_clear(target="shared")`, which wipes every artifact on the
    hub for every team. A per-item delete missing while a fleet-wide wipe is one call away is how an
    agent tidying up destroys somebody else's work.

    Only the sharer or an operator surface may delete; the service enforces it.
    """
    r = await _api("DELETE", f"/shared/{name}", params={"requestedBy": requestedBy})
    if "detail" in r:
        return f"Error: {r['detail']}"
    return f'Deleted shared artifact "{name}".'


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_share, comms_read, comms_files, comms_unshare)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
