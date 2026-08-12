"""How a dispatch to this agent will actually be EXECUTED — channel, wrapper, native or nothing.

One function, 111 lines, and it is alone on purpose. Its closure also contained `_row_capabilities`
and `_has_codex_live_app_server`, both of which went to `api_core/capabilities.py` instead: they answer
"what can this agent row do", which five other carrier functions also ask, while this module answers
"given that, how does work reach it", which only the dispatch path asks. A closure tells you what a
function NEEDS; it does not tell you what belongs in one module with it.

The execution mode is derived, never stored, and that is the point. It reads the runtime, the session
mode, the live capabilities and the channel runtime sets, and returns the mode that is actually
available right now — so a runtime whose channel flag is off, or whose gateway URL is missing, cannot
be selected for a path that would then strand the dispatch. The four channel runtime sets it consults
live in `api_core/channel_delivery.py`, together, because route and claim have drifted apart before.

DB ACCESS: none. It is handed a row.
"""

from __future__ import annotations

from typing import Any, Optional

from service.api_core.capabilities import (
    _has_codex_live_app_server,
    _has_hermes_gateway_url,
    _managed_via_wrapper_for_runtime,
    _row_capabilities,
)
from service.api_core.channel_delivery import (
    _CHANNEL_FLAG_GATED_RUNTIMES,
    _CHANNEL_MANAGED_RUNTIMES,
    _channel_managed_eligible,
)
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES as _LAUNCHABLE_RUNTIMES


