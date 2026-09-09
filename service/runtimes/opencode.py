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
    #
    # THE WRAPPER, NOT THE RUNTIME BINARY, and this said `opencode` until 2026-09-09. Every other
    # part of the product names `opencode-aify`: this adapter's own `resume_command` below,
    # `mcp/stdio/adapters/opencode.js`, the map at the top of `runtimes.js`, and that file's
    # `AIFY_OPENCODE_AIFY_COMMAND` default. The contract test could not see the disagreement
    # because it asked whether the resume command STARTS WITH this value, and `opencode-aify`
    # starts with `opencode` -- a prefix satisfies the check the shorter string was wrong about.
    #
    # LATENT RATHER THAN LIVE: nothing in `service/` reads `wrapper_name` at all, so the wrong
    # value changed no behaviour. That is the state this repo records as a harmless looseness
    # waiting for something to lean on it, so it is corrected rather than left for the reader that
    # eventually arrives.
    wrapper_name = "opencode-aify"

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
