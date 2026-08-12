"""Runtime capability and environment-support predicates. PURE — no database, no router.

The first slice of the v0.5.4 layer decomposition. `control_plane.py`'s call graph turned out to be
a clean 8-layer DAG, and these eight sit in layer 0: they call nothing else in the control plane and
touch no DB, so they are unit-testable in isolation — the pattern `terminal_diagnostics.py` set and
CLAUDE.md asks new work to follow.

WHY THIS GROUP FIRST. `_default_capabilities_for` is read by 10 non-test modules and
`_managed_via_wrapper_for_runtime` by 13, every one of them reaching through the control plane.
Moving them to a leaf lets those modules import the owner directly, so the borrow shims are RETIRED
rather than relocated — the first work in this series that reduces the shim count instead of adding
to it.

Everything here depends only on other leaves (`api_core.settings`, `api_core.runtime`, `env_status`,
`clock`, `runtimes`), so this module cannot participate in a cycle with the control plane.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from service.api_core.serialization import _json_loads_or
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.settings import DEFAULT_SETTINGS
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status



def _managed_via_wrapper_for_runtime(settings: dict[str, Any], runtime: str) -> bool:
    """True when managed dispatches for this runtime should route through a
    *-aify wrapper PTY (the wrapper's child bridge claims and delivers) instead
    of the bridge's native RPC adapter. Unified-backing refactor 2026-05-24,
    extended in Plan 2 (2026-05-25) to consult the runtime adapter.

    claude-code is excluded — it's already wrapper-backed via claude-channel.js
    inside claude-aify regardless of this flag.

    For all other runtimes, eligibility is driven by the adapter's
    preferred_delivery_mode == "managed-via-wrapper". Pi is explicitly
    excluded because OMP is single-client RPC; dashboard chat and Console must
    share the same native managed controller and virtual terminal stream.
    """
    from service.runtimes import adapter_for

    val = settings.get("managed_via_wrapper", DEFAULT_SETTINGS.get("managed_via_wrapper", False))
    runtime_n = _normalize_runtime(runtime or "")
    if runtime_n == "claude-code":
        return False
    if runtime_n == "pi":
        return False
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return False
    if adapter.preferred_delivery_mode != "managed-via-wrapper":
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, list):
        return runtime_n in {str(item).strip().lower() for item in val if item}
    return False


def _has_live_rpc_controller(agent_id: str) -> bool:
    """Plan 4: True when an in-memory RPC controller is registered for this
    agent (managed-RPC synth fallback path). Today aify-comms doesn't
    maintain such a registry on the server side — the bridge owns RPC
    lifecycle. Returns False by default; wrapper-PTY backed agents go
    through _has_live_terminal_session above.

    Future: if a server-side registry of bridge-owned RPC children is
    introduced, query it here.
    """
    return False


def _has_hermes_gateway_url(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    """Plan 4 Task 17: hermes resident uses the gateway path when a live
    gatewayUrl is present in runtime_config. Mirrors the bridge-side check
    in mcp/stdio/server.js."""
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("gatewayUrl") or "").strip().lower().startswith(("ws://", "wss://"))


def _default_capabilities_for(
    runtime: str,
    session_mode: str,
    session_handle: str = "",
    runtime_config: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Build the default capability list for an agent registration.

    Plan 3 (2026-05-25): resident gating routes through adapter.is_resident_ready()
    which closes the #120 regression — claude resident needs channelEnabled,
    hermes resident needs a valid gatewayUrl, both rolled into the adapter.
    """
    from service.runtimes import adapter_for

    runtime_n = _normalize_runtime(runtime or "")
    try:
        adapter = adapter_for(runtime_n)
    except ValueError:
        return []

    caps: list[str] = []
    session_mode_n = _normalize_session_mode(session_mode or "")

    if session_mode_n == "resident":
        # Plan 3: adapter.is_resident_ready() encapsulates per-runtime,
        # per-config gating (channelEnabled for claude, gatewayUrl for hermes).
        if adapter.supports_resident and adapter.is_resident_ready(runtime_config or {}):
            caps.append("resident-run")
    else:
        if adapter.supports_managed:
            caps.append("managed-run")

    if adapter.supports_resident or adapter.supports_managed:
        caps.append("resume")
    if adapter.supports_interrupt:
        caps.append("interrupt")
    supports_steering = adapter.supports_steering
    # ASYMMETRY(hermes): wrapper/gateway delivery supports native steer. The
    # advanced single-client ACP fallback rejects concurrent session/prompt.
    if runtime_n == "hermes" and session_mode_n == "managed" and not bool((runtime_config or {}).get("channelEnabled")):
        supports_steering = False
    if supports_steering:
        caps.append("steer")

    # `spawn` capability is independent — every aify-comms managed-capable
    # runtime supports being spawned by another agent's environment.
    if session_mode_n != "resident" and adapter.supports_managed:
        caps.append("spawn")

    return caps


def _managed_env_reachable(agent_row, env_row, settings) -> bool:
    """Phase I flip parity: whether a MANAGED agent's owning environment is reachable
    (the engine's `available`/`online` gate). A RESOLVED env gates on its effective
    status; an UNRESOLVABLE env (None — unbound agent) is reachable ONLY while the agent
    is still heartbeating within the offline window, so a freshly-registered unbound
    agent is `available` (env resolves at claim time) while an ancient one is `offline`
    (matches the legacy last_seen offline threshold; without the heartbeat term an unbound
    dead agent would wrongly read `available`)."""
    if env_row is not None:
        return _environment_effective_status(
            env_row, offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90))
        ) in {"online", "degraded"}
    last_seen_epoch = _iso_to_epoch(str(agent_row["last_seen"] or ""))
    if not last_seen_epoch:
        return False
    offline_secs = max(60, int(settings.get("agent_liveness_seconds", 90) or 90))
    return (datetime.now(timezone.utc).timestamp() - last_seen_epoch) <= offline_secs


