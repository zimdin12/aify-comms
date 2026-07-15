"""Resolve the operator-facing Dashboard Next URL for legacy entry points."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

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
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = os.environ.get("AIFY_DASHBOARD_PORT", "8801").strip() or "8801"
    scheme = request.url.scheme or "http"
    return urlunsplit((scheme, f"{host}:{port}", "/", "", ""))
