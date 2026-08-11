"""
Health and service info endpoints.
Used by Docker healthchecks and by AI agents to discover the service.
"""

import asyncio
import time
import urllib.request
import json as _json
import logging

from fastapi import APIRouter, Request
from service.config import get_config

router = APIRouter(tags=["health"])

logger = logging.getLogger(__name__)

# GitHub compare endpoint. Surfaces "N commits behind origin/main" as a WARNING
# (the container has no .git / docker socket to rebuild itself — see the
# version-awareness plan). 60 req/hr unauth limit, so the result is cached.
_GITHUB_COMPARE_URL = "https://api.github.com/repos/zimdin12/aify-comms/compare/{sha}...main"
_UPDATE_TTL_SECONDS = 20 * 60  # ~20 min

# Module-level cache + injectable comparer (the test swaps in a stub so it never
# hits the network).
_update_cache: dict | None = None
_update_cache_at: float = 0.0
_update_comparer = None  # Optional[Callable[[str], dict]]


def set_update_comparer(fn):
    """Inject the comparer (testing seam). ``fn(sha) -> {behind_by, ahead_by, status}``."""
    global _update_comparer
    _update_comparer = fn
    _reset_update_cache()


def _reset_update_cache():
    """Clear the cached behind-count (used between tests)."""
    global _update_cache, _update_cache_at
    _update_cache = None
    _update_cache_at = 0.0


def _github_compare(sha: str) -> dict:
    """Call the GitHub compare API. Raises on any network/HTTP error."""
    url = _GITHUB_COMPARE_URL.format(sha=sha)
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "aify-comms"})
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (fixed host)
        payload = _json.loads(resp.read().decode("utf-8"))
    return {
        "behind_by": payload.get("behind_by"),
        "ahead_by": payload.get("ahead_by"),
        "status": payload.get("status"),
    }


def _check_update(sha: str) -> dict:
    """Cached behind-count. NEVER raises — any failure → behind_by=null.

    Returns a dict with ``behind_by`` / ``ahead_by`` / ``status`` / ``source``
    / ``stale``. ``behind_by`` is null when the SHA is unknown or the network
    call fails (offline / 403 rate-limited / 404 unknown sha).
    """
    global _update_cache, _update_cache_at
    now = time.time()
    if _update_cache is not None and (now - _update_cache_at) < _UPDATE_TTL_SECONDS:
        return _update_cache

    result = {
        "behind_by": None,
        "ahead_by": None,
        "status": None,
        "source": "github-compare",
        "stale": True,
    }
    if sha and sha != "unknown":
        comparer = _update_comparer or _github_compare
        try:
            cmp = comparer(sha)
            result["behind_by"] = cmp.get("behind_by")
            result["ahead_by"] = cmp.get("ahead_by")
            result["status"] = cmp.get("status")
            result["stale"] = result["behind_by"] is None
        except Exception as e:  # offline / 403 / 404 / parse — never propagate
            logger.info(f"version update check failed: {e}")

    _update_cache = result
    _update_cache_at = now
    return result


@router.get("/health")
async def health():
    """Health check endpoint. Returns 200 if service is running.

    The `ntfy` block is v0.4 C4, and it is here because review pointed out that a send-failure
    counter cannot see the failure that actually matters: a drain worker that has stopped produces
    NO failures at all — the queue simply fills until the bound starts shedding alerts, with the
    counter reading zero the whole time. So liveness and queue depth are reported alongside it, and
    shed alerts are named rather than silent.

    It carries no URL, redacted or otherwise (C6: the topic URL grants read AND publish).
    `test_ntfy_relay.py::test_health_never_contains_the_url` asserts that.
    """
    from service.ntfy import get_relay

    return {"status": "healthy", "ntfy": get_relay().health()}


@router.get("/version")
async def version():
    """Build identity + a behind-count WARNING block (never raises).

    Served at ``/version`` (the health router has no prefix). Fields:
    name/version/sha/sha_short/branch/built_at + an ``update`` block whose
    ``behind_by`` is null when offline / rate-limited / unknown sha.
    """
    config = get_config()
    # _check_update may do a blocking GitHub call on a cache miss — run it in a worker thread
    # so a slow/unreachable GitHub never stalls the event loop (this route is unauthenticated).
    update = await asyncio.to_thread(_check_update, config.build_sha)
    return {
        "name": config.name,
        "version": config.version,
        "sha": config.build_sha,
        "sha_short": config.build_short,
        "branch": config.build_branch,
        "built_at": config.built_at,
        "update": update,
    }


@router.get("/ready")
async def ready(request: Request):
    """Readiness check. Verifies all components are initialized."""
    checks = {}
    manager = getattr(request.app.state, "container_manager", None)
    if manager is not None:
        checks["container_manager"] = "initialized"
        checks["docker"] = "connected" if manager.docker else "unavailable"
    return {"status": "ready", "checks": checks}


@router.get("/info")
async def info(request: Request):
    """
    Service discovery endpoint for AI agents.
    Returns everything an agent needs to use this service.
    """
    config = get_config()

    # Use request host for URLs so they work from other containers/machines
    host = request.headers.get("host", f"localhost:{config.port}")
    base = f"http://{host}"

    response = {
        "name": config.name,
        "version": config.version,
        "description": config.description,
        "endpoints": {
            "api": f"{base}/api/v1",
            "docs": f"{base}/docs",
            "openapi": f"{base}/openapi.json",
            "health": f"{base}/health",
            "ready": f"{base}/ready",
        },
        "integrations": {
            "mcp_sse": f"{base}{config.mcp_path_prefix}/sse" if config.mcp_enabled else None,
            "mcp_stdio": "See mcp/stdio/ directory for host-side MCP server",
            "codex_skill": "See .agents/skills/aify-comms/SKILL.md",
            "claude_code_skill": "See .claude/skills/aify-comms/SKILL.md",
            "environment_bridge": "Run the installed aify-comms launcher on each host/WSL environment",
        },
    }

    manager = getattr(request.app.state, "container_manager", None)
    if manager is not None:
        response["endpoints"]["containers"] = f"{base}/api/v1/containers"
        response["endpoints"]["gpu"] = f"{base}/api/v1/gpu"
        response["endpoints"]["route"] = f"{base}/route/{{container_name}}/{{path}}"
        response["containers"] = manager.list_containers()
        response["groups"] = manager.get_groups()

    return response
