"""PiAdapter — Python mirror of mcp/stdio/adapters/pi.js.

Capability declarations encode the Pi delivery model: resident is False
because omp --mode rpc is single-client stdio (no multi-client gateway).
Managed dispatch stays on the native persistent RPC controller so dashboard
chat and Console attach to the same synthesized terminal stream.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class PiAdapter(RuntimeAdapter):
    name = "pi"
    session_env_vars = ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    supports_resident = False
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    preferred_delivery_mode = "managed"

    def resume_command(self, session_id, agent_id="") -> str:
        # Mirror mcp/stdio/adapters/pi.js resumeCommand. The agent id is
        # REQUIRED when known: without it the wrapper cannot export AIFY_AGENT_ID and every
        # turn-state path (detector + hooks) silently no-ops, latching the agent's status.
        aid = str(agent_id or "").strip()
        if aid:
            return f"pi-aify --aify-agent {aid} --resume {session_id}"
        return f"pi-aify --resume {session_id}"

    def console_argv(self, *, agent_id: str, handle: str, interactive: bool) -> list[str]:
        parts = ["pi-aify", "--aify-agent", agent_id]
        # Interactive pi never resumes - the 026H trap. Kept as a branch on the LIST rather than an
        # early-returned string, so the two forms cannot disagree about it.
        if handle and not interactive:
            parts.extend(["--resume", handle])
        return parts
