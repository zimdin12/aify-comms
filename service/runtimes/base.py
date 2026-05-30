"""Abstract runtime adapter — Python mirror of mcp/stdio/adapters/base.js.

Every supported runtime (claude-code, codex, hermes, pi, opencode) ships a
subclass that fills in the following class attributes:

    name: str
    display_name: str  (defaults to name if unset)
    session_env_vars: list[str]
    supports_resident: bool
    supports_managed: bool
    supports_steering: bool
    supports_interrupt: bool
    supports_multi_client: bool
    preferred_delivery_mode: str  # "resident" | "managed" | "managed-via-wrapper"

The base supplies shared session-handle normalization, model-override
normalization, default diagnostic_env() implementation, and stubs for the
Plan 3 (console + delivery) methods so the contract surface is defined
upfront.
"""

from __future__ import annotations

import os
from typing import Any

HANDLE_PLACEHOLDERS = {"unknown", "default", "none", "null"}
MODEL_PLACEHOLDERS = {"unknown", "default", "auto"}


class RuntimeAdapter:
    # Class attributes set by subclasses. Accessing on the base class raises
    # AttributeError, which surfaces missing overrides loudly in tests.

    @property
    def display_name(self) -> str:
        # Default to `name` if subclass doesn't override.
        cls_attr = type(self).__dict__.get("display_name")
        if isinstance(cls_attr, str):
            return cls_attr
        return self.name

    # ─────────────────── SESSION LIFECYCLE (Plan 1) ───────────────────

    def get_current_session_id(self) -> str | None:
        for var in self.session_env_vars:
            raw = os.environ.get(var, "")
            normalized = self.normalize_session_handle(raw)
            if normalized:
                return normalized
        return None

    def normalize_session_handle(self, raw: Any) -> str:
        text = str(raw if raw is not None else "").strip()
        if not text:
            return ""
        if text.lower() in HANDLE_PLACEHOLDERS:
            return ""
        return text

    def resume_args(self, handle: Any) -> list[str]:
        h = self.normalize_session_handle(handle)
        return ["--resume", h] if h else []

    def normalize_model_override(self, raw: Any) -> str:
        text = str(raw if raw is not None else "").strip()
        if not text:
            return ""
        if text.lower() in MODEL_PLACEHOLDERS:
            return ""
        return text

    def diagnostic_env(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for var in self.session_env_vars:
            val = os.environ.get(var, "").strip()
            out[var] = val if val else "(unset)"
        return out

    async def discover_session_id(self) -> str | None:
        """Plan 4 (2026-05-25): runtime-native session discovery. Default
        returns None; subclasses override with filesystem/SQLite/RPC discovery.
        Called by bridge heartbeat as a fallback when env-read returns None.
        """
        return None

    # ─────────────────── CONSOLE / WRAPPER (Plan 3) ───────────────────

    @property
    def wrapper_name(self) -> str:
        raise NotImplementedError("Plan 3 — subclass must override wrapper_name")

    def console_command(self, **opts: Any) -> str:
        raise NotImplementedError("Plan 3 — subclass must override console_command")

    # ─────────────────── OPERATOR TAKEOVER (governance) ───────────────────

    def resume_command(self, session_id: Any) -> str:
        """Operator takeover command for `session_id` — Python mirror of the
        JS adapter `resumeCommand(sessionId)`. Surfaced by the dashboard when an
        agent is resident or in a `session-changed` state so the operator can
        copy the exact command to attach to the SAME session. Subclasses
        override with their wrapper form (e.g. `claude-aify --resume <id>`).
        """
        raise NotImplementedError(
            f"abstract: {getattr(self, 'name', type(self).__name__)} adapter "
            "must override resume_command(session_id)"
        )

    # Plan 3 — default implementation. Subclasses with extra per-config gates
    # (claude channelEnabled, hermes gatewayUrl) override.
    def is_resident_ready(self, runtime_config: dict) -> bool:
        return self.supports_resident

    # ─────────────────── DELIVERY (Plan 3 — stubbed) ───────────────────

    async def inject_message(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")

    async def interrupt(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")

    async def steer(self, **opts: Any) -> Any:
        raise NotImplementedError("Plan 3 — not yet implemented")
