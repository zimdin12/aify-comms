"""CodexAdapter — Python mirror of mcp/stdio/adapters/codex.js."""

from __future__ import annotations

from .base import RuntimeAdapter


class CodexAdapter(RuntimeAdapter):
    name = "codex"
    session_env_vars = ["CODEX_THREAD_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    preferred_delivery_mode = "managed-via-wrapper"

    def resume_command(self, session_id, agent_id="") -> str:
        # Mirror mcp/stdio/adapters/codex.js resumeCommand. The agent id is
        # REQUIRED when known: without it the wrapper cannot export AIFY_AGENT_ID and every
        # turn-state path (detector + hooks) silently no-ops, latching the agent's status.
        aid = str(agent_id or "").strip()
        if aid:
            return f"codex-aify --aify-agent {aid} --resume {session_id}"
        return f"codex-aify --resume {session_id}"

    def console_argv(self, *, agent_id: str, handle: str, interactive: bool) -> list[str]:
        parts = ["codex-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return parts
