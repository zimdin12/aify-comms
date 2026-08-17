"""HermesAdapter — Python mirror of mcp/stdio/adapters/hermes.js."""

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


class HermesAdapter(RuntimeAdapter):
    name = "hermes"
    display_name = "Hermes"
    session_env_vars = ["HERMES_SESSION_ID", "HERMES_SESSION"]
    supports_resident = True
    supports_managed = True
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "hermes-aify"

    def resume_command(self, session_id, agent_id="") -> str:
        # The aify-aware way to reopen an agent is the wrapper: hermes-aify's
        # --resume recovery maps the real session id back to its agent and
        # resumes that real session via the gateway-host.
        aid = str(agent_id or "").strip()
        if aid:
            return f"hermes-aify --aify-agent {aid} --resume {session_id}"
        return f"hermes-aify --resume {session_id}"

    def console_command(self, *, agent_id: str, handle: str, interactive: bool) -> str:
        parts = ["hermes-aify", "--aify-agent", agent_id]
        if handle:
            parts.extend(["--resume", handle])
        return " ".join(parts)

    def is_resident_ready(self, runtime_config: dict) -> bool:
        if not runtime_config:
            return False
        gw = str(runtime_config.get("gatewayUrl", "")).strip()
        return bool(re.match(r"^wss?://", gw, re.IGNORECASE))

    def diagnostic_env(self) -> dict[str, str]:
        env = super().diagnostic_env()
        val = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        env["AIFY_HERMES_GATEWAY_URL"] = val if val else "(unset)"
        return env

    async def discover_session_id(self) -> str | None:
        """Mirror the JS Hermes adapter ordering.

        Prefer the active-session file written by the visible TUI, then the
        durable env handle. If a gateway is present but neither exists, return
        None rather than querying historical gateway state and binding to a
        hidden/non-visible session.
        """
        active = self._read_active_session_file()
        if active:
            return active
        env_session = self.get_current_session_id()
        if env_session:
            return env_session
        gw = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        if gw and re.match(r"^wss?://", gw, re.IGNORECASE):
            return None
        return self._scan_hermes_sessions_dir()

    def _read_active_session_file(self) -> str | None:
        """The live visible session, as written by the TUI / the hermes-aify wrapper.

        MIRRORS `mcp/stdio/adapters/hermes.js::_readActiveSessionFile`, and two divergences from it
        were fixed here on 2026-08-17:

        * it read only `AIFY_HERMES_ACTIVE_SESSION_FILE`. The JS reads
          `HERMES_TUI_ACTIVE_SESSION_FILE` as well — that is the variable hermes' own TUI exports,
          and the debug skill names both. A host that set only the TUI one had a live active-session
          file that this side could not see, and discovery fell through to the env handle.
        * a JSON OBJECT with no recognized id key fell through to `return raw`, handing back the
          whole JSON document as a session handle. The JS returns "" for exactly that case with the
          comment "JSON object with no recognized id key". A shape we do not understand is not a
          session id, and registering one produces a `--resume {"…"}` that cannot resolve.

        The non-object fallback stays: a bare hermes timestamp id like `20260603120000` IS valid
        JSON (a number), so anything that is not an object must return the raw text unchanged.
        """
        file = (
            os.environ.get("AIFY_HERMES_ACTIVE_SESSION_FILE", "").strip()
            or os.environ.get("HERMES_TUI_ACTIVE_SESSION_FILE", "").strip()
        )
        if not file:
            return None
        try:
            raw = Path(file).read_text(encoding="utf-8", errors="replace").strip()
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None
        if not raw:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                for key in ("session_id", "sessionId", "id"):
                    val = parsed.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()
                return None
        except json.JSONDecodeError:
            pass
        return raw

    # REMOVED 2026-08-17: `_query_gateway_most_recent`, a 38-line WebSocket client that asked the
    # gateway for `session.most_recent`. Nothing called it, and nothing may: `discover_session_id`
    # above returns None instead ("rather than querying historical gateway state and binding to a
    # hidden/non-visible session"), both debug skills say in as many words not to use
    # `session.most_recent` as the current visible session because it can be historical DB state,
    # and the JS original it mirrors has no counterpart — the tui_gateway path it spoke to was
    # retired in `11ba0cd`.
    #
    # It was deleted rather than tested. An untested implementation of the thing the docs forbid is
    # worse than no implementation: the next person extending discovery finds a ready-made helper
    # for it and a comment two methods up saying don't.

    def _scan_hermes_sessions_dir(self) -> str | None:
        sessions_dir = Path.home() / ".hermes" / "sessions"
        try:
            entries = list(sessions_dir.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return None
        if not entries:
            return None
        try:
            files = [p for p in entries if p.is_file()]
            if not files:
                return None
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        newest = files[0]

        m = _UUID_RE.search(newest.name)
        if m:
            return m.group(0)

        base = re.sub(r"\.jsonl?$", "", newest.name)
        if 0 < len(base) < 128:
            return base

        try:
            lines = newest.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if lines:
                obj = json.loads(lines[0])
                for key in ("session_id", "sessionId", "id"):
                    val = obj.get(key)
                    if isinstance(val, str) and val:
                        return val
        except (json.JSONDecodeError, OSError):
            pass
        return None
