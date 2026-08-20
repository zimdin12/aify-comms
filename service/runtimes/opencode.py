"""OpencodeAdapter — Python mirror of mcp/stdio/adapters/opencode.js.

aify-comms currently spawns the opencode CLI directly without using
`opencode serve` as a persistent resident surface. The managed controller's
per-run server supports native promptAsync injection while a turn is active.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class OpencodeAdapter(RuntimeAdapter):
    name = "opencode"
    display_name = "OpenCode"
    session_env_vars = ["OPENCODE_SESSION_ID", "OPENCODE_SESSION"]
    supports_resident = False
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = False
    preferred_delivery_mode = "managed"

    # Plan 3 additions
    wrapper_name = "opencode"

    def resume_command(self, session_id, agent_id="") -> str:
        # Mirror mcp/stdio/adapters/opencode.js resumeCommand. The agent id is
        # REQUIRED when known: without it the wrapper cannot export AIFY_AGENT_ID and every
        # turn-state path (detector + hooks) silently no-ops, latching the agent's status.
        aid = str(agent_id or "").strip()
        if aid:
            return f"opencode-aify --aify-agent {aid} --resume {session_id}"
        return f"opencode-aify --resume {session_id}"

    def console_argv(self, *, agent_id: str, handle: str, interactive: bool) -> list[str]:
        return ["opencode"]
