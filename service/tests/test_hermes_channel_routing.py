"""Task 1.5 (2026-05-30, Runtime Symmetry & Session Governance) —
managed hermes routes to execution_mode='channel' via the per-agent
hermes-channel.js sidecar, gated on a channel-enabled runtime flag set by
the wrapper env (mirror of claude's channelEnabled / AIFY_CHANNELS_ENABLED=1),
and hermes delivery no longer requires a non-empty session_handle.

Symmetry target: managed hermes now matches managed claude — both resolve to
'channel' so an in-session sidecar claims and delivers the wake, and the
agent self-replies via comms_send. The asymmetry vs claude: claude routes to
channel UNCONDITIONALLY (no headless managed-run), while hermes is GATED on
the wrapper-set channelEnabled flag (without it, hermes stays on its prior
native/managed path) — see ASYMMETRY(hermes) comments in api_v2.py.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.control_plane import _agent_execution_mode


def _managed_hermes_row(*, channel_enabled: bool, session_handle: str = "", caps=None):
    runtime_config = {"channelEnabled": True} if channel_enabled else {}
    return {
        "id": "hermes-managed-1",
        "runtime": "hermes",
        "session_mode": "managed",
        "session_handle": session_handle,
        "launch_mode": "detached",
        "runtime_config": json.dumps(runtime_config),
        "capabilities": json.dumps(
            caps if caps is not None else ["resume", "interrupt"]
        ),
    }


def test_managed_hermes_with_channel_flag_routes_to_channel():
    """A managed hermes agent whose wrapper set the channel-enabled flag
    resolves to execution_mode='channel' (claimable by hermes-channel.js),
    the same way managed claude does. Gated purely on the flag — NOT on the
    managed_via_wrapper PTY toggle (settings omit hermes here)."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(channel_enabled=True),
        settings={"managed_via_wrapper": ["codex"]},
    )
    assert mode == "channel", f"expected channel; got ({mode!r}, {reason!r})"
    assert reason is None


def test_managed_hermes_channel_flag_routes_even_with_settings_none():
    """Gating is the runtime flag, independent of the settings object."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(channel_enabled=True),
        settings=None,
    )
    assert mode == "channel", f"expected channel; got ({mode!r}, {reason!r})"
    assert reason is None


def test_managed_hermes_channel_delivery_does_not_require_session_handle():
    """The empty session_handle must NOT reject/downgrade channel delivery —
    the sidecar drives the agent's pinned daemon session; no captured handle
    is needed (the old gateway-handle gate is gone for the channel path)."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(channel_enabled=True, session_handle=""),
        settings={"managed_via_wrapper": ["codex"]},
    )
    assert mode == "channel", f"empty handle must not block channel; got {reason!r}"
    assert reason is None


def test_managed_hermes_channel_flag_overrides_missing_managed_run_cap():
    """Like channel-managed claude, the channel path skips the managed-run
    capability requirement (the sidecar delivers, not the headless API)."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(channel_enabled=True, caps=["resume", "interrupt"]),
        settings=None,
    )
    assert mode == "channel", f"expected channel; got ({mode!r}, {reason!r})"
    assert reason is None


def test_managed_hermes_without_channel_flag_does_not_falsely_claim_channel():
    """WITHOUT the channel-enabled flag and WITHOUT managed_via_wrapper for
    hermes, a managed hermes agent must NOT falsely advertise channel
    deliverability. It stays on its prior path (here: rejected for lacking
    managed-run, exactly as before this change) — no silent channel claim."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(channel_enabled=False, caps=["resume", "interrupt"]),
        settings={"managed_via_wrapper": ["codex"]},
    )
    assert mode != "channel", (
        f"hermes without the channel flag must not claim channel; got ({mode!r}, {reason!r})"
    )


def test_managed_hermes_without_flag_but_managed_run_cap_stays_managed():
    """No channel flag + has managed-run cap + not wrapper-backed → native
    'managed' route, unchanged from pre-Task-1.5 behavior."""
    mode, reason = _agent_execution_mode(
        _managed_hermes_row(
            channel_enabled=False, caps=["managed-run", "resume", "interrupt"]
        ),
        settings={"managed_via_wrapper": ["codex"]},
    )
    assert mode == "managed", f"expected managed; got ({mode!r}, {reason!r})"
    assert reason is None


def test_hermes_is_in_channel_claim_runtimes():
    """The claim-side whitelist already accepts a hermes channel claim (the
    sidecar presents executionModes including 'channel')."""
    assert "hermes" in _CHANNEL_CLAIM_RUNTIMES


def test_managed_claude_channel_behavior_unchanged():
    """Guard: managed claude (channelEnabled set by claude-aify) still
    resolves to channel — its routing is untouched by the hermes change."""
    row = {
        "id": "claude-managed-1",
        "runtime": "claude-code",
        "session_mode": "managed",
        "session_handle": "",
        "launch_mode": "detached",
        "runtime_config": json.dumps({"channelEnabled": True}),
        "capabilities": json.dumps(["resume", "interrupt"]),
    }
    mode, reason = _agent_execution_mode(row, settings=None)
    assert mode == "channel", f"managed claude must stay channel; got ({mode!r}, {reason!r})"
    assert reason is None


def test_managed_claude_in_channel_managed_runtimes():
    """Guard: claude remains the unconditional channel-managed runtime."""
    from service.api_core.channel_delivery import _CHANNEL_MANAGED_RUNTIMES

    assert "claude-code" in _CHANNEL_MANAGED_RUNTIMES


def test_managed_codex_behavior_unchanged():
    """Guard: managed codex without the channel flag and not wrapper-backed
    keeps its native managed route (channel flag is a hermes/claude concern;
    codex routes via managed_via_wrapper, exercised elsewhere)."""
    row = {
        "id": "codex-managed-1",
        "runtime": "codex",
        "session_mode": "managed",
        "session_handle": "thread-x",
        "launch_mode": "detached",
        "runtime_config": "{}",
        "capabilities": json.dumps(["managed-run", "resume", "interrupt"]),
    }
    mode, reason = _agent_execution_mode(row, settings={"managed_via_wrapper": []})
    assert mode == "managed", f"codex native managed unchanged; got ({mode!r}, {reason!r})"
    assert reason is None


def test_managed_codex_without_channel_flag_not_channel_via_flag_path():
    """Guard: the new flag-gated channel path must NOT capture codex even if
    a stray channelEnabled flag is present (codex is not a sidecar-channel
    runtime; only claude + hermes are)."""
    row = {
        "id": "codex-managed-2",
        "runtime": "codex",
        "session_mode": "managed",
        "session_handle": "thread-y",
        "runtime_config": json.dumps({"channelEnabled": True}),
        "launch_mode": "detached",
        "capabilities": json.dumps(["managed-run", "resume", "interrupt"]),
    }
    # With managed_via_wrapper excluding codex, codex must NOT be routed to
    # channel purely by a channelEnabled flag (that gate is hermes-only).
    mode, reason = _agent_execution_mode(row, settings={"managed_via_wrapper": []})
    assert mode == "managed", f"codex flag must not trigger channel; got ({mode!r}, {reason!r})"
