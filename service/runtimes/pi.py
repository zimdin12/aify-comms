"""PiAdapter — Python mirror of mcp/stdio/adapters/pi.js.

Capability declarations encode the Pi delivery model: resident is False
because omp --mode rpc is single-client stdio (no multi-client gateway).
Managed dispatch stays on the native persistent RPC controller so dashboard
chat and Console attach to the same synthesized terminal stream.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .base import RuntimeAdapter

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


class PiAdapter(RuntimeAdapter):
    name = "pi"
    display_name = "Pi"
    session_env_vars = ["PI_SESSION_ID", "OMP_SESSION_ID", "AIFY_PI_SESSION_ID"]
    supports_resident = False
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = False
    preferred_delivery_mode = "managed"

    # Plan 3 additions
    wrapper_name = "pi-aify"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        if interactive:
            return f"pi-aify --aify-agent {agent_id}"
        parts = ["pi-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    # Plan 4 (2026-05-25): pi storage at ~/.omp/agent/sessions/<project-key>/
    # <timestamp>_<uuid>.jsonl. The session id is the UUID embedded in the
    # filename; the first JSON line of the file also carries an `id` field as
    # a fallback. We scan one level deep — flat files at the root are also
    # tolerated for forward compatibility.
    async def discover_session_id(self) -> str | None:
        sessions_dir = Path.home() / ".omp" / "agent" / "sessions"
        candidates = self._collect_candidates(sessions_dir)
        if candidates is None:
            return None
        if not candidates:
            return None
        newest = max(candidates, key=lambda c: c[1])  # (path, mtime)
        newest_path: Path = newest[0]
        m = _UUID_RE.search(newest_path.name)
        if m:
            return m.group(0)
        try:
            first_line = newest_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if first_line:
                obj = json.loads(first_line[0])
                for key in ("id", "session_id", "sessionId"):
                    val = obj.get(key)
                    if isinstance(val, str) and val:
                        return val
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _collect_candidates(self, root_dir: Path) -> list[tuple[Path, float]] | None:
        """Return [(path, mtime)] for session files one level deep or at the
        root. None means the root dir is missing/unreadable.
        """
        try:
            top_entries = list(root_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return None
        out: list[tuple[Path, float]] = []
        for ent in top_entries:
            try:
                if ent.is_file():
                    self._push_if_session(out, ent)
                elif ent.is_dir():
                    try:
                        for sub in ent.iterdir():
                            if sub.is_file():
                                self._push_if_session(out, sub)
                    except (PermissionError, OSError):
                        continue
            except OSError:
                continue
        return out

    def _push_if_session(self, out: list[tuple[Path, float]], path: Path) -> None:
        # Only consider files that look like session payloads (jsonl/json)
        # and carry a uuid in their filename.
        if path.suffix.lower() not in (".jsonl", ".json"):
            return
        if not _UUID_RE.search(path.name):
            return
        try:
            out.append((path, path.stat().st_mtime))
        except OSError:
            pass
