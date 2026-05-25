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
    supports_steering = True
    supports_interrupt = True
    supports_multi_client = True
    preferred_delivery_mode = "managed-via-wrapper"

    # Plan 3 additions
    wrapper_name = "hermes-aify"

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
        """Plan 4 (2026-05-25): hermes session discovery — try the gateway's
        session.most_recent JSON-RPC method first (when AIFY_HERMES_GATEWAY_URL
        is set + ws:/wss:), then fall back to a filesystem scan of
        ~/.hermes/sessions/ for the newest file by mtime.
        """
        gw = os.environ.get("AIFY_HERMES_GATEWAY_URL", "").strip()
        if gw and re.match(r"^wss?://", gw, re.IGNORECASE):
            try:
                id_ = await self._query_gateway_most_recent(gw)
                if id_:
                    return id_
            except Exception:
                # Best-effort — fall through to fs scan
                pass
        return self._scan_hermes_sessions_dir()

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
