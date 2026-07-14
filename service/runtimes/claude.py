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

    def resume_command(self, session_id, agent_id="") -> str:
        # Mirror mcp/stdio/adapters/claude.js resumeCommand. The agent id is
        # REQUIRED when known: without it the wrapper cannot export AIFY_AGENT_ID and every
        # turn-state path (detector + hooks) silently no-ops, latching the agent's status.
        aid = str(agent_id or "").strip()
        if aid:
            return f"claude-aify --aify-agent {aid} --resume {session_id}"
        return f"claude-aify --resume {session_id}"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive and handle:
            return f"claude-aify --aify-agent {agent_id} --resume {handle}"
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

    async def discover_session_id(self) -> str | None:
        # Session discovery for claude is bridge-side ONLY (the JS adapter,
        # mcp/stdio/adapters/claude.js, which scopes discovery to the agent's
        # own cwd). This Python path is never invoked in service/ flow; it
        # deliberately returns None rather than re-implementing a machine-global
        # transcript scan that would cross-agent-contaminate — exactly what the
        # JS adapter was rewritten to forbid.
        return None
