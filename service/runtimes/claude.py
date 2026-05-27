"""ClaudeAdapter — Python mirror of mcp/stdio/adapters/claude.js.

Capability values per Plan 2 spec; everything else inherited from the base.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import RuntimeAdapter

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


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
        """Plan 4 (2026-05-25): claude stores transcripts at
        ~/.claude/projects/<encoded-cwd>/<uuid>.jsonl. Returns newest .jsonl's
        uuid across all project subdirs.
        """
        root = Path.home() / ".claude" / "projects"
        try:
            projects = list(root.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return None
        if not projects:
            return None

        newest: tuple[Path, float] | None = None
        for proj in projects:
            if not proj.is_dir():
                continue
            try:
                for f in proj.iterdir():
                    if not f.is_file() or f.suffix != ".jsonl":
                        continue
                    try:
                        mtime = f.stat().st_mtime
                    except OSError:
                        continue
                    if newest is None or mtime > newest[1]:
                        newest = (f, mtime)
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                continue

        if newest is None:
            return None

        f = newest[0]
        m = _UUID_RE.search(f.name)
        if m:
            return m.group(0)
        # Fallback: strip extension
        base = f.stem
        if 0 < len(base) < 128:
            return base
        return None
