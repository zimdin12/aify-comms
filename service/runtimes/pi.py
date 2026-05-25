"""PiAdapter — Python mirror of mcp/stdio/adapters/pi.js.

Capability declarations encode the Plan 2 pi delivery flip: resident is
False because omp --mode rpc is single-client stdio (no multi-client
gateway). preferred_delivery_mode is managed-via-wrapper so the dispatch
router pins pi to the unified wrapper-backing path.
"""

from __future__ import annotations

from .base import RuntimeAdapter


class PiAdapter(RuntimeAdapter):
    name = "pi"
    display_name = "Pi"
    session_env_vars = ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    supports_resident = False
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = False
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "pi-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive:
            return f"pi-aify --aify-agent {agent_id}"
        parts = ["pi-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)
