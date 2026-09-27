"""ClaudeAdapter — Python mirror of mcp/stdio/adapters/claude.js.

Capability values per Plan 2 spec; everything else inherited from the base.
"""

from __future__ import annotations

import re

from .base import RuntimeAdapter


class ClaudeAdapter(RuntimeAdapter):
    name = "claude-code"
    session_env_vars = ["CLAUDE_SESSION_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    preferred_delivery_mode = "managed-via-wrapper"
    #: Read from Claude Code 2.1.283's own command table (the installed claude.exe):
    #: `{type:"local",name:"compact",description:"Free up context by summarizing the conversation
    #: so far",...}`.
    native_compact_command = "/compact"

    #: Claude Code truncates a project directory name longer than this and appends a hash of the
    #: path, which is not reproduced here; past it the brief names the file without the directory.
    _PROJECT_DIR_MAX = 200

    def transcript_location(self, session_handle, workspace) -> str:
        # `~/.claude/projects/<cwd, every non-alphanumeric as "->/<session id>.jsonl`: the rule in
        # claude.exe 2.1.283 (`e.replace(/[^a-zA-Z0-9]/g,"-")`) and in runtimes-claude.js
        # `claudeSessionTranscriptPath`, which the bridge already reads transcripts through.
        handle = str(session_handle or "").strip()
        if not handle:
            return ""
        project = re.sub(r"[^a-zA-Z0-9]", "-", str(workspace or "").strip())
        if not project or len(project) > self._PROJECT_DIR_MAX:
            return f"{handle}.jsonl under ~/.claude/projects/"
        return f"~/.claude/projects/{project}/{handle}.jsonl"

    def resume_command(self, session_id, agent_id="") -> str:
        # Mirror mcp/stdio/adapters/claude.js resumeCommand. The agent id is
        # REQUIRED when known: without it the wrapper cannot export AIFY_AGENT_ID and every
        # turn-state path (detector + hooks) silently no-ops, latching the agent's status.
        aid = str(agent_id or "").strip()
        if aid:
            return f"claude-aify --aify-agent {aid} --resume {session_id}"
        return f"claude-aify --resume {session_id}"

    def console_argv(self, *, agent_id: str, handle: str, interactive: bool) -> list[str]:
        # `--auto` only outside interactive: a console an operator is typing into must not also be
        # answering for itself.
        parts = ["claude-aify", "--aify-agent", agent_id]
        if not interactive:
            parts.append("--auto")
        if handle:
            parts.extend(["--resume", handle])
        return parts

    def is_resident_ready(self, runtime_config: dict) -> bool:
        # Restores Plan 2 Task 14 dropped gate (#120).
        if not runtime_config:
            return False
        return runtime_config.get("channelEnabled") is True
