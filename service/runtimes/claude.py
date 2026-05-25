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
