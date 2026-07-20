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
_HERMES_GATEWAY_TIMEOUT_S = 3.0


class HermesAdapter(RuntimeAdapter):
    name = "hermes"
    display_name = "Hermes"
    session_env_vars = ["HERMES_SESSION_ID", "HERMES_SESSION"]
    supports_resident = True
    supports_managed = True
    # ASYMMETRY(hermes): managed submissions made while the model is active interrupt the current
    # turn. Queue until turn-end instead of advertising safe mid-turn steering. Interrupt remains
    # independently supported through the gateway.
    supports_steering = False
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
        file = os.environ.get("AIFY_HERMES_ACTIVE_SESSION_FILE", "").strip()
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
        except json.JSONDecodeError:
            pass
        return raw

    async def _query_gateway_most_recent(self, gateway_url: str) -> str | None:
        """Best-effort WebSocket query against hermes tui_gateway. If the
        websockets package isn't available we silently return None — the
        filesystem fallback still works.
        """
        try:
            import asyncio  # noqa: PLC0415

            import websockets  # noqa: PLC0415
        except ImportError:
            return None
        try:
            async with asyncio.timeout(_HERMES_GATEWAY_TIMEOUT_S):
                async with websockets.connect(gateway_url) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "session.most_recent",
                        "params": {},
                    }))
                    while True:
                        raw = await ws.recv()
                        try:
                            msg = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if msg.get("id") != 1:
                            continue
                        result = msg.get("result")
                        if isinstance(result, str):
                            return result
                        if isinstance(result, dict):
                            for key in ("session_id", "sessionId", "id"):
                                val = result.get(key)
                                if isinstance(val, str) and val:
                                    return val
                        return None
        except (TimeoutError, OSError, websockets.exceptions.WebSocketException):
            return None
        return None

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
