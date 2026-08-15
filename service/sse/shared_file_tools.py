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


async def comms_files() -> str:
    """List all shared artifacts."""
    r = await _api("GET", "/shared")
    files = r.get("files", [])
    if not files:
        return "No shared artifacts."
    lines = [
        f"- {f['name']} ({f.get('size', 0)}B, from: {f.get('from', '?')}, {f.get('sharedAt', '')})"
        + (f" -- {f['description']}" if f.get("description") else "")
        for f in files
    ]
    return "\n".join(lines)


#: Registered in the order they were declared in the transport. Named explicitly rather than swept
#: out of `globals()`, so a future helper that happens to be a coroutine cannot become an
#: agent-callable tool by accident.
TOOLS = (comms_share, comms_read, comms_files)


def register(mcp_server) -> None:
    """Apply `@mcp_server.tool()` to each tool, where the declarations used to stand."""
    for tool in TOOLS:
        mcp_server.tool()(tool)
