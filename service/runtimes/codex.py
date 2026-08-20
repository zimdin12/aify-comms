"""CodexAdapter — Python mirror of mcp/stdio/adapters/codex.js.

Adds AIFY_CODEX_APP_SERVER_URL to diagnostic_env() for parity with the JS side.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .base import RuntimeAdapter

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_MAX_WALK_DEPTH = 4


class CodexAdapter(RuntimeAdapter):
    name = "codex"
    display_name = "Codex"
    session_env_vars = ["CODEX_THREAD_ID"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "codex-aify"

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

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_CODEX_APP_SERVER_URL", "").strip()
        env["AIFY_CODEX_APP_SERVER_URL"] = val if val else "(unset)"
        return env

    async def discover_session_id(self) -> str | None:
        """Plan 4 (2026-05-25): codex storage at ~/.codex/sessions/. Recon found
        the actual layout is date-sharded — YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl
        — plus a sibling quarantine-oversized/ flat-file dir. We walk up to 4
        levels deep, find newest .jsonl by mtime, extract uuid from filename,
        and fall back to first-line JSON metadata for forward compatibility.
        """
        root = Path.home() / ".codex" / "sessions"
        files: list[Path] = []
        try:
            self._walk_codex_sessions(root, files, depth=0)
        except (OSError, RecursionError):
            return None
        if not files:
            return None
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        newest = files[0]

        m = _UUID_RE.search(newest.name)
        if m:
            return m.group(0)
        try:
            lines = newest.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if lines:
                obj = json.loads(lines[0])
                for key in ("id", "session_id", "sessionId", "thread_id", "threadId"):
                    val = obj.get(key)
                    if isinstance(val, str) and val:
                        return val
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _walk_codex_sessions(
        self, p: Path, out: list[Path], depth: int
    ) -> None:
        if depth > _MAX_WALK_DEPTH:
            return
        try:
            entries = list(p.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return
        for ent in entries:
            try:
                if ent.is_dir():
                    self._walk_codex_sessions(ent, out, depth + 1)
                elif ent.is_file() and ent.suffix.lower() in (".jsonl", ".json"):
                    out.append(ent)
            except OSError:
                pass
