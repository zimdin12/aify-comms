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
    #: A managed codex console is the `codex --remote` TUI in front of the wrapper's app-server. Read
    #: from codex-cli 0.157.0's own slash popup in an isolated TUI: `/compact  summarize conversation
    #: to prevent hitting the context limit`. The TUI runs it as the app-server's
    #: `thread/compact/start`, which the same binary answers with a `contextCompaction` turn.
    native_compact_command = "/compact"

    def transcript_location(self, session_handle, workspace) -> str:
        # `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread id>.jsonl` as codex 0.157.0
        # writes it, and `${CODEX_HOME:-$HOME/.codex}` is how codex-aify reads that root. The date is
        # the thread's, which the service does not record, so the file is named by its suffix.
        handle = str(session_handle or "").strip()
        if not handle:
            return ""
        return f"the rollout file ending -{handle}.jsonl under $CODEX_HOME/sessions (default ~/.codex/sessions)"

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
