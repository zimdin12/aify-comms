"""HermesAdapter — Python mirror of mcp/stdio/adapters/hermes.js."""

from __future__ import annotations

import re

from .base import RuntimeAdapter


class HermesAdapter(RuntimeAdapter):
    name = "hermes"
    session_env_vars = ["HERMES_SESSION_ID", "HERMES_SESSION"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    preferred_delivery_mode = "managed-via-wrapper"
    #: `/compress` is the canonical name in both hermes front ends, read in the hermes-agent checkout
    #: (<hermes-agent> below): the command registry (<hermes-agent>/hermes_cli/commands.py:81,
    #: `CommandDef("compress", ..., aliases=("compact",))`) and the Ink TUI
    #: (<hermes-agent>/ui-tui/src/app/slash/commands/session.ts:241, `name: 'compress'`, with no
    #: `compact` alias). Hermes refuses it itself while a turn runs
    #: (<hermes-agent>/tui_gateway/methods_session.py, `busy_message("compress")`).
    native_compact_command = "/compress"

    def transcript_location(self, session_handle, workspace) -> str:
        # Hermes keeps sessions in its state database rather than a file per session; its CLI writes
        # one out: `sessions export <output> [--session-id ID]`
        # (<hermes-agent>/hermes_cli/console_engine.py:826).
        handle = str(session_handle or "").strip()
        if not handle:
            return ""
        return f"not a file; write it out with `hermes sessions export <file> --session-id {handle}`"

    def resume_command(self, session_id, agent_id="") -> str:
        # The aify-aware way to reopen an agent is the wrapper: hermes-aify's
        # --resume recovery maps the real session id back to its agent and
        # resumes that real session via the gateway-host.
        aid = str(agent_id or "").strip()
        if aid:
            return f"hermes-aify --aify-agent {aid} --resume {session_id}"
        return f"hermes-aify --resume {session_id}"

    def console_argv(self, *, agent_id: str, handle: str, interactive: bool) -> list[str]:
        parts = ["hermes-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return parts

    def is_resident_ready(self, runtime_config: dict) -> bool:
        if not runtime_config:
            return False
        gw = str(runtime_config.get("gatewayUrl", "")).strip()
        return bool(re.match(r"^wss?://", gw, re.IGNORECASE))
