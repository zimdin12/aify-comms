"""HermesAdapter — Python mirror of mcp/stdio/adapters/hermes.js."""

from __future__ import annotations

import os
import re

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

    # Plan 3 additions
    wrapper_name = "hermes-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        parts = ["hermes-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        if not runtime_config:
            return False
        gw = str(runtime_config.get("gatewayUrl", "")).strip()
        return bool(re.match(r"^wss?://", gw, re.IGNORECASE))

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        env["AIFY_HERMES_GATEWAY_URL"] = val if val else "(unset)"
        return env
