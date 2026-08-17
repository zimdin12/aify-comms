"""Resolve the operator-facing Dashboard Next URL for legacy entry points."""

from __future__ import annotations

import os
from urllib.parse import urlunsplit

from fastapi import Request


def dashboard_url(request: Request) -> str:
    """Return the configured new-dashboard URL, or derive it from the request host.

    ``AIFY_DASHBOARD_URL`` is authoritative for reverse proxies/custom ports. The
    fallback preserves the incoming scheme and hostname while replacing the API
    port with ``AIFY_DASHBOARD_PORT`` (8801 by default).
    """

    configured = os.environ.get("AIFY_DASHBOARD_URL", "").strip()
    if configured:
        return configured.rstrip("/") + "/"

    host = request.url.hostname or "localhost"
    # `url.hostname` STRIPS the brackets from an IPv6 authority (`[::1]:8800` -> `::1`), so they
    # have to go back on or the reassembled URL reads as `http://::1:8801/`, which no browser will
    # follow. The `startswith("[")` half is DEFENSIVE ONLY and is unreachable from this caller for
    # the same reason — kept as one condition standing between a changed Starlette contract and a
    # broken link. `test_dashboard_redirect.py` records that a mutation removing it survives.
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("AIFY_DASHBOARD_PORT", "8801").strip() or "8801"
    scheme = request.url.scheme or "http"
    return urlunsplit((scheme, f"{host}:{port}", "/", "", ""))
