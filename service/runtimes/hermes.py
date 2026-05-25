"""HermesAdapter — Python mirror of mcp/stdio/adapters/hermes.js."""

from __future__ import annotations

import os

from .base import RuntimeAdapter


class HermesAdapter(RuntimeAdapter):
    name = "hermes"
    display_name = "Hermes"
    session_env_vars = ["HERMES_SESSION_ID", "HERMES_SESSION"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        env["AIFY_HERMES_GATEWAY_URL"] = val if val else "(unset)"
        return env
