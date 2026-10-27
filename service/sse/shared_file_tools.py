"""Shared artifacts: share text, read, list, and take back (port of `mcp/stdio/artifact-tools.mjs`, service
mode).

The service is not on the agent's machine, so it shares TEXT given as `content`; a file by `filePath` is
the local channel's (0.9.11 plan: the tools that touch the agent's machine). For the same reason a binary
artifact is described, not saved: there is no local path to hand back. The sharer and the one taking an
artifact back is the CALLER, from the request's header.
"""

from __future__ import annotations

from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import Field

from service.sse.api_client import ERROR_BODY_LIMIT, call, request
from service.sse.caller import caller_id
from service.sse.registration import register_tools
from service.sse.rendering import tool_error
from service.sse.send_tools import SAFE_NAME


MAX_FILES_LIMIT = 200


def _bad_name(name: str):
    if SAFE_NAME.match(name):
        return None
    return tool_error(f'Invalid name: must be 1-128 alphanumeric chars, dots, hyphens, underscores. Got: "{name}"')


async def comms_share(
    ctx: Context,
    name: str = Field(description="Artifact name (e.g. 'test-results.txt', 'screenshot.png')"),
    content: str | None = Field(default=None, description="Text content"),
    description: str | None = Field(default=None, description="Short description"),
):
    """Share a text artifact with other agents."""
    if refusal := _bad_name(name):
        return refusal
    if not content:
        return tool_error("Need content or filePath.")
    resp = await request("POST", "/shared", form={"from_agent": caller_id(ctx), "name": name, "description": description or "", "content": content})
    try:
        r = resp.json()
    except ValueError:
        r = {}
    if resp.status_code >= 300:
        return tool_error(f"Share failed (HTTP {resp.status_code}): {r.get('detail') or r.get('error') or 'server rejected the upload'}")
    return f'Shared "{r.get("name") or name}" on server.'


async def comms_read(name: str = Field(description="Artifact name to read")):
    """Read a shared artifact by name."""
    if refusal := _bad_name(name):
        return refusal
    resp = await request("GET", f"/shared/{quote(name, safe='')}")
    if resp.status_code == 404:
        return tool_error(f'Artifact "{name}" not found.')
    # Any other failure says what failed: "not found" for an outage sends the agent looking for a file that exists.
    if resp.status_code >= 300:
        detail = resp.text[:ERROR_BODY_LIMIT]
        try:
            parsed = resp.json()
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            detail = parsed.get("detail") or parsed.get("error") or detail
        return tool_error(f'Reading "{name}" failed (HTTP {resp.status_code}): {detail}')
    if "application/json" not in resp.headers.get("content-type", ""):
        return (f'Binary artifact "{name}" ({len(resp.content)} bytes). This service cannot hand you a local copy; '
                "read it from an agent that has a local comms process.")
    r = resp.json()
    if r.get("content"):
        meta = r.get("meta") or {}
        header = (f"From: {meta['from']} | {meta.get('sharedAt') or ''}{' | ' + meta['description'] if meta.get('description') else ''}\n\n"
                  if meta.get("from") else "")
        return header + r["content"]
    return f'"{name}" — empty or unreadable.'


def _files_listing(files: list, query: str | None, from_agent: str | None, limit: float | None) -> str:
    # BOUNDED AT BOTH ENDS: limit=100000 dumped the whole table into an agent's context (reported 2026-08-18).
    cap = min(MAX_FILES_LIMIT, max(1, int(limit) if limit is not None else 50))
    needle = (query or "").strip().lower()
    sharer = (from_agent or "").strip().lower()
    matched = [f for f in files if not sharer or str(f.get("from") or "").strip().lower() == sharer]
    if needle:
        matched = [f for f in matched if needle in str(f.get("name") or "").lower() or needle in str(f.get("description") or "").lower()]
    shown = matched[:cap]
    if not shown:
        return f"No shared artifacts matching that filter. ({len(files)} shared in total.)" if (query or from_agent) else "No shared artifacts."
    lines = [f"- {f.get('name')} ({f.get('size')}B, from: {f.get('from')}, {f.get('sharedAt')})"
             + (f" -- {f['description']}" if f.get("description") else "") for f in shown]
    more = " — narrow with query=/fromAgent= or raise limit=" if len(matched) > len(shown) else ""
    return f"{chr(10).join(lines)}\n\n(showing {len(shown)} of {len(matched)} matched; {len(files)} shared in total){more}"


async def comms_files(
    query: str | None = Field(default=None, description="Match against artifact name or description"),
    fromAgent: str | None = Field(default=None, description="Only artifacts shared by this agent"),
    limit: float | None = Field(default=None, description="Max artifacts to list (default 50)"),
):
    """List shared artifacts. Narrow with query or fromAgent; raise limit to see more."""
    return _files_listing((await call("GET", "/shared")).get("files") or [], query, fromAgent, limit)


async def comms_unshare(ctx: Context, name: str = Field(description="Artifact name to delete")):
    """Delete a shared artifact YOU shared, by name."""
    try:
        await call("DELETE", f"/shared/{quote(name, safe='')}?requestedBy={quote(caller_id(ctx), safe='')}")
    except Exception as error:  # noqa: BLE001 - worded as the JS tool words it
        return tool_error(f"Failed to delete artifact: {error}")
    return f'Deleted shared artifact "{name}".'


TOOLS = (comms_share, comms_read, comms_files, comms_unshare)


def register(mcp_server) -> None:
    register_tools(mcp_server, TOOLS)
