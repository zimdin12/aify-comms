"""Abstract runtime adapter — the service's per-runtime facts.

Every supported runtime (claude-code, codex, hermes, pi, opencode) ships a
subclass that fills in the following class attributes:

    name: str
    session_env_vars: list[str]
    supports_resident: bool
    supports_managed: bool
    supports_steering: bool
    supports_interrupt: bool
    preferred_delivery_mode: str  # "resident" | "managed" | "managed-via-wrapper"

and overrides `console_argv` and `resume_command`. That is the whole surface the
service reads. Session discovery, handle normalisation and delivery are the
bridge's job and live in the JS adapters under mcp/stdio/adapters/.
"""

from __future__ import annotations

from typing import Any


class RuntimeAdapter:
    # Class attributes set by subclasses. Accessing on the base class raises
    # AttributeError, which surfaces missing overrides loudly in tests.

    def console_argv(self, **opts: Any) -> list[str]:
        """The console launch as ARGV - the program, then its arguments.

        v0.6 Phase 8 needs the structural form because aify-env executes an allowlisted launcher file
        rather than a shell string. The one string form the service shows is derived from this list
        (`_default_console_command` in api_core/capabilities.py), so the two cannot drift.
        """
        raise NotImplementedError("subclass must override console_argv")

    # ─────────────────── OPERATOR TAKEOVER (governance) ───────────────────

    def resume_command(self, session_id: Any, agent_id: Any = "") -> str:
        """Operator takeover command for `session_id` — Python mirror of the
        JS adapter `resumeCommand(sessionId)`. Surfaced by the dashboard when an
        agent is resident or in a `session-changed` state so the operator can
        copy the exact command to attach to the SAME session. Subclasses
        override with their wrapper form (e.g. `claude-aify --resume <id>`).

        MUST carry `--aify-agent <agent_id>` when known (2026-07-14). Every
        turn-state path in a wrapper-launched session is gated on AIFY_AGENT_ID,
        which the wrapper only exports when the agent id is passed. A resume
        command WITHOUT it hands the operator a session that registers, messages
        and heartbeats normally but whose status silently latches forever — the
        general-manager "always working" incident. The command we hand out must
        never be the one that breaks the agent.
        """
        raise NotImplementedError(
            f"abstract: {getattr(self, 'name', type(self).__name__)} adapter "
            "must override resume_command(session_id)"
        )

    # Default readiness. Subclasses with extra per-config gates (claude
    # channelEnabled, hermes gatewayUrl) override.
    def is_resident_ready(self, runtime_config: dict) -> bool:
        return self.supports_resident
