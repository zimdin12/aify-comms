"""CodexAdapter — Python mirror of mcp/stdio/adapters/codex.js.

Adds AIFY_CODEX_APP_SERVER_URL to diagnostic_env() for parity with the JS side.
"""

from __future__ import annotations

import os

from .base import RuntimeAdapter


class CodexAdapter(RuntimeAdapter):
    name = "codex"
    display_name = "Codex"
    session_env_vars = ["CODEX_THREAD_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_CODEX_APP_SERVER_URL", "").strip()
        env["AIFY_CODEX_APP_SERVER_URL"] = val if val else "(unset)"
        return env