def _environment_supports_terminal(environment: dict[str, Any], runtime: str) -> bool:
    if not bool(environment.get("terminal")) or not bool(environment.get("pty")):
        return False
    allowed = [
        _normalize_runtime(str(item or ""))
        for item in (environment.get("terminalRuntimes") or [])
        if str(item or "").strip()
    ]
    if allowed and _normalize_runtime(runtime) not in allowed:
        return False
    return True


def _environment_uses_windows_paths(environment: dict[str, Any]) -> bool:
    text = " ".join(
        str(environment.get(key) or "")
        for key in ("id", "os", "kind", "machineId")
    ).lower()
    if "win32" in text or "windows" in text:
        return True
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    return any(re.match(r"^[A-Za-z]:[\\/]", root) for root in roots)


def _default_console_command(session, workspace: str, *, interactive: bool = False) -> str:
    """Build the dashboard Console launch command for an agent session.

    Plan 3 (2026-05-25): per-runtime tail collapses to
    `adapter.console_command(...)`. The adapter owns the per-runtime quirks
    (claude interactive stays fresh, codex always resumes, pi interactive
    avoids the 026H trap, opencode is plain CLI).
    """
    from service.runtimes import adapter_for

    agent_id = str(session["agent_id"] or "").strip()
    handle = str(session["session_handle"] or "").strip()
    runtime = _normalize_runtime(session["runtime"] or "")

    try:
        adapter = adapter_for(runtime)
    except ValueError:
        return f"{runtime or 'agent'} --aify-agent {agent_id}"

    return adapter.console_command(
        agent_id=agent_id,
        handle=handle,
        interactive=interactive,
    )


# v0.5.4: `_row_capabilities` and `_has_codex_live_app_server` arrived here rather than following
# `_agent_execution_mode` into its own leaf. They came out of the same closure, but `_row_capabilities`
# has SIX other carrier readers (wake mode, fix hint, the agent record, the status cache, dispatch
# creation, send preflight) — it is a general accessor for what an agent row can do, not a detail of
# how one dispatch will execute. Following its largest consumer would have made five unrelated modules
# import a module named after somebody else's decision.

def _has_codex_live_app_server(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("appServerUrl") or "").strip().lower().startswith(("ws://", "wss://"))


def _row_capabilities(row) -> list[str]:
    if not row:
        return []
    capabilities = _json_loads_or(row["capabilities"], [])
    runtime = _normalize_runtime((row["runtime"] if "runtime" in row.keys() else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if "session_mode" in row.keys() else "") or "resident")
    session_handle = str((row["session_handle"] if "session_handle" in row.keys() else "") or "").strip()
    runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
    if runtime == "pi":
        if session_mode == "resident":
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
        if session_mode == "managed":
            for cap in ("managed-run", "resume", "interrupt", "steer", "spawn"):
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
    if runtime == "opencode" and session_mode == "resident":
        return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
    if runtime == "hermes":
        if session_mode == "managed":
            managed_caps = ["managed-run", "resume", "interrupt", "spawn"]
            if bool(runtime_config.get("channelEnabled")):
                managed_caps.append("steer")
            else:
                capabilities = [cap for cap in capabilities if cap != "steer"]
            for cap in managed_caps:
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
        elif _has_hermes_gateway_url(runtime_config):
            for cap in ("resident-run", "resume", "interrupt", "steer"):
                if cap not in capabilities:
                    capabilities = [*capabilities, cap]
        else:
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
    if runtime == "claude-code" and session_mode == "resident":
        channel_enabled = isinstance(runtime_config, dict) and runtime_config.get("channelEnabled") is True
        if not channel_enabled:
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
        for cap in ("resident-run", "interrupt", "steer"):
            if cap not in capabilities:
                capabilities = [*capabilities, cap]
    return capabilities