def _agent_execution_mode(row, requested_runtime: Optional[str] = None, settings: Optional[dict[str, Any]] = None) -> tuple[Optional[str], Optional[str]]:
    runtime = _normalize_runtime(row["runtime"] or "generic")
    session_mode = _normalize_session_mode(row["session_mode"] or "resident")
    session_handle = str(row["session_handle"] or "").strip()
    if requested_runtime and _normalize_runtime(requested_runtime) != runtime:
        return None, f'requested runtime "{requested_runtime}" does not match registered runtime "{runtime}"'
    if runtime not in _LAUNCHABLE_RUNTIMES:
        return None, f'runtime "{runtime}" does not support active dispatch'
    capabilities = _row_capabilities(row)
    if session_mode == "managed":
        if (row["launch_mode"] or "detached") == "none":
            return None, "launch mode is disabled"
        # Unified-backing refactor 2026-05-24: when this runtime is
        # wrapper-backed (managed_via_wrapper includes it), route managed
        # dispatches as execution_mode='channel'. The wrapper's child bridge
        # (loaded as MCP inside *-aify, running with sessionMode=resident)
        # claims via its resident-run capability and executionModes=['channel',
        # 'resident'] — same shape as channel-route managed claude. The main
        # bridge no longer claims 'managed' for wrapper-backed runtimes
        # (mcp/stdio/dispatch-execution.js supportedExecutionModes gate).
        if settings is not None and _managed_via_wrapper_for_runtime(settings, runtime):
            return "channel", None
        # Managed claude with channelEnabled=true uses the channel
        # transport, not the headless managed-run API (claude doesn't
        # have a true headless managed-run). The wrapper-PTY-hosted
        # claude-channel.js delivers via channel notifications. Skip
        # the managed-run cap check for that path; the dispatch flows
        # through execution_mode='channel' below.
        runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        # Sidecar-channel managed delivery (claude unconditional; hermes
        # gated on the wrapper-set channelEnabled flag). The in-session
        # sidecar claims the channel run and delivers the wake; the agent
        # self-replies via comms_send. The channel path needs no captured
        # session_handle — the sidecar drives the agent's own session — so
        # this returns before any handle requirement (Task 1.5: hermes
        # delivery no longer needs session_handle).
        _channel_eligible = _channel_managed_eligible(runtime, runtime_config)
        if capabilities and "managed-run" not in capabilities and not _channel_eligible:
            return None, 'agent capabilities do not include "managed-run"'
        # claude: unconditional channel (no native managed-run). hermes: channel
        # only when the flag is set; otherwise it falls through to its native
        # 'managed' route. ASYMMETRY(hermes): documented at the set definitions.
        if runtime in _CHANNEL_MANAGED_RUNTIMES:
            return "channel", None
        if runtime in _CHANNEL_FLAG_GATED_RUNTIMES and _channel_eligible:
            return "channel", None
        return "managed", None
    if runtime == "pi":
        return None, (
            f'agent "{row["id"]}" is a Pi/OMP presence session, not a triggerable resident target. '
            "Switch to managed or spawn a managed Pi agent so delivery uses the bridge-owned OMP RPC worker."
        )
    if runtime == "opencode":
        return None, (
            f'agent "{row["id"]}" is an OpenCode presence session, not a triggerable resident target. '
            "Create an environment-managed OpenCode agent; resident OpenCode delivery is disabled until a real multi-client surface is wired."
        )
    if "resident-run" not in capabilities:
        # Actionable diagnosis: identify the most likely missing wake-config
        # for this runtime so the operator can fix the registration without
        # spelunking docs. Mirror of mcp/stdio/runtimes.js:defaultCapabilities-
        # ForRuntime gating: bridge returns [] for resident agents missing
        # their runtime-specific wake handle (sessionHandle for codex/pi/
        # opencode; gatewayUrl for hermes; channelEnabled for claude).
        runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
        if runtime == "claude-code" and not runtime_config.get("channelEnabled"):
            return None, (
                f'agent "{row["id"]}" is a resident Claude session without channelEnabled. '
                "Restart with `claude-aify` (which sets AIFY_CHANNELS_ENABLED=1) and re-register from that session."
            )
        if runtime == "codex" and not _has_codex_live_app_server(runtime_config):
            return None, (
                f'agent "{row["id"]}" is a resident Codex session without a live appServerUrl. '
                "Restart with `codex-aify` and re-register passing `appServerUrl=\"$AIFY_CODEX_APP_SERVER_URL\"` and `sessionHandle=\"$CODEX_THREAD_ID\"`."
            )
        if runtime == "hermes":
            gateway_url = str(runtime_config.get("gatewayUrl") or "").strip()
            if not (gateway_url.startswith("ws://") or gateway_url.startswith("wss://")):
                return None, (
                    f'agent "{row["id"]}" is a resident Hermes session without a live gatewayUrl. '
                    "Restart with the updated `hermes-aify` (which exports AIFY_HERMES_GATEWAY_URL) and re-register — the bridge auto-detects the gateway from env. "
                    "Verify the wrapper is current with `head -30 ~/.local/bin/hermes-aify | grep pick_port` (function exists in the new wrapper)."
                )
        return None, 'agent capabilities do not include "resident-run" — re-register from a live aify-wrapper session with the runtime\'s wake handle.'
    if runtime == "codex" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident Codex session without a bound session handle. '
            "Re-register that live session or provide sessionHandle explicitly."
        )
    if runtime == "hermes" and not session_handle:
        # Hermes-with-gatewayUrl doesn't need a captured sessionHandle —
        # the bridge's gateway-channel controller resolves
        # session.most_recent at dispatch time. Mirror of the carve-out
        # in defaultCapabilitiesForRuntime (mcp/stdio/runtimes.js).
        # Operator-reported 2026-05-24: sc-hermes-test-1 registered with
        # gatewayUrl but no sessionHandle, capability check passed (resident-run
        # was granted) but this gate still rejected live delivery. Without this
        # carve-out the new gateway path can never deliver since hermes-aify
        # registers before any chat session exists.
        _rc = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
        _rc = _rc if isinstance(_rc, dict) else {}
        _gw = str(_rc.get("gatewayUrl") or "").strip()
        if not (_gw.startswith("ws://") or _gw.startswith("wss://")):
            return None, (
                f'agent "{row["id"]}" is a resident Hermes session without a bound session handle. '
                "Restart with hermes-aify and a resumable session handle, or create an environment-managed session."
            )
    if (row["launch_mode"] or "detached") == "none":
        return None, "launch mode is disabled"
    return "resident", None


async def _auto_return_resident_to_managed_if_possible(
    db,
    row,
    *,
    settings: dict[str, Any],
    force: bool = False,
    reason: str = "resident_lease_expired",
):
    # Manual ownership model: resident<->managed changes happen only through
    # PATCH /agents/{id}/session-mode. Keep the helper as a compatibility
    # no-op for older call sites while the automatic paths are removed.
    return row, ""
