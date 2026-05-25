"""ClaudeAdapter — Python mirror of mcp/stdio/adapters/claude.js.

Capability values per Plan 2 spec; everything else inherited from the base.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class ClaudeAdapter(RuntimeAdapter):
    name = "claude-code"
    display_name = "Claude Code"
    session_env_vars = ["CLAUDE_SESSION_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "claude-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive:
            return f"claude-aify --aify-agent {agent_id}"
        parts = ["claude-aify", "--aify-agent", agent_id, "--auto"]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Restores Plan 2 Task 14 dropped gate (#120).
        if not runtime_config:
            return False
        return runtime_config.get("channelEnabled") is True
